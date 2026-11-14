"""Tests for shared/skills/qux-mail: the store, ingest, resolve, pin and the command line.
Run: python3 -m unittest test_mail test_mail_search test_mail_send test_mail_dmarc

Lives at the repo root for the reason test_git_boundary.py gives: a top-level file is never
installed. The scripts hold no path: every path they read or write (the mail store,
the owner-address file, the sender config, the drafts folder, the sent log) comes from a flag
or an ENINE_MAIL_* variable, and MailCase sets only temp-dir values after removing any the
environment had. Every address, name and message-id here is made up.
"""

import contextlib
import email
import email.policy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from email.policy import default as default_policy
from pathlib import Path
from unittest import mock

from test_configure_domain_email import load

mail = load("qux-mail", "mail.py")
store = sys.modules["mail_store"]
search_module = sys.modules["mail_search"]
send_module = sys.modules["mail_send"]
paths_module = sys.modules["mail_paths"]
MODULES = (mail, store, search_module, send_module, sys.modules["mail_eml"], paths_module, sys.modules["mail_dmarc"])

OWNER = "owner@example.com"
MSGID = "test-0001@mail.example.com"


class MailCase(unittest.TestCase):
    """A temp dir holding every path the scripts touch, and the owner's identity in it.

    The five paths are given the way a consumer gives them, by ENINE_MAIL_* variables, and
    any such variable already in the environment is removed first, so no test reads a real path.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self._remove)
        self.root = self.tmp / "mail"
        self.identity = self.tmp / "identity.json"
        self.senders = self.tmp / "senders.toml"
        self.drafts = self.tmp / "out" / "drafts"
        self.sent = self.tmp / "out" / "sent.jsonl"
        environ = {key: value for key, value in os.environ.items() if not key.startswith("ENINE_MAIL_")}
        environ.update(ENINE_MAIL_STORE=str(self.root), ENINE_MAIL_IDENTITY=str(self.identity),
                       ENINE_MAIL_SENDERS=str(self.senders), ENINE_MAIL_DRAFTS=str(self.drafts),
                       ENINE_MAIL_SENT=str(self.sent))
        patcher = mock.patch.dict(os.environ, environ, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.identity.write_text(json.dumps({"owner_addresses": [OWNER]}))

    def owners(self):
        return store.owner_addresses(self.identity)

    def ingest(self, src, **kwargs):
        return store.ingest(src, store=self.root, identity=self.identity, **kwargs)

    def resolve(self, message_id):
        return store.resolve(message_id, self.root)

    def resolve_many(self, message_ids):
        return store.resolve_many(message_ids, self.root)

    def search(self, **kwargs):
        return search_module.search(store=self.root, **kwargs)

    def _remove(self):
        """pin sets the user-immutable flag; clear it so the temp dir can be removed."""
        if sys.platform == "darwin":
            for path in self.tmp.rglob("*"):
                with contextlib.suppress(OSError):
                    os.chflags(path, 0)  # pylint: disable=no-member
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv):
        """(exit code, stdout, stderr) of mail.py with these arguments."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mail.main(list(argv))
        return code, out.getvalue(), err.getvalue()


def eml(from_addr, to_addr, *, msgid=MSGID, date="Wed, 08 Jul 2026 09:56:11 -0400"):
    return (f"From: {from_addr}\r\nTo: {to_addr}\r\nDate: {date}\r\n"
            f"Message-ID: <{msgid}>\r\nSubject: hi\r\n\r\nbody\r\n").encode()


def parsed(raw):
    return email.message_from_bytes(raw, policy=default_policy)


# Where ingest files eml(OWNER, "bob@example.org"): the owner's account (outbound), the month of its
# Date header in UTC (09:56 -0400 on 8 July 2026 is 13:56Z, July), and the file named by its stem.
OUTBOUND_RELPATH = f"{OWNER}/messages/2026/07/{store.message_stem(eml(OWNER, 'bob@example.org'))}.eml"


def store_walks(glob):
    """How many store-wide walks (resolve's Message-ID scan) a patched Path.glob recorded."""
    return sum(1 for call in glob.call_args_list if call.args[1] == store.MESSAGES_GLOB)


def counting_glob():
    """Path.glob, patched to record each call and still glob."""
    return mock.patch.object(Path, "glob", autospec=True, side_effect=Path.glob)


class Isolation(MailCase):
    def test_no_module_holds_a_path_of_its_own(self):
        for module in MODULES:
            for name, value in vars(module).items():
                self.assertNotIsInstance(value, Path, f"{module.__name__}.{name} = {value}")
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("Path.home", source, module.__name__)
            self.assertNotIn("expanduser(\"~", source, module.__name__)


VARIABLES = {"store": "ENINE_MAIL_STORE", "identity": "ENINE_MAIL_IDENTITY", "senders": "ENINE_MAIL_SENDERS",
             "drafts": "ENINE_MAIL_DRAFTS", "sent": "ENINE_MAIL_SENT"}
FLAGS = {"store": "--store", "identity": "--identity", "senders": "--senders", "drafts": "--drafts", "sent": "--sent"}
SENDER_CONFIG = '[defaults]\nregion = "us-east-1"\n[domains."example.org"]\nprofile = "p"\n'


