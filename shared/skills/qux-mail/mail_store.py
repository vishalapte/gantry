"""The mail store: message identity, the account rule, the file name and month, ingest, resolve, and
the user-immutable lock that pins a stored message.

Ported from delphi's mail/lib/store.py, core/identity.py (the owner addresses only) and the
chflags helpers of mail/lib/_pin.py. ingest files on wicket's layout (ADR-MAIL, 2026-11-14):

    <store>/<account>/messages/<YYYY>/<MM>/<stem>.eml

The stem is a hash of the message (`message_stem`), so a tracked and a harvested copy of one
message land on the same path and are stored once; YYYY/MM is its `message_date`. There are no
folders. wicket computes both the same way, and no import joins the two repos, so
test_mail.py's parity test holds the two copies of the rule equal. resolve finds a message
through wicket's manifest (the row's `stem`), else by the Message-ID inside each file.

No path lives here: every function that touches the store takes it as `store`, and the owner
addresses come from the file the caller names (mail_paths.py is the paths' one home).
"""

import email
import email.parser
import email.policy
import email.utils
import hashlib
import json
import os
import stat
import sys
from datetime import datetime, timezone
from email.policy import default as default_policy
from functools import lru_cache
from pathlib import Path

from mail_eml import LEGACY_ARCHIVE, UNMIGRATED, header_values, read_head, store_dirs

# Account partition when no delivery recipient can be read off the headers.
UNKNOWN_ACCOUNT = "unknown"


class Refusal(Exception):
    """The run cannot proceed: a bad argument, missing config, a target that does not exist.
    mail.py prints it and exits 2."""


@lru_cache(maxsize=None)
def _owners(path):
    """The owner addresses in `path`, lowercased; empty when the file is absent or malformed."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return frozenset()
    raw = data.get("owner_addresses", []) if isinstance(data, dict) else []
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(a).strip().lower() for a in raw if str(a).strip())


def owner_addresses(identity):
    """The mailbox owner's own addresses, from the owner-address file at `identity`:
    {"owner_addresses": ["someone@example.com"]}. Matched by exact address, never by domain:
    a domain the owner uses carries other people's mail too. A file that is absent or
    malformed reads as no addresses."""
    return _owners(identity)


def normalize_message_id(raw):
    """The bare RFC Message-ID (<> stripped, lowercased), or None when absent or empty."""
    if not raw:
        return None
    _, addr = email.utils.parseaddr(raw)
    candidate = (addr or raw).strip().strip("<>").strip().lower()
    return candidate or None


# The file's name and month: the same rule as wicket.message (message_stem, message_date). ----


def _header_block(raw):
    """A message's headers, raw values (compat32), the body not parsed."""
    return email.parser.BytesHeaderParser(policy=email.policy.compat32).parsebytes(raw)


def _message_id_of(headers):
    """The Message-ID header exactly as written, unfolded, re-decoded as UTF-8; '' when absent.

    compat32, not policy.default, whose msg-id parser cuts an id short at a ':' or ','.
    """
    raw = next((str(v) for k, v in headers.raw_items() if k.lower() == "message-id"), "")
    unfolded = raw.replace("\r\n", "").replace("\n", "")
    return unfolded.encode("ascii", "surrogateescape").decode("utf-8", "replace")


def _parse_date(value):
    """A date header value as an aware UTC datetime, or None when absent or unreadable."""
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):  # OverflowError: a year past 9999 in UTC
        return None


def message_stem(raw):
    """The 16-hex name of a message's one file: sha1(message-id, Date in UTC, From)[:16].

    The normalized Message-ID, the Date header as YYYY-MM-DDTHH:MM:SSZ in UTC, and the From
    addr-spec in lower case, joined by newlines; a missing header, or a Date that cannot be
    read, contributes ''. `raw` may be a whole message or its header block.
    """
    headers = _header_block(raw)
    message_id = normalize_message_id(_message_id_of(headers)) or ""
    sent = _parse_date(headers.get("Date"))
    date = sent.strftime("%Y-%m-%dT%H:%M:%SZ") if sent else ""
    _, sender = email.utils.parseaddr(str(headers.get("From", "") or ""))
    text = f"{message_id}\n{date}\n{sender.strip().lower()}"
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def message_date(raw, fallback):
    """MESSAGE_DATE, the month a message is filed under, as UTC: the Date header (RFC 5322 is
    canon); only when it is missing or unreadable, the topmost Received header's date, else
    `fallback` (an aware datetime)."""
    headers = _header_block(raw)
    when = _parse_date(headers.get("Date"))
    if when is None:
        for value in headers.get_all("Received", [])[:1]:
            when = _parse_date(str(value).rpartition(";")[2].strip())
    return (when or fallback).astimezone(timezone.utc)


def _addresses(headers):
    return [addr.lower() for _, addr in email.utils.getaddresses(headers) if addr]


def _is_owner(addr, owners):
    return addr.strip().lower() in owners


def _canonical_account(addr):
    """Lowercase the address and drop a `+tag`, so tagged deliveries share one account."""
    addr = addr.strip().lower()
    local, _, domain = addr.partition("@")
    local = local.split("+", 1)[0]
    return f"{local}@{domain}" if domain else (local or UNKNOWN_ACCOUNT)


