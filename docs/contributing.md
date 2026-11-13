---
title: Contributing
description: How changes to Pydantic AI Factory are shaped, checked and reviewed.
---

# Contributing

This page explains how to change Pydantic AI Factory. The library is built in public as small stacked pull requests, so each change must be easy to read on its own. A change carries its code, its tests and its documentation together. The maintainer reviews and merges; nobody else merges.

## Set up

You need [uv](https://docs.astral.sh/uv/) and Python 3.11 or later. Clone the repository, then install everything:

```bash
uv sync
```

This installs both packages in the workspace and the development tools.

## The checks

Run these before you ask for review. CI runs the first five on Python 3.11 to 3.14, and the keep-out scan and the two site commands once each.

| Command | What it proves |
|---|---|
| `uv run ruff format --check .` | The code is formatted. |
| `uv run ruff check .` | The lint rules pass, including no `assert` in library code. |
| `uv run pyright` | Strict typing passes. |
| `uv run lint-imports` | The package boundaries hold. |
| `uv run pytest` | The tests pass, with warnings treated as errors. |
| `uv run python scripts/keep_out_scan.py` | Nothing private is about to be published. |
| `npm --prefix site run check` | The docs site type-checks. Run `npm --prefix site ci` once first. |
| `npm --prefix site run build` | The docs site builds and every internal link resolves. |

Report what you ran and what each command returned. Do not report a check you skipped as passed.

## The two boundaries

The import check enforces two rules. They are part of the design, not style.

1. **The core never imports a domain package.** `pydantic_ai_factory` is generic. `pydantic_ai_factory_software` depends on it, never the reverse.
2. **Core layers import downwards only.** From highest to lowest: `cli`, `run`, `factory`, `lines`, `methodology`, `stations`, `gates`, `artifacts`, `contracts`. A layer may use the ones below it.

If a change needs to break a rule, the design is wrong somewhere. Raise it before writing the code.

## The shape of a pull request

Keep each pull request to one idea. A good one can be read in a single sitting.

Describe it in this order:

1. **What and why.** A plain summary a newcomer can follow.
2. **How it was proven.** The commands you ran and what they returned.
3. **Technical detail for the reviewer**, folded away.

Each pull request also includes:

- tests named after the behaviour they prove;
- the docs page for what changed, with code samples that run;
- a line in `CHANGELOG.md` for anything a user can see.

## Writing documentation

Pages live in `docs/` as Markdown, beside the code. The site in `site/` renders them. You need Node 22 or later.

```bash
npm --prefix site ci        # once
npm --prefix site run dev   # preview at http://localhost:4321/pydantic_ai_factory/
```

Four rules keep the docs honest.

1. **Open with a plain summary.** A reader should be able to stop after the first paragraph.
2. **Every Python sample runs, and its output is checked.** `uv run pytest` executes each fenced `python` block in `docs/` and the READMEs, and compares every `#>` comment with what the line above printed. Run `uv run pytest --update-examples` to rewrite those comments after an intended change. A block that is only a sketch opts out with `python test="skip"` on its opening fence, and the page says why. Samples are found in `.md` pages only, so keep Python out of `.mdx` pages; a test fails if one appears.
3. **Link by file path.** Write `[concepts](concepts.md)`. The site turns it into a route, and the link also works on GitHub.
4. **Draw with Mermaid.** A fenced `mermaid` block becomes a diagram.

A new page needs a `title` and `description` in its front matter, and an entry in the sidebar in `site/astro.config.mjs`.

## Keeping private text out

The keep-out scan refuses three kinds of text: a personal home directory, a credential, and any private term the maintainer lists. The private list is never stored in the repository. It is read from the `KEEP_OUT_TERMS` environment variable or from `~/.config/pydantic-ai-factory/keep-out.txt`.

A finding names the file, the line and the kind of rule. It never prints the private term. When a term is in a file's path, the path is withheld too, so the output is safe in a public log. Add `--reveal` on your own machine to see those paths.

A term matches whole words, ignoring case. Prefix it with `cs:` to match case. List joined forms, such as `AcmeClient`, as their own terms.

CI always runs the generic rules. It checks private terms only when the `KEEP_OUT_TERMS` repository secret is set, and its log says how many terms it loaded. Run the scan locally before every push.

If you have a private list, make the scan fail when it cannot be loaded:

```bash
uv run python scripts/keep_out_scan.py --require-terms
```

A line that must contain a matching string, such as a test fixture, can end with `keep-out: allow`. A comment closer such as `-->` may follow it.

## Working with Pydantic AI

Prefer what Pydantic AI already provides. Check the API against the version in `uv.lock` before you build on it. [AGENTS.md](https://github.com/hnrly/pydantic_ai_factory/blob/main/AGENTS.md) lists the practices this project holds itself to.

## Reporting a bug

Include the version, your platform, the command you ran with secrets removed, what you expected and the smallest example that shows the problem. For anything sensitive, follow [the security policy](https://github.com/hnrly/pydantic_ai_factory/blob/main/SECURITY.md) instead of opening an issue.