class PathsFromTheConsumer(MailCase):
    """No path has a default: each comes from its flag or its variable, and a verb needs only its own."""

    def setUp(self):
        super().setUp()
        self.root.mkdir()
        self.senders.write_text(SENDER_CONFIG)
        self.body = self.tmp / "body.txt"
        self.body.write_text("hello\n")
        self.message = ["--from", "desk@example.org", "--to", "bob@example.net", "--subject", "S",
                        "--body", str(self.body)]

    def only(self, *names):
        """The environment with only these paths' variables set."""
        environ = {k: v for k, v in os.environ.items() if not k.startswith("ENINE_MAIL_")}
        environ.update({VARIABLES[n]: os.environ[VARIABLES[n]] for n in names})
        return mock.patch.dict(os.environ, environ, clear=True)

    def test_a_missing_path_refuses_and_names_its_flag_and_variable(self):
        with self.only():
            code, out, err = self.run_cli("search", "--subject", "x")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("search needs the mail store: pass --store DIR or set ENINE_MAIL_STORE", err)
        with self.only():
            code, _, err = self.run_cli("ingest", str(self.body))
        self.assertIn("--store DIR or set ENINE_MAIL_STORE", err)
        self.assertIn("--identity FILE or set ENINE_MAIL_IDENTITY", err)

    def test_each_path_from_its_flag_before_or_after_the_verb(self):
        with self.only():
            self.assertEqual(self.run_cli("search", "--subject", "x", "--store", str(self.root))[0], 1)
            self.assertEqual(self.run_cli("--store", str(self.root), "search", "--subject", "x")[0], 1)
            argv = ["draft", *self.message, "--senders", str(self.senders), "--drafts", str(self.drafts)]
            self.assertEqual(self.run_cli(*argv)[0], 0)
            self.assertEqual(self.run_cli("--drafts", str(self.drafts), "--sent", str(self.sent), "draft")[0], 0)
            src = self.tmp / "m.eml"
            src.write_bytes(eml(OWNER, "bob@example.org"))
            code, out, _ = self.run_cli("ingest", str(src), "--store", str(self.root), "--identity", str(self.identity))
            self.assertEqual(code, 1)
            self.assertIn(OUTBOUND_RELPATH, out)  # the identity file made it outbound

    def test_a_flag_beats_its_variable(self):
        wrong = self.tmp / "wrong-store"
        with mock.patch.dict(os.environ, {"ENINE_MAIL_STORE": str(wrong)}):
            self.assertEqual(self.run_cli("search", "--subject", "x")[0], 2)  # no store at the variable's path
            self.assertEqual(self.run_cli("search", "--subject", "x", "--store", str(self.root))[0], 1)
        self.assertEqual(mail.mail_paths.resolve(mail.parser().parse_args(["--drafts", "/a", "draft"]),
                                                 {"ENINE_MAIL_DRAFTS": "/b", "ENINE_MAIL_SENT": ""}).drafts, Path("/a"))
        self.assertIsNone(mail.mail_paths.resolve(mail.parser().parse_args(["draft"]), {"ENINE_MAIL_SENT": ""}).sent)

    def test_a_verb_takes_only_the_path_flags_it_uses(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            mail.parser().parse_args(["search", "--subject", "x", "--senders", "f"])

    def test_each_verb_needs_only_its_own_paths(self):
        self.run_cli("draft", *self.message)  # draft 1, with every path set
        src = self.tmp / "m.eml"
        src.write_bytes(eml(OWNER, "bob@example.org"))
        cases = [
            (("search", "--subject", "x"), ("store",)),
            (("resolve", MSGID), ("store",)),
            (("ingest", str(src)), ("store", "identity")),
            (("draft",), ("drafts", "sent")),
            (("draft", "1"), ("drafts",)),
            (("send", "1"), ("drafts",)),
            (("draft", *self.message), ("senders", "drafts")),
            (("send", *self.message), ("senders", "drafts")),
            (("draft", *self.message, "--in-reply-to", "p@example.net"), ("senders", "drafts", "sent", "store")),
        ]
        if hasattr(os, "chflags"):
            cases.append((("pin", MSGID, "--check"), ("store",)))
        for argv, needed in cases:
            with self.only(*needed):
                code, _, err = self.run_cli(*argv)
            self.assertNotEqual(code, 2, (argv, err))
            for missing in needed:
                with self.only(*(n for n in needed if n != missing)):
                    code, _, err = self.run_cli(*argv)
                self.assertEqual(code, 2, (argv, missing))
                self.assertIn(f"{FLAGS[missing]} ", err)
                self.assertIn(VARIABLES[missing], err)

    def test_send_apply_needs_the_drafts_and_the_sent_log(self):
        self.run_cli("draft", *self.message)
        with self.only("drafts"):
            code, _, err = self.run_cli("send", "1", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("--sent FILE or set ENINE_MAIL_SENT", err)


class Store(MailCase):
    def test_normalize_message_id_strips_and_lowercases(self):
        self.assertEqual(store.normalize_message_id("<ABC@Host.Example>"), "abc@host.example")
        self.assertIsNone(store.normalize_message_id("  "))
        self.assertIsNone(store.normalize_message_id(None))

    def test_account_outbound_is_owner_from_not_counterparty(self):
        self.assertEqual(store.account_of(parsed(eml(OWNER, "bob@example.org")), self.owners()), OWNER)

    def test_account_inbound_prefers_delivery_header_alias(self):
        raw = (b"From: noreply@bookings.example.net\r\nTo: svc@example.org\r\n"
               b"Delivered-To: svc@example.org\r\nDate: Thu, 30 Apr 2026 11:59:47 +0000\r\n"
               b"Message-ID: <x@y.example>\r\nSubject: hi\r\n\r\nbody\r\n")
        self.assertEqual(store.account_of(parsed(raw), self.owners()), "svc@example.org")

    def test_account_canonicalizes_plus_tag(self):
        raw = (b"From: shop@store.example\r\nDelivered-To: owner+deals@example.com\r\n"
               b"Date: Thu, 30 Apr 2026 11:59:47 +0000\r\nMessage-ID: <z@y.example>\r\nSubject: hi\r\n\r\nbody\r\n")
        self.assertEqual(store.account_of(parsed(raw), self.owners()), OWNER)

    def test_no_identity_file_reads_as_no_owner(self):
        absent = self.tmp / "absent.json"
        self.assertEqual(store.owner_addresses(absent), frozenset())


# wicket's checkout: WICKET_SRC, else the owner's standard clone. Read only by StemParity.
WICKET_SRC = Path(os.environ.get("WICKET_SRC") or Path.home() / "dev" / "personal" / "wicket" / "src")

# Messages that exercise every input of the stem and the month: an offset Date, a missing and an
# unreadable Date (with and without Received), no Message-ID, no From, an id parsed short by
# policy.default, a non-ASCII id, folded headers, a display name, and CRLF line ends.
PARITY_CASES = [
    b"Message-ID: <a@x>\nFrom: Them <Them@Acme.com>\nDate: Mon, 06 Jul 2026 14:30:00 +0530\n\nb\n",
    b"Message-ID: <a@x>\nFrom: them@acme.com\n\nb\n",
    b"Message-ID: <a@x>\nFrom: them@acme.com\nDate: +0530\nReceived: from a by b; Tue, 30 Jun 2026 23:59:00 -0400\n\nb\n",
    b"From: them@acme.com\nDate: Mon, 06 Jul 2026 09:00:00 +0000\n\nb\n",
    b"Message-ID: <a@x>\nDate: Mon, 06 Jul 2026 09:00:00 +0000\n\nb\n",
    b"Message-ID: <urn:correios:msg:1@amazon.com>\nFrom: a@b.com\nDate: Fri, 01 Mar 2024 09:00:00 GMT\n\nb\n",
    "Message-ID: <caf\u00e9@x>\nFrom: a@b.com\nDate: Fri, 01 Mar 2024 09:00:00 +0000\n\nb\n".encode(),
    b"Message-ID:\r\n <Folded@X>\r\nFrom: \"Last, First\"\r\n <First.Last@Example.org>\r\n"
    b"Date: Sun, 31 Dec 2023 23:30:00 -0100\r\n\r\nb\r\n",
    b"Subject: nothing else\n\nb\n",
    b"Message-ID: <far@x>\nFrom: a@b.com\nDate: Fri, 31 Dec 9999 23:00:00 -0100\n\nb\n",
]


class StemParity(unittest.TestCase):
    """mail_store's stem and month are wicket.message's, case for case (ADR-MAIL, 2026-11-14).

    The two repos import nothing from each other, so the rule has two copies; this test holds
    them equal. It loads wicket from its checkout at test time only, and skips, saying why, when
    that checkout is absent or predates the rule.
    """

    def wicket_message(self):
        if not (WICKET_SRC / "wicket" / "message.py").exists():
            self.skipTest(f"no wicket checkout at {WICKET_SRC}; set WICKET_SRC")
        sys.path.insert(0, str(WICKET_SRC))
        try:
            import wicket.message as wicket_message  # pylint: disable=import-outside-toplevel
        finally:
            sys.path.remove(str(WICKET_SRC))
        if not hasattr(wicket_message, "message_stem"):
            self.skipTest(f"the wicket checkout at {WICKET_SRC} predates message_stem")
        return wicket_message

    def test_the_stem_and_month_match_wickets_on_every_case(self):
        from datetime import datetime, timezone  # pylint: disable=import-outside-toplevel

        theirs = self.wicket_message()
        fallback = datetime(2020, 1, 1, tzinfo=timezone.utc)
        for raw in PARITY_CASES:
            with self.subTest(raw=raw[:40]):
                self.assertEqual(store.message_stem(raw), theirs.message_stem(raw))
                self.assertEqual(store.message_date(raw, fallback), theirs.message_date(raw, fallback))


class LayoutGuards(MailCase):
    """The new layout's guarantees: an unmigrated store is refused, each account keeps its own
    copy, a held copy whose Subject differs is reported, a link is never filed, and a filed
    file is read-only and never written over."""

    def test_an_account_still_on_archive_is_refused_by_ingest_resolve_and_search(self):
        (self.root / OWNER / "archive").mkdir(parents=True)
        src = self.tmp / "m.eml"
        src.write_bytes(eml(OWNER, "bob@example.org"))
        with self.assertRaisesRegex(store.Refusal, "run `wicket migrate` first"):
            store.ingest(src, store=self.root, identity=self.identity, dry_run=True)
        with self.assertRaisesRegex(store.Refusal, "run `wicket migrate` first"):
            store.resolve(MSGID, self.root)
        code, _, err = self.run_cli("search", "--subject", "x")
        self.assertEqual(code, 2)
        self.assertIn("run `wicket migrate` first", err)
        self.assertTrue(src.exists())

    def test_a_dot_dir_is_not_an_account(self):
        """wicket's backups sit beside the accounts: one still on archive/ refuses nothing, and
        one holding messages/ and a manifest is never searched or resolved into."""
        (self.root / ".backup-old" / "archive").mkdir(parents=True)
        backup = self.root / ".backup-new"
        held = backup / "messages" / "2026" / "07" / "18f2aa00bb11cc22.eml"
        held.parent.mkdir(parents=True)
        held.write_bytes(eml(OWNER, "bob@example.org"))
        (backup / "manifest").mkdir()
        (backup / "manifest" / "2026.jsonl").write_text(
            json.dumps({"id": MSGID, "stem": held.stem, "downloaded": True}) + "\n")
        self.assertIsNone(store.resolve(MSGID, self.root))
        self.assertEqual(store.manifest_stores(self.root), [])
        own = self.root / OUTBOUND_RELPATH
        own.parent.mkdir(parents=True)
        own.write_bytes(eml(OWNER, "bob@example.org"))
        self.assertEqual(store.resolve(MSGID, self.root), own)
        code, out, err = self.run_cli("search", "--subject", "hi")
        self.assertEqual(code, 0, err)
        self.assertIn(str(own), out)
        self.assertNotIn(".backup", out)

    def test_another_accounts_copy_does_not_count(self):
        other = self.root / "other@example.net"
        held = other / "messages" / "2026" / "07" / "18f2aa00bb11cc22.eml"
        held.parent.mkdir(parents=True)
        held.write_bytes(eml(OWNER, "bob@example.org"))
        (other / "manifest").mkdir()
        (other / "manifest" / "2026.jsonl").write_text(
            json.dumps({"id": MSGID, "stem": held.stem, "downloaded": True}) + "\n")
        src = self.tmp / "m.eml"
        src.write_bytes(eml(OWNER, "bob@example.org"))
        info = store.ingest(src, store=self.root, identity=self.identity)
        self.assertEqual(info["action"], "filed")
        self.assertTrue(info["path"].startswith(str(self.root / OWNER / "messages")))

    def test_a_held_copy_whose_subject_differs_is_reported_and_its_source_kept(self):
        raw = eml(OWNER, "bob@example.org")
        rewritten = raw.replace(b"Subject: ", b"Subject: [CATCH-ALL] ", 1)
        self.assertEqual(store.message_stem(raw), store.message_stem(rewritten))
        held = self.root / OUTBOUND_RELPATH
        held.parent.mkdir(parents=True)
        held.write_bytes(raw)
        src = self.tmp / "m.eml"
        src.write_bytes(rewritten)
        info = store.ingest(src, store=self.root, identity=self.identity)
        self.assertEqual(info["action"], "exists")
        self.assertIn("[CATCH-ALL]", info["subject_differs"]["this"])
        self.assertTrue(src.exists())
        self.assertEqual(held.read_bytes(), raw)

    def test_a_link_is_skipped_not_filed(self):
        target = self.tmp / "real.eml"
        target.write_bytes(eml(OWNER, "bob@example.org"))
        link = self.tmp / "link.eml"
        link.symlink_to(target)
        code, _, err = self.run_cli("ingest", str(link), "--apply")
        self.assertEqual(code, 1)
        self.assertIn("skip (a link)", err)
        self.assertFalse((self.root / OUTBOUND_RELPATH).exists())

    def test_a_filed_message_is_read_only(self):
        src = self.tmp / "m.eml"
        src.write_bytes(eml(OWNER, "bob@example.org"))
        info = store.ingest(src, store=self.root, identity=self.identity)
        self.assertEqual(os.stat(info["path"]).st_mode & 0o777, 0o444)


class Ingest(MailCase):
    RELPATH = OUTBOUND_RELPATH

    def source(self, name="msg.eml"):
        src = self.tmp / name
        src.write_bytes(eml(OWNER, "bob@example.org"))
        return src

    def test_a_dry_run_prints_the_target_and_writes_nothing(self):
        src = self.source()
        code, out, _ = self.run_cli("ingest", str(src))
        self.assertEqual(code, 1)
        self.assertIn(f"plan    {self.RELPATH}  <-  msg.eml", out)
        self.assertIn("Dry run: nothing written", out)
        self.assertTrue(src.exists())
        self.assertFalse((self.tmp / "mail").exists())

    def test_apply_moves_it_and_is_idempotent(self):
        src = self.source()
        code, out, _ = self.run_cli("ingest", str(src), "--apply")
        self.assertEqual(code, 0)
        self.assertIn(f"filed   {self.RELPATH}", out)
        self.assertTrue((self.tmp / "mail" / self.RELPATH).exists())
        self.assertFalse(src.exists())
        # Filing an identical copy again changes nothing and consumes the duplicate.
        again = self.source("again.eml")
        code, out, _ = self.run_cli("ingest", str(again), "--apply")
        self.assertEqual(code, 0)
        self.assertIn("exists", out)
        self.assertFalse(again.exists())

    def test_copy_keeps_the_source(self):
        src = self.source()
        self.assertEqual(self.run_cli("ingest", str(src), "--apply", "--copy")[0], 0)
        self.assertTrue(src.exists())
        self.assertEqual((self.tmp / "mail" / self.RELPATH).read_bytes(), src.read_bytes())

    def test_a_message_wicket_already_stored_is_exists_and_nothing_is_written(self):
        # wicket's copy, under a stem the manifest records rather than the one ingest computes.
        folder = self.tmp / "mail" / OWNER / "messages" / "2026" / "07"
        folder.mkdir(parents=True)
        harvested = folder / "18f2aa00bb11cc22.eml"
        harvested.write_bytes(eml(OWNER, "bob@example.org").replace(b"\r\nSubject", b"\r\nX-Harvested: 1\r\nSubject"))
        write_manifest(self.tmp / "mail" / OWNER, [{"id": MSGID, "downloaded": True, "stem": harvested.stem}])
        src = self.source()
        for flags in ((), ("--apply",)):
            code, out, _ = self.run_cli("ingest", str(src), *flags)
            self.assertEqual(code, 0, out)
            self.assertIn(f"exists  {OWNER}/messages/2026/07/{harvested.name}", out)
        self.assertFalse((self.tmp / "mail" / self.RELPATH).exists())
        self.assertTrue(src.exists())  # not byte-identical, so the source is kept

    def test_a_stored_copy_with_other_bytes_is_never_overwritten(self):
        target = self.tmp / "mail" / self.RELPATH
        target.parent.mkdir(parents=True)
        target.write_bytes(b"the stored copy")
        src = self.source()
        code, out, _ = self.run_cli("ingest", str(src), "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("exists", out)
        self.assertEqual(target.read_bytes(), b"the stored copy")
        self.assertTrue(src.exists())

    def test_a_missing_file_is_skipped_and_exits_1(self):
        code, _, err = self.run_cli("ingest", str(self.tmp / "nope.eml"), "--apply")
        self.assertEqual(code, 1)
        self.assertIn("skip (not a file)", err)

    def test_json(self):
        code, out, _ = self.run_cli("ingest", str(self.source()), "--json")
        self.assertEqual(code, 1)
        result = json.loads(out)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["files"][0]["relpath"], self.RELPATH)


class Resolve(MailCase):
    def test_resolve_finds_by_message_id(self):
        src = self.tmp / "msg.eml"
        src.write_bytes(eml(OWNER, "bob@example.org"))
        self.ingest(src, move=True)
        hit = self.resolve(MSGID)
        self.assertEqual(hit, self.root / OUTBOUND_RELPATH)
        self.assertIsNone(self.resolve("no-such-id@nowhere.example"))
        code, out, _ = self.run_cli("resolve", f"<{MSGID}>")
        self.assertEqual((code, out.strip()), (0, str(hit)))
        code, out, err = self.run_cli("resolve", "no-such-id@nowhere.example")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("not found", err)


class ResolveByHeader(MailCase):
    """The harvest names files another way: resolve finds a message by the Message-ID inside it."""

    def harvested(self, stem, msgid_header, *, account=OWNER, extra=b"", body=b"body"):
        folder = self.tmp / "mail" / account / "messages" / "2026" / "08"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{stem}.eml"
        path.write_bytes(b"From: alice@example.org\r\nTo: owner@example.com\r\n" + extra
                         + b"Message-ID:" + msgid_header + b"\r\nSubject: hi\r\n\r\n" + body + b"\r\n")
        return path

    def test_a_harvested_file_with_an_unrelated_name_resolves(self):
        path = self.harvested("18c2f0a1b2c3d4e5", b" <harvest-0001@mail.example.org>")
        self.assertEqual(self.resolve("<harvest-0001@mail.example.org>"), path)
        code, out, _ = self.run_cli("resolve", "harvest-0001@mail.example.org")
        self.assertEqual((code, out.strip()), (0, str(path)))

    def test_case_folding_and_a_comment_still_match(self):
        path = self.harvested("aaaa000000000001", b"\r\n <Harvest-0002@Mail.Example.ORG> (sent by the list)")
        self.assertEqual(self.resolve("harvest-0002@mail.example.org"), path)
        commented = self.harvested("aaaa000000000002", b" <part(x)one-0003@mail.example.org>")
        self.assertEqual(self.resolve("<partone-0003@mail.example.org>"), commented)

    def test_an_id_only_in_a_body_or_references_does_not_resolve(self):
        self.harvested("bbbb000000000001", b" <other-0001@mail.example.org>",
                       extra=b"References: <wanted-0009@mail.example.org>\r\n",
                       body=b"see <wanted-0009@mail.example.org>")
        self.assertIsNone(self.resolve("wanted-0009@mail.example.org"))
        self.assertEqual(self.run_cli("resolve", "wanted-0009@mail.example.org")[0], 1)

    def test_several_ids_are_answered_in_one_walk(self):
        first = self.harvested("cccc000000000001", b" <many-0001@mail.example.org>")
        second = self.harvested("cccc000000000002", b" <many-0002@mail.example.org>")
        self.harvested("cccc000000000003", b" <unrelated-0003@mail.example.org>")
        reads = []
        real_read = store.read_head

        def read(path):
            reads.append(path.name)
            return real_read(path)

        with mock.patch.object(store, "read_head", read), counting_glob() as glob:
            found = self.resolve_many(["many-0001@mail.example.org", "<many-0002@mail.example.org>",
                                        "missing-0004@mail.example.org"])
        self.assertEqual(found, {"many-0001@mail.example.org": first, "<many-0002@mail.example.org>": second,
                                 "missing-0004@mail.example.org": None})
        self.assertEqual(store_walks(glob), 1)
        self.assertEqual(sorted(reads), sorted(set(reads)))  # each file's header read at most once

    def test_the_first_in_path_order_wins(self):
        msgid = b" <" + MSGID.encode() + b">"
        later = self.harvested("ffff000000000001", msgid, account="z@example.com")
        earlier = self.harvested("ffff000000000002", msgid, account="a@example.com")
        self.assertEqual(self.resolve(MSGID), earlier)
        self.assertTrue(later.exists())


def write_manifest(store_dir, rows, *, year=2026, tail=""):
    """A wicket manifest shard as wicket writes it: one sorted-key JSON row per line."""
    shard = store_dir / "manifest" / f"{year}.jsonl"
    shard.parent.mkdir(parents=True, exist_ok=True)
    shard.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows) + tail)
    return shard


