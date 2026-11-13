// Parse every ```mermaid block in docs/ with the same mermaid version the site draws with.
//
// The site draws diagrams in the reader's browser, so a diagram with a syntax error would
// build cleanly and then show an error box. This check catches that before it ships.
// It checks that each diagram parses. It does not look at the drawing.
//
// Exit code 1 if any diagram fails. Each failure prints as `path:line: message`.
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const docs = join(root, 'docs');

// mermaid expects a browser; jsdom provides enough of one for parsing.
const dom = new JSDOM('<!doctype html><html><body></body></html>', { pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.DOMParser = dom.window.DOMParser;

const { default: mermaid } = await import('mermaid');
mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' });

function pages(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return pages(path);
    return /\.mdx?$/.test(entry.name) ? [path] : [];
  });
}

function diagrams(text) {
  const found = [];
  const lines = text.split(/\r?\n/);
  let start = -1;
  for (let i = 0; i < lines.length; i++) {
    if (start < 0 && /^```mermaid\s*$/.test(lines[i])) start = i;
    else if (start >= 0 && /^```\s*$/.test(lines[i])) {
      found.push({ line: start + 1, source: lines.slice(start + 1, i).join('\n') });
      start = -1;
    }
  }
  if (start >= 0) found.push({ line: start + 1, source: null });
  return found;
}

let count = 0;
const failures = [];
for (const page of pages(docs).sort()) {
  for (const { line, source } of diagrams(readFileSync(page, 'utf8'))) {
    count++;
    const where = `${relative(root, page)}:${line}`;
    if (source === null) {
      failures.push(`${where}: the mermaid block is never closed`);
      continue;
    }
    try {
      await mermaid.parse(source);
    } catch (error) {
      failures.push(`${where}: ${String(error.message ?? error).split('\n').slice(0, 4).join(' | ')}`);
    }
  }
}

for (const failure of failures) console.error(failure);
console.log(`check-diagrams: ${count} diagrams, ${failures.length} failed`);
process.exit(failures.length ? 1 : 0);
