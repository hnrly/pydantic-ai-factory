---
name: design-interview
description: How to interview a person before drafting the work. Load it before you ask a person anything. It says what only a person can decide, how to ask in rounds of up to four questions, and how to give options with your recommendation first.
---

# Interview a person

You are about to ask a person questions before you draft the work, or while you
revise it. Their time is the scarcest thing in the run. Ask only what they must
decide, and ask it so that a short answer settles it.

## First, take what you can from the evidence

Read the request and everything else you were given before you ask anything.
Take every fact you can from it. Never ask what the request or the evidence
already settles. A question whose answer you could have read is a wasted round.

## What only a person can decide

Ask about these, and only these:

- **Who the work is for, and what done looks like.** The people it serves, and
  how anyone will know it is finished.
- **What is out of scope.** What the work deliberately leaves out.
- **Hard limits.** Constraints you cannot infer: time, money, size, volumes,
  places it must fit, rules it must follow.
- **Choices with more than one defensible answer.** Where two capable people
  could choose differently, and the work would be different for it.

If the work would still be right whichever way a choice went, do not ask: choose
a sensible default and record it as an assumption.

## How to ask

- Ask with the `ask_user_question` tool. One round is one call. Put every
  question you need now into the same call.
- Ask **at most three rounds** in a run. Plan the first round to settle the
  most; use later rounds only for what the answers opened up.
- For each question, say **why it matters**: what changes in the work
  depending on the answer.
- For a choice, give **two or three options**. For each, say what it costs and
  when it fails. Put **the option you recommend first**, with " (Recommended)"
  at the end of its label.
- Keep each question to one decision. A question that bundles two decisions
  gets half an answer.

## After the answers

Record each answer as a decision in the work, with the person as its source.
Read any note the person adds beside their choice: a short note often carries
the decision. If an answer opens a new choice that only a person can make, ask
it in the next round, or list it as an open question.

## Sources

Ported from coship-dev's `design` skill, its interview: several rounds of
questions, the recommended option first, and nothing asked that the evidence
answers. The question themes come from Google's Elephant Goldfish plugin, skill
`egm-brainstorm`.