def account_of(msg, owners):
    """The owner mailbox a message belongs to: the top-level filing partition.

    Outbound (From is the owner): the owner's From address. Inbound: Delivered-To, then
    X-Original-To, then the first owner address among To/Cc, else the first recipient.
    UNKNOWN_ACCOUNT when no address can be read. Lowercased, `+tag` stripped.
    """
    from_addrs = _addresses(msg.get_all("From", []))
    sender = from_addrs[0] if from_addrs else ""
    if sender and _is_owner(sender, owners):
        return _canonical_account(sender)
    for header in ("Delivered-To", "X-Original-To"):
        addrs = _addresses(msg.get_all(header, []))
        if addrs:
            return _canonical_account(addrs[0])
    recipients = _addresses(msg.get_all("To", []) + msg.get_all("Cc", []))
    for addr in recipients:
        if _is_owner(addr, owners):
            return _canonical_account(addr)
    return _canonical_account(recipients[0]) if recipients else UNKNOWN_ACCOUNT


def message_relpath(msg, raw, owners, fallback):
    """The path relative to the store a message files at: <account>/messages/YYYY/MM/<stem>.eml.

    `fallback` dates a message with no readable Date or Received header (the file's time).
    """
    when = message_date(raw, fallback)
    return (Path(account_of(msg, owners)) / "messages" / f"{when.year:04d}" / f"{when.month:02d}"
            / f"{message_stem(raw)}.eml")


def _refuse_unmigrated(account_dir):
    """Refuse an account still on the archive/ layout, which would read as holding nothing."""
    if (account_dir / LEGACY_ARCHIVE).is_dir():
        raise Refusal(UNMIGRATED.format(account=account_dir.name))


def _subject(raw):
    msg = email.message_from_bytes(raw, policy=default_policy)
    return " ".join(str(msg.get("Subject", "") or "").split())


def ingest(src, *, store, identity, move=True, dry_run=False):
    """File one .eml into the store; return what was (or would be) done.

    Idempotent and byte-preserving, never overwriting. A message its account already holds
    (a file with its stem in any month, or the file the account's own manifest records for its
    Message-ID) is `exists`: nothing is written, and the source is consumed on `move` only when
    it is byte-identical to the stored file. Another account holding it does not count: each
    account keeps its own copy. When the held file's Subject differs, `subject_differs` holds
    both: usually a copy rewritten in transit, rarely a reused Message-ID, for the owner to
    read. A new file is created exclusively and read-only (0444), as wicket writes one.
    `action` is filed, exists or plan. Refuses an account wicket has not migrated.
    """
    raw = src.read_bytes()
    msg = email.message_from_bytes(raw, policy=default_policy)
    owners = owner_addresses(identity)
    mtime = datetime.fromtimestamp(src.stat().st_mtime, tz=timezone.utc)
    rel = message_relpath(msg, raw, owners, mtime)
    account_dir, dst = store / rel.parts[0], store / rel
    _refuse_unmigrated(account_dir)
    msgid = normalize_message_id(_message_id_of(_header_block(raw)))
    info = {"src": str(src), "msgid": msgid, "account": rel.parts[0], "relpath": str(rel),
            "path": str(dst)}
    held = _held(msgid, dst, account_dir)
    if held is None and not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(dst, "xb") as handle:  # never over a file that appeared meanwhile
                handle.write(raw)
        except FileExistsError:
            held = dst
        else:
            os.chmod(dst, 0o444)
            if move:
                src.unlink()
            info["action"] = "filed"
            return info
    if held is None:
        info["action"] = "plan"
        return info
    info.update(action="exists", relpath=str(held.relative_to(store)), path=str(held))
    stored = held.read_bytes()
    if stored != raw and _subject(stored) != _subject(raw):
        info["subject_differs"] = {"this": _subject(raw), "held": _subject(stored)}
    if not dry_run and move and src.resolve() != held.resolve() and stored == raw:
        src.unlink()  # a byte-identical duplicate of the stored file
    return info


def _held(msgid, dst, account_dir):
    """The file this account already holds for this message: one with its stem in any month,
    else the file the account's manifest records for its Message-ID; None when neither."""
    if dst.exists():
        return dst
    same_stem = message_files(account_dir).get(dst.stem)
    if same_stem is not None:
        return same_stem
    return _from_manifests({msgid}, [account_dir]).get(msgid) if msgid else None


def resolve(message_id, store):
    """The stored .eml for a message-id in `store`, or None; see resolve_many."""
    return resolve_many([message_id], store)[message_id]


