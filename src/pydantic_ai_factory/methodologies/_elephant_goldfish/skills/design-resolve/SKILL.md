---
name: design-resolve
description: How to answer review findings on your work. Load it when goldfish findings come back. It says when to fix a finding, when to contest it with a citation anyone can check, and when to escalate it to a person.
---

# Answer review findings

Fresh reviewers, the goldfish, read your draft with no memory of how it was made.
Their findings came back to you. Answer every Blocker and every Major. A Nit
never blocks: fix it if it is cheap, or leave it.

## Read the explain-back first

When a goldfish explained your work back, compare its account with what you
meant. If they differ, your draft says something other than you meant. Fix the
draft so a capable reader would explain it back as you intended. Never argue
with an explain-back.

## For each Blocker and Major, choose one answer

1. **Fix it.** Change the draft so the finding no longer holds. This is the
   usual answer. A fixed finding needs no tool call: the next round shows it.
2. **Contest it**, with the `contest` tool, when the finding is wrong. Give your
   evidence, and cite what anyone can check:
   - `field:name` for a part of your own output, such as `field:non_goals`,
     when the draft already says what the finding claims it misses;
   - `path:symbol` for a named thing in a file you were given, such as
     `notes/plan.md:Budget`, when the evidence settles it.
   A citation that a check confirms can settle the finding without a person.
   A contest with no citation goes to a person.
3. **Escalate it**, with the `escalate` tool, when only a person can settle it:
   a choice between defensible answers, or a limit nobody has stated. Say why a
   person must decide. Do not also list it as an open question.

## Rules

- **Never contest a comprehension finding.** If a capable reader could not
  follow a passage, the passage is unclear, whatever you meant. Fix it.
- **Never change a severity.** You answer findings; you do not grade them.
- **Never contest to save effort.** A finding that is right but costly is fixed,
  or escalated if a person must weigh the cost.
- Answer each finding once. A settled finding needs nothing more from you.

## Sources

Ported from Google's Elephant Goldfish plugin, skill `egm-resolve`: fix,
contest or escalate each finding, never contest a comprehension gap, and never
change a severity.