class ResolveFromManifest(MailCase):
    """wicket's manifest answers resolve before any walk; the file still has the last word."""

    harvested = ResolveByHeader.harvested

    def setUp(self):
        super().setUp()
        self.reads = []
        real_read = store.read_head

        def read(path):
            self.reads.append(path.name)
            return real_read(path)

        for patcher in (mock.patch.object(store, "read_head", read), counting_glob()):
            self.glob = patcher.start()
            self.addCleanup(patcher.stop)

    def row(self, key, stem, **extra):
        return {"id": key, "msgid": "18f2", "thread_id": "1", "downloaded": True, "stem": stem, **extra}

    def walks(self):
        return store_walks(self.glob)

    def test_a_manifest_row_answers_without_a_walk(self):
        target = self.harvested("1111000000000001", b" <man-0001@mail.example.org>")
        for i in range(2, 5):
            self.harvested(f"111100000000000{i}", f" <other-000{i}@mail.example.org>".encode())
        write_manifest(self.tmp / "mail" / OWNER, [self.row("man-0001@mail.example.org", target.stem)],
                       tail='{"id": "torn-0001@mail.exa')  # a torn last line is skipped
        self.assertEqual(self.resolve("<MAN-0001@mail.example.org>"), target)
        self.assertEqual(self.reads, [target.name])  # the one file, to confirm the row
        self.assertEqual(self.walks(), 0)

    def test_a_second_copys_key_and_two_stores_take_the_first_path(self):
        first = self.harvested("2222000000000001", b" <dup-0001@mail.example.org>", account="a@example.com")
        later = self.harvested("2222000000000002", b" <dup-0001@mail.example.org>", account="z@example.com")
        write_manifest(self.tmp / "mail" / "z@example.com", [self.row("dup-0001@mail.example.org", later.stem)])
        write_manifest(self.tmp / "mail" / "a@example.com",
                       [self.row("dup-0001@mail.example.org|gmail:19a0", first.stem)])
        self.assertEqual(self.resolve("dup-0001@mail.example.org"), first)
        self.assertEqual(self.walks(), 0)

    def test_a_row_that_cannot_be_trusted_falls_back_to_the_walk(self):
        real = self.harvested("3333000000000001", b" <fall-0001@mail.example.org>")
        wrong = self.harvested("3333000000000002", b" <someone-else@mail.example.org>")
        write_manifest(self.tmp / "mail" / OWNER, [
            self.row("fall-0001@mail.example.org", "0000000000000000"),  # stale: no such file
            self.row("fall-0001@mail.example.org", wrong.stem),  # the file holds another id
            self.row("fall-0001@mail.example.org", real.stem, excluded="duplicate of x"),
        ])
        self.assertEqual(self.resolve("fall-0001@mail.example.org"), real)
        self.assertEqual(self.walks(), 1)

    def test_several_ids_mix_manifest_and_walk(self):
        from_row = self.harvested("4444000000000001", b" <mix-0001@mail.example.org>")
        walked = self.harvested("4444000000000002", b" <mix-0002@mail.example.org>")
        src = self.tmp / "msg.eml"
        src.write_bytes(eml(OWNER, "bob@example.org"))
        filed = Path(self.ingest(src, move=True)["path"])
        write_manifest(self.tmp / "mail" / OWNER, [self.row("mix-0001@mail.example.org", from_row.stem)])
        found = self.resolve_many(["mix-0001@mail.example.org", MSGID, "mix-0002@mail.example.org"])
        self.assertEqual(found, {"mix-0001@mail.example.org": from_row, MSGID: filed,
                                 "mix-0002@mail.example.org": walked})
        self.assertEqual(self.walks(), 1)