def resolve_many(message_ids, store):
    """{message-id: the stored .eml holding it in `store`, or None}.

    Answered in two passes, the second only for the ids the first left open:
      1. wicket's manifests: a row whose id is the id, not excluded, whose `stem` names a file
         under its account's messages/ whose Message-ID header is the id (the row is a cache;
         the file decides). Of several such files, the first in path order.
      2. every file under each account's messages/, by its Message-ID header, in path order,
         from one walk: a file ingest filed, which no manifest records.
    The stem cannot be computed from a Message-ID alone (it also hashes Date and From), so no
    pass looks a file up by name. One Message-ID can sit on several files (the same message
    under two accounts); each pass returns the first in sorted path order. Refuses a store
    with an account wicket has not migrated.
    """
    found = dict.fromkeys(message_ids)
    if not found or not store.exists():
        return found
    for account_dir in store_dirs(store):
        _refuse_unmigrated(account_dir)
    normalized = {mid: normalize_message_id(mid) for mid in found}
    answered = _from_manifests({n for n in normalized.values() if n}, manifest_stores(store))
    for mid, key in normalized.items():
        found[mid] = answered.get(key)
    wanted = {}  # normalized id -> the given ids that normalize to it
    for mid, path in found.items():
        if path is None and normalized[mid]:
            wanted.setdefault(normalized[mid], []).append(mid)
    if wanted:
        _match_headers(sorted(store.glob(MESSAGES_GLOB)), wanted, found)
    return found


# wicket's per-account manifest: <store>/<account>/manifest/<YYYY>.jsonl, one JSON row per
# message (wicket ADR 0002). wicket replaces a shard whole through a temp file and a rename,
# so no reader sees it half written; a line that does not parse is skipped all the same.
# A row's `stem` names its file under <store>/<account>/messages/YYYY/MM/ (ADR-MAIL).
MANIFEST_DIRNAME = "manifest"
MESSAGES_GLOB = "[!.]*/messages/[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.eml"


def message_files(account_dir):
    """{stem: file} for every file under one account's messages/; the first in path order wins."""
    found = {}
    for path in sorted(account_dir.glob("messages/[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.eml")):
        found.setdefault(path.stem, path)
    return found


def manifest_rows(store_dir):
    """Every row in one store's manifest shards, oldest shard first; none when it has none."""
    for shard in sorted((store_dir / MANIFEST_DIRNAME).glob("*.jsonl")):
        try:
            text = shard.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def manifest_stores(store):
    """The account directories under `store` that hold a manifest, sorted."""
    if not store.is_dir():
        return []
    return [d for d in store_dirs(store) if (d / MANIFEST_DIRNAME).is_dir()]


def _from_manifests(keys, account_dirs):
    """{normalized id: its stored file} for the ids the manifests answer, each verified.

    A row's id is wicket's key, made by the same normalization as ours; a second copy's id is
    `<key>|<provider>:<native-id>`, so the key is the part before `|`.
    """
    candidates = {}
    for store_dir in account_dirs:
        files = None  # the account's messages/, indexed only if a row asks
        for row in manifest_rows(store_dir):
            key = str(row.get("id", "")).split("|", 1)[0]
            if key in keys and row.get("stem") and not row.get("excluded"):
                files = message_files(store_dir) if files is None else files
                if str(row["stem"]) in files:
                    candidates.setdefault(key, set()).add(files[str(row["stem"])])
    answered = {}
    for key, paths in candidates.items():
        for path in sorted(paths):
            if key in message_ids_in(path):
                answered[key] = path
                break
    return answered


def message_ids_in(path):
    """The normalized Message-IDs in a file's header block; none when it cannot be read."""
    try:
        low = read_head(path).lower()
    except OSError:
        return set()
    return {normalize_message_id(value.decode("utf-8", errors="replace"))
            for value in header_values(low, (b"message-id",))}


def _match_headers(files, wanted, found):
    """Fill `found` for each wanted id from the first file whose Message-ID header is it.

    Each file's header block is read and its Message-ID values taken from the raw bytes, folds
    joined, then normalized as the id was; no header is parsed, and a body or another
    message's References never counts.
    """
    for path in files:
        if not wanted:
            return
        for normalized in message_ids_in(path):
            for mid in wanted.pop(normalized, ()):
                found[mid] = path


# The user-immutable flag is a BSD/macOS file flag: os.chflags and st_flags exist only there.
# Off macOS the three helpers refuse, and mail.py's pin refuses before reaching them.
if sys.platform == "darwin":

    def is_locked(p):
        """Whether the user-immutable flag is set on a file."""
        return bool(os.stat(p).st_flags & stat.UF_IMMUTABLE)

    def lock(p):
        """Set the user-immutable flag on a file (chflags uchg)."""
        os.chflags(p, os.stat(p).st_flags | stat.UF_IMMUTABLE)  # pylint: disable=no-member

    def unlock(p):
        """Clear the user-immutable flag on a file (chflags nouchg)."""
        os.chflags(p, os.stat(p).st_flags & ~stat.UF_IMMUTABLE)  # pylint: disable=no-member

else:

    def _unavailable():
        return OSError("mail pin needs the macOS user-immutable flag (os.chflags)")

    def is_locked(p):
        """Whether the user-immutable flag is set: unavailable off macOS."""
        raise _unavailable()

    def lock(p):
        """Set the user-immutable flag: unavailable off macOS."""
        raise _unavailable()

    def unlock(p):
        """Clear the user-immutable flag: unavailable off macOS."""
        raise _unavailable()
