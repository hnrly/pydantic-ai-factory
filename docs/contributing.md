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
| `npm --prefix site run build` | Every diagram parses, the docs site builds and every internal link resolves. |

Report what you ran and what each command returned. Do not report a check you skipped as passed.

### Against a real model

Every test above uses a scripted model. Before a release, the maintainer also runs the getting-started path against a real model:

```bash
ANTHROPIC_API_KEY=... uv run pytest -m live
```

Tests marked `live` are skipped unless asked for with `-m live`. They make real, paid requests to `anthropic:claude-opus-5-5`. Report the date, the model and what each test returned.

## The two boundaries

The import check enforces two rules. They are part of the design, not style.

1. **The core never imports a domain package.** `pydantic_ai_factory` is generic. `pydantic_ai_factory_software` depends on it, never the reverse.
2. **Core layers import downwards only.** From highest to lowest: `cli`, `methodologies`, `stores`, `factory`, `stations`, `_methodology`, `gates`, `artifacts`, `contracts`. A layer may use the ones below it.

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
npm --prefix site run dev   # preview at http://localhost:4321/pydantic-ai-factory/
```

Five rules keep the docs honest, and close to the style of Pydantic AI's own docs.

1. **Open with a plain summary.** A reader should be able to stop after the first paragraph.
2. **Every Python sample runs, and its output is checked.** Samples name a real model, `anthropic:claude-opus-5-5`, and call `run_sync`, as a reader would. The test suite swaps in a scripted model for every agent, so the samples run with no key and always print the same thing; each page's replies are in `tests/scripted.py`. Each sample is complete on its own, with its imports. `uv run pytest` executes each fenced `python` block in `docs/` and the READMEs, and compares every `#>` comment with what the line above printed. Run `uv run pytest --update-examples` to rewrite those comments after an intended change. A block that cannot run, such as one that needs an outside account, opts out with `python test="skip"` on its opening fence, and the page says why. Samples are found in `.md` pages only, so keep Python out of `.mdx` pages; a test fails if one appears.
3. **Link by file path.** Write `[stations](stations.md)`. The site turns it into a route, and the link also works on GitHub.
4. **One worked example.** Every page uses the to-do list web page from [Getting started](getting-started.md). A blank task, or a ticked task that comes back after a reload, drives review, escalation and approval. A bug report about it is the signal example. Do not invent abstract examples.
5. **Concepts and short code, no diagrams.** Open with what the thing is in a sentence or two, show the smallest sample that uses it, then explain variations with smaller samples. Keep tables for things a reader looks up, such as options and outcomes.

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

Prefer what Pydantic AI already provides. Check the API against the version in `uv.lock` before you build on it. [AGENTS.md](https://github.com/hnrly/pydantic-ai-factory/blob/main/AGENTS.md) lists the practices this project holds itself to.

## Reporting a bug

Include the version, your platform, the command you ran with secrets removed, what you expected and the smallest example that shows the problem. For anything sensitive, follow [the security policy](https://github.com/hnrly/pydantic-ai-factory/blob/main/SECURITY.md) instead of opening an issue.

## Releasing

Only the maintainer releases. A release is a tag; GitHub Actions does the rest.

1. Run the `live` tests against a real model, as above, on the commit you will release.
2. In a pull request, rename `## Unreleased` in `CHANGELOG.md` to `## <version>` and set that version in both `pyproject.toml` files. The software package pins the core, with its `methodologies` extra, at the same version. Check it with `uv run python scripts/release_check.py v<version>`.
3. Once it has merged, tag `main` and push the tag: `git tag v<version> && git push origin v<version>`.

The Release workflow checks the tag again, builds both packages and imports the built wheels. It then publishes the core and then the software package to PyPI with trusted publishing, so no PyPI token is stored anywhere. Last, it makes the GitHub release, with the changelog lines as its notes. Running the workflow by hand with a tag is a dry run that publishes nothing.
