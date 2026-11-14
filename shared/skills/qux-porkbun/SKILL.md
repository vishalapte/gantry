---
name: qux-porkbun
description: >
  Porkbun DNS: create, update or delete one record, list the account's domains, or show a
  domain's records, capture the records a domain publishes elsewhere into Porkbun's zone
  before its nameservers move, list or restore the zone's restore points (undo), or run
  Porkbun's preflight for a change such as moving the nameservers, from a prompt such as "/qux-porkbun create A auth.example.com 192.0.2.1",
  "/qux-porkbun list" or "/qux-porkbun domain example.com". A bare /qux-porkbun shows the help and asks for the verb, then the name, the
  record type and the target. Writes always dry-run first and ask the owner before --apply.
  /qux-porkbun-create, /qux-porkbun-update, /qux-porkbun-delete, /qux-porkbun-list and /qux-porkbun-domain do the
  same with the verb fixed. Use for "DNS at porkbun", "add a DNS record", "change a DNS
  record", "remove a DNS record", "which domains do I have", or "what DNS records does
  <name> have".
---

# Porkbun DNS

1. IF the prompt gives no arguments, THEN run `python3.14 ~/.claude/skills/qux-porkbun/porkbun.py` and show its output as the help.
2. IF the prompt names no verb, THEN ask with AskUserQuestion: `create`, `update`, `delete`, `list`.
3. Say in that question that Other takes `domain`, to show one domain's records, `capture`, `history`, `restore` or `check`.
4. Do not mark any option "(Recommended)".
5. With the verb known, read `~/.claude/skills/qux-porkbun/PROCEDURE.md` and follow it for that verb.

**Why:** one procedure serves this skill and the five verb skills, so it lives in one file.
This skill adds only the help and the verb question, which holds four options, so `domain`,
`capture`, `history`, `restore` and `check` go under Other.
