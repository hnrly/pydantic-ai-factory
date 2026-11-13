# Documentation site

Astro and Starlight render the Markdown and MDX in the repository's `docs/` folder. The pages live beside the code so each pull request changes both together. You need Node 22 or later.

```sh
npm ci
npm run check     # type-check the site
npm run build     # build it, then verify every internal link
npm run dev       # preview at http://localhost:4321/pydantic_ai_factory/
```

## How pages are wired

- `src/content.config.ts` loads `../docs/**/*.{md,mdx}`.
- `scripts/docs-links.mjs` lets pages link to each other by file path, such as `[concepts](concepts.md)`, so links work on GitHub too. It also drops each page's first heading, because Starlight prints the title from the front matter.
- `astro.config.mjs` holds the sidebar. A pull request that adds a page adds its sidebar entry.
- Diagrams are fenced `mermaid` blocks, drawn in the browser in both themes.

Python samples in the docs are run by `tests/test_docs_examples.py` in the main test suite, not by the site build.

## Look

The site carries the hnrly look: black ground, Inter Tight and Geist Mono, and one pink accent. Tokens are in `src/styles/hnrly.css`, code themes in `src/code-theme.mjs`, and the footer in `src/components/Footer.astro`. Keep the "not affiliated with Pydantic" line in the footer.

## Publishing

`.github/workflows/docs.yml` deploys to GitHub Pages from `main`, and only while the repository is public. Pull requests build the site and check its links but publish nothing.
