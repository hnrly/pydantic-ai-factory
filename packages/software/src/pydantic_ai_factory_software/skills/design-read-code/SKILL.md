---
name: design-read-code
description: How to ground a software design in the code that exists. Load it before you ask a person anything. It says what to read first, which facts to take from the code, and how to cite every path exactly.
---

# Ground the design in the code

A design that ignores the code that exists makes a builder guess. Before you ask
anything, read what you are given, and take every fact you can from it.

## What to read, in order

1. **The mission.** Every requirement, success criterion and constraint in it is
   kept in the design, and none is added.
2. **The repository's instruction files and skills,** when you can read them:
   `AGENTS.md`, `CLAUDE.md`, and any skills the repository offers. They say how
   the project is built and what it expects.
3. **The entry points.** Where the program starts: its command, its web routes,
   its main module.
4. **The data.** The schema, migrations or models, and where data is stored.
5. **The configuration.** How it is built, run and deployed, and which
   dependencies it already has.
6. **The tests.** How the project proves its behaviour, and what a new check
   should look like.

If you have no file tools, the mission and the conversation are all you have.
Do not invent a codebase: design for what the mission says, and record any
choice the code would have settled as an assumption or an open question.

## What to take from the code

- Fit the design to the code that exists. **Reuse** a module, a pattern or a
  dependency that is already there before adding one.
- Take facts, not guesses: names of files, functions, tables and endpoints as
  they are written.
- Record a decision the code settles with the source `code`, and say where.
- Never ask a person what the code already answers.

## Cite every path exactly

- Write each path from the repository's root, exactly as it is spelled.
- A file you will change must exist; a file you will create must not.
- Name a function, table or endpoint only if you have seen it, or if the design
  creates it.

## Sources

Ported from Google's Elephant Goldfish plugin, the scouts of its `egm-bootstrap`
skill and its codebase details reviewer, and from coship-dev's `design` skill,
"read the code to answer what you can".