@unittest.skipUnless(hasattr(os, "chflags"), "mail pin needs macOS chflags")
class PinHarvested(MailCase):
    harvested = ResolveByHeader.harvested

    def test_pin_locks_harvested_files_from_one_walk(self):
        one = self.harvested("dddd000000000001", b" <pin-0001@mail.example.org>")
        two = self.harvested("dddd000000000002", b" <pin-0002@mail.example.org>")
        with counting_glob() as glob:
            code, out, _ = self.run_cli("pin", "pin-0001@mail.example.org", "pin-0002@mail.example.org", "--apply")
        self.assertEqual((code, store_walks(glob)), (0, 1))
        self.assertIn("2 locked", out)
        self.assertTrue(store.is_locked(one) and store.is_locked(two))
        self.assertEqual(self.run_cli("pin", "--unpin", "pin-0001@mail.example.org", "--apply")[0], 0)
        self.assertFalse(store.is_locked(one))


@unittest.skipUnless(hasattr(os, "chflags"), "mail pin needs macOS chflags")
class Pin(MailCase):
    def setUp(self):
        super().setUp()
        src = self.tmp / "msg.eml"
        src.write_bytes(eml(OWNER, "bob@example.org"))
        self.path = Path(self.ingest(src, move=True)["path"])

    def test_a_dry_run_lists_what_would_lock_and_locks_nothing(self):
        code, out, _ = self.run_cli("pin", MSGID)
        self.assertEqual(code, 1)
        self.assertIn("would lock", out)
        self.assertFalse(store.is_locked(self.path))

    def test_apply_locks_and_check_then_passes(self):
        self.assertEqual(self.run_cli("pin", MSGID, "--check")[0], 1)
        self.assertEqual(self.run_cli("pin", MSGID, "--apply")[0], 0)
        self.assertTrue(store.is_locked(self.path))
        with self.assertRaises(PermissionError):
            self.path.unlink()
        # Idempotent: nothing is left to do, so the dry run and the check exit 0.
        self.assertEqual(self.run_cli("pin", MSGID, "--apply")[0], 0)
        self.assertEqual(self.run_cli("pin", MSGID)[0], 0)
        code, out, _ = self.run_cli("pin", MSGID, "--check")
        self.assertEqual(code, 0)
        self.assertIn("OK: all 1 .eml locked", out)

    def test_a_dangling_id_fails_every_mode(self):
        for mode in ((), ("--check",), ("--apply",)):
            code, out, err = self.run_cli("pin", MSGID, "missing@nowhere.example", *mode)
            self.assertEqual(code, 1, mode)
            self.assertIn("DANGLING  missing@nowhere.example", out + err)

    def test_unpin_is_a_dry_run_until_apply(self):
        self.run_cli("pin", MSGID, "--apply")
        code, out, _ = self.run_cli("pin", "--unpin", MSGID)
        self.assertEqual(code, 1)
        self.assertIn("would unlock", out)
        self.assertTrue(store.is_locked(self.path))
        self.assertEqual(self.run_cli("pin", "--unpin", MSGID, "--apply")[0], 0)
        self.assertFalse(store.is_locked(self.path))
        self.assertEqual(self.run_cli("pin", "--unpin", MSGID)[0], 0)  # nothing left to unlock

    def test_bad_combinations_are_refused(self):
        for argv in (("pin",), ("pin", MSGID, "--unpin", MSGID), ("pin", MSGID, "--check", "--apply"),
                     ("pin", "--unpin", MSGID, "--check")):
            code, _, err = self.run_cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("refused:", err)
        self.assertFalse(store.is_locked(self.path))


