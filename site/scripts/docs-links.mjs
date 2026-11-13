// Preserve repository-relative Markdown links in source; render site routes in HTML.
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../../', import.meta.url));
const docs = path.join(root, 'docs');
const base = '/pydantic_ai_factory';
export default function docsLinks() {
  return (tree, file) => {
    const source = file.path || file.history?.[0];
    if (!source) return;
    if (tree.children?.[0]?.type === 'heading' && tree.children[0].depth === 1) tree.children.shift();
    function walk(node) {
      if (node.type === 'link' && node.url && !/^(?:[a-z]+:|\/|#)/i.test(node.url)) {
        const [target, fragment] = node.url.split('#');
        const absolute = path.resolve(path.dirname(source), decodeURIComponent(target));
        const relative = path.relative(docs, absolute);
        if (!relative.startsWith('..') && /\.mdx?$/.test(relative)) {
          const slug = relative.replace(/\.mdx?$/, '').replace(/^(index|README)$/, '');
          node.url = `${base}/${slug ? slug + '/' : ''}${fragment ? '#' + fragment : ''}`;
        } else {
          node.url = `https://github.com/hnrly/pydantic_ai_factory/blob/main/${path.relative(root, absolute)}${fragment ? '#' + fragment : ''}`;
        }
      }
      for (const child of node.children || []) walk(child);
    }
    walk(tree);
  };
}
