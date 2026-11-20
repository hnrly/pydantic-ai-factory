---
name: design-doc
description: The software design template, section by section. Load it before you draft. It says what each part of the Design must carry, the Alternatives Rule, the Zero-Decision Rule, and when a choice is an assumption or an open question.
---

# Write the design

The design is a typed `Design`. It renders as Markdown under fixed headings, so a
person and a builder always find each part in the same place. Fill each field as
below. A field with nothing to say stays empty; it renders as "None.".

## Section by section

- **title**: a short name for the software.
- **summary**: what will be built, why, and what happens next, in a short
  paragraph a smart outsider can follow.
- **problem**: the pain this solves, and how it is handled today.
- **goals**: what the work must achieve, one sentence each.
- **non_goals**: what is deliberately left out, one sentence each.
- **open_questions**: anything undecided that a person must settle. A design
  with open questions goes back for repair; ask instead when you can.
- **assumptions**: defaults nobody was asked about.
- **journeys**: the critical journeys a person takes: given, when, then.
- **behaviour**: what the software does, one statement each, including what
  happens on blank or unusual input.
- **acceptance_criteria**: one checkable sentence each. They are numbered AC1,
  AC2 and so on by their place; do not number them yourself.
- **architecture**: the components, how data flows between them, the stack,
  hosting and data model.
- **decisions**: each choice the plan rests on, with its reason and its source:
  `person`, `code`, `request` or `default`.
- **alternatives**: at least one way you rejected, with its pros, its cons and
  why it was rejected.
- **risks**: what could go wrong, and what the plan does about it.
- **changes**: every file you will create, modify or delete, each path once,
  with what changes and why.
- **steps**: the work in order. Each step lists the paths it changes, the steps
  it depends on, and how anyone can tell it is done. Each step leaves the build
  passing. Together the steps make every change.
- **verification**: the checks that prove the acceptance criteria. Each check
  names its kind (`build`, `unit`, `lint`, `e2e` or `manual`) and the criteria
  it proves, by number. Every criterion is proved by at least one check.

## The Alternatives Rule

A design that offers a single path is not finished. Weigh at least one real
alternative and reject it for a concrete trade-off, not for being "worse".

## The Zero-Decision Rule

A builder who has never seen the code could follow the design and make the right
change the first time, without guessing or asking. Leave no design choice to the
builder: not a schema, an interface, an endpoint path, a dependency, the error
handling, or an edge case.

## Assumption or open question

If you choose a default nobody was asked about, and the design would still be
right if the choice were different, record it under **assumptions**. If the
design depends on the choice, it is an **open question**: ask the person, or
list it.

## A short example

Mission: "A to-do list page. Keep it to one file."

- behaviour: "Pressing Enter on a blank task adds nothing."
- acceptance_criteria: "A task typed and entered appears in the list."
- decisions: topic "Storage", choice "localStorage", reason "Ticked tasks must
  survive a reload and there is no server", source `default`.
- alternatives: "A small server with a database", rejected because the mission
  keeps it to one file.
- changes: create `index.html`, the whole page.
- steps: "Write the page", changes `index.html`, done when every acceptance
  criterion holds in a browser.
- verification: manual, "Open index.html and try each criterion", proves 1.

## Sources

Ported from Google's Elephant Goldfish plugin, skill `egm-draft` and its
`references/design_doc.md` template, and from the shape of this project's own
design documents, such as the Factory Evals design.