class PinOffMacos(MailCase):
    def test_pin_refuses_without_chflags(self):
        saved = getattr(os, "chflags", None)
        if saved is not None:
            del os.chflags
        try:
            code, _, err = self.run_cli("pin", MSGID)
        finally:
            if saved is not None:
                os.chflags = saved
        self.assertEqual(code, 2)
        self.assertIn("macOS user-immutable flag", err)


class GetBody(unittest.TestCase):
    """mail_eml.get_body: delphi imports it by name, on a message parsed with policy.default."""

    def parse(self, raw):
        return email.message_from_bytes(raw, policy=email.policy.default)

    ALTERNATIVE = (b"From: alice@example.org\r\nTo: bob@example.net\r\nSubject: hi\r\nMIME-Version: 1.0\r\n"
                   b'Content-Type: multipart/alternative; boundary="b"\r\n\r\n'
                   b"--b\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nthe plain body\r\n"
                   b"--b\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>the html body</p>\r\n--b--\r\n")

    def test_text_is_the_plain_body_and_html_the_html_body(self):
        msg = self.parse(self.ALTERNATIVE)
        get_body = sys.modules["mail_eml"].get_body
        self.assertEqual(get_body(msg).strip(), "the plain body")
        self.assertEqual(get_body(msg, type="text").strip(), "the plain body")
        self.assertEqual(get_body(msg, type="html").strip(), "<p>the html body</p>")

    def test_an_attached_text_file_is_not_the_body(self):
        raw = (b"From: alice@example.org\r\nSubject: hi\r\nMIME-Version: 1.0\r\n"
               b'Content-Type: multipart/mixed; boundary="m"\r\n\r\n'
               b'--m\r\nContent-Type: text/plain\r\nContent-Disposition: attachment; filename="notes.txt"\r\n\r\n'
               b"the attached file\r\n"
               b"--m\r\nContent-Type: text/plain\r\n\r\nthe real body\r\n--m--\r\n")
        self.assertEqual(sys.modules["mail_eml"].get_body(self.parse(raw)).strip(), "the real body")

    def test_a_missing_type_is_an_empty_string(self):
        raw = b"From: alice@example.org\r\nSubject: hi\r\nContent-Type: text/plain\r\n\r\nonly plain\r\n"
        get_body = sys.modules["mail_eml"].get_body
        self.assertEqual(get_body(self.parse(raw), type="html"), "")
        self.assertEqual(get_body(self.parse(raw)).strip(), "only plain")

    def test_json_lists_every_text_part_with_attachments_marked(self):
        raw = (b"From: alice@example.org\r\nSubject: hi\r\nMIME-Version: 1.0\r\n"
               b'Content-Type: multipart/mixed; boundary="m"\r\n\r\n'
               b'--m\r\nContent-Type: multipart/alternative; boundary="a"\r\n\r\n'
               b"--a\r\nContent-Type: text/plain\r\n\r\nthe plain body\r\n"
               b"--a\r\nContent-Type: text/html\r\n\r\n<p>the html body</p>\r\n--a--\r\n"
               b'--m\r\nContent-Type: text/plain\r\nContent-Disposition: attachment; filename="notes.txt"\r\n\r\n'
               b"the attached file\r\n"
               b"--m\r\nContent-Type: image/png\r\nContent-Disposition: attachment; filename=\"x.png\"\r\n\r\nxx\r\n--m--\r\n")
        parts = json.loads(sys.modules["mail_eml"].get_body(self.parse(raw), type="json"))
        self.assertEqual([(p["type"], p["content"].strip(), p["attachment"], p["filename"], p["depth"]) for p in parts], [
            ("text/plain", "the plain body", False, None, 0),
            ("text/html", "<p>the html body</p>", False, None, 0),
            ("text/plain", "the attached file", True, "notes.txt", 0),
        ])

    def test_json_reaches_into_an_attached_message_and_gives_its_depth(self):
        inner = (b"From: shop@example.com\r\nSubject: Order 4512\r\nMIME-Version: 1.0\r\n"
                 b'Content-Type: multipart/alternative; boundary="i"\r\n\r\n'
                 b"--i\r\nContent-Type: text/plain\r\n\r\nYour order 4512\r\n"
                 b"--i\r\nContent-Type: text/html\r\n\r\n<p>Your order 4512</p>\r\n--i--\r\n")
        raw = (b"From: alice@example.org\r\nSubject: Fwd: Order\r\nMIME-Version: 1.0\r\n"
               b'Content-Type: multipart/mixed; boundary="m"\r\n\r\n'
               b"--m\r\nContent-Type: text/plain\r\n\r\nsee attached\r\n"
               b'--m\r\nContent-Type: message/rfc822\r\nContent-Disposition: attachment; filename="order.eml"\r\n\r\n'
               + inner + b"\r\n--m--\r\n")
        msg = self.parse(raw)
        parts = json.loads(sys.modules["mail_eml"].get_body(msg, type="json"))
        self.assertEqual([(p["content"].strip(), p["depth"]) for p in parts],
                         [("see attached", 0), ("Your order 4512", 1), ("<p>Your order 4512</p>", 1)])
        self.assertEqual(sys.modules["mail_eml"].get_body(msg).strip(), "see attached")

    def test_json_of_a_message_without_text_is_an_empty_list(self):
        raw = b"From: alice@example.org\r\nSubject: hi\r\nContent-Type: image/png\r\n\r\nxx\r\n"
        self.assertEqual(sys.modules["mail_eml"].get_body(self.parse(raw), type="json"), "[]")

    def test_another_type_is_refused(self):
        with self.assertRaises(ValueError):
            sys.modules["mail_eml"].get_body(self.parse(self.ALTERNATIVE), type="plain")

    def test_the_seven_names_delphi_imports_stay(self):
        eml = sys.modules["mail_eml"]
        for name in ("decode_hdr", "msg_date", "html_to_text", "_decode_part", "body_text", "account_dirs", "get_body"):
            self.assertTrue(callable(getattr(eml, name)), name)
        self.assertFalse(hasattr(eml, "body_parts"))


class CommandLine(MailCase):
    def test_no_arguments_prints_the_help_and_exits_2(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("Usage", out)
        for verb in mail.VERBS:
            self.assertIn(f"mail.py {verb}", out)

    def test_every_verb_takes_json(self):
        for verb in mail.VERBS:
            argv = [verb, "x"] if verb in ("resolve", "ingest") else [verb]
            self.assertTrue(mail.parser().parse_args([*argv, "--json"]).json, verb)


if __name__ == "__main__":
    unittest.main()
