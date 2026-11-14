---
name: qux-mail-dmarc
description: >
  Read every DMARC report in the mail store and rank what they show, threats and security
  holes first: "/qux-mail-dmarc", or "/qux-mail-dmarc --domain example.org --after
  2026-01-01". Threats are senders that failed DMARC while using a domain and were
  delivered, or the domain's own senders failing; blocked attempts are counted in the statistics. Holes are p=none, pct below 100, sp=none, senders that pass on SPF alone,
  and domains whose reports stopped. Statistics come last. Writes nothing, so it asks no
  approval. Use for "review the DMARC reports", "is anyone spoofing my domains", "DMARC
  statistics", or "which domains are exposed".
---

# qux-mail-dmarc

The verb is `dmarc`. It takes `--domain`, `--after`, `--before` and `--days`, all optional.

1. Read `~/.claude/skills/qux-mail/SKILL.md` and follow its "Read: dmarc" section.
2. Pass only the flags the prompt names.

**Why:** the procedure, the script and the help have one home, in `../qux-mail/`. This skill
fixes the verb, so the verb question is not asked.
