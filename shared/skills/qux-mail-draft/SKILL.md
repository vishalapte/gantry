---
name: qux-mail-draft
description: >
  Draft a message, subject and body included, from an address at a domain in the sender
  config, and show the exact bytes SES will get: "/qux-mail-draft", or "/qux-mail-draft
  --from me@example.org --to alice@example.com" with what the message is for. Task mail (a
  claim, a cancellation, a refund, a setup) gets the subject "<reference>: <VERB> ..." and
  the ask first in the body. A recipient with a person record gets a message fitted to what
  the record states. Writes a draft, never sends one; sending is "/qux-mail send N".
  Use for "draft an email", "write a mail to <someone>", "reply to message <id>", or
  "prepare a message from <address>".
---

# qux-mail-draft

The verb is `draft`. It takes `--from`, `--to`, `--cc`, `--bcc`, `--reply-to`, `--subject`,
`--body`, `--attach` and `--in-reply-to`. The script, its paths and its help are in
`~/.claude/skills/qux-mail/SKILL.md`; read it first.

1. Collect `--from`, `--to`, and what the message is for, if the prompt does not give them.
2. Ask for each missing part in a plain message, one at a time. End the turn.
3. IF a recipient's address appears in a file under `~/.delphi/data/people/`, THEN read that person record.
4. Fit the tone, formality and form of address to what that record states.
5. Never infer culture or ethnicity from a name, an employer or a country.
6. Write the body from the facts the owner gave in this session or a stored message holds.
7. IF the message is task mail, THEN write the subject as `<reference>: <VERB> ...` and put the ask first in the body.
8. IF task mail has no reference, THEN start the subject with the verb.
9. Pass the subject as `--subject`, and pass only the other flags the prompt names.
10. Follow qux-mail's "Draft" section from the body file on: `--body`, `--in-reply-to`, `mail.py draft`, show the whole draft.
11. Do not run `send`; the draft waits for the owner.

**Why:** a message works when it fits its reader, and a person record states who the reader
is. A guess from a name is often wrong, and a wrong guess is worse than a neutral message.

**Why:** task mail asks the recipient to act. Its reader works a queue and routes by reference
and action, so both lead (BLUF, bottom line up front). The owner edits the draft when it is
wrong, so the skill writes the subject and body rather than asking for them.
