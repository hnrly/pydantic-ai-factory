import { readdir, readFile, stat } from 'node:fs/promises';
import path from 'node:path';
const directory = path.resolve('dist');
const prefix = '/pydantic_ai_factory/';
const errors = [];
async function walk(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) { await walk(file); continue; }
    if (!file.endsWith('.html')) continue;
    const html = await readFile(file, 'utf8');
    for (const match of html.matchAll(/(?:href|src)="([^"]+)"/g)) {
      const url = match[1].split(/[?#]/)[0];
      if (!url) continue; // a link to a heading on the same page
      if (/^(?:[a-z]+:|\/\/)/i.test(url)) continue;
      if (url.startsWith('/') && !url.startsWith(prefix)) {
        errors.push(`${file}: missing site prefix: ${url}`); continue;
      }
      let target = url.startsWith(prefix)
        ? path.join(directory, decodeURIComponent(url.slice(prefix.length)))
        : path.resolve(path.dirname(file), decodeURIComponent(url));
      try {
        if ((await stat(target)).isDirectory()) target = path.join(target, 'index.html');
        await stat(target);
      } catch { errors.push(`${file}: missing ${url}`); }
    }
  }
}
await walk(directory);
if (errors.length) throw new Error(errors.join('\n'));
console.log('Built documentation links and subpath assets verified.');
