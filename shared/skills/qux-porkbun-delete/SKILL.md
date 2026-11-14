---
name: qux-porkbun-delete
description: >
  Delete DNS records of a type at a name at Porkbun, or only one of them: "/qux-porkbun-delete TXT _acme.example.com", or a bare /qux-porkbun-delete to be asked for the name, type and target.
  Dry-runs first and asks the owner before --apply writes. Use for "remove <name>", "delete the <type> record for <name>", or "delete a DNS record".
---

# qux-porkbun-delete

The verb is `delete`. The prompt's arguments are `<TYPE> <name> [target]`.

1. Read `~/.claude/skills/qux-porkbun/PROCEDURE.md` and follow it with the verb `delete`.
2. IF the prompt gives no arguments, THEN start at its "Collect the missing parts".

**Why:** the procedure, the script and the help have one home, in `../qux-porkbun/`. This skill
fixes the verb, so the verb question is not asked.
