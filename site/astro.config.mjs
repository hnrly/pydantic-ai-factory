import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import mermaid from 'astro-mermaid';
import { unified } from '@astrojs/markdown-remark';
import docsLinks from './scripts/docs-links.mjs';
import { hnrlyDark, hnrlyLight } from './src/code-theme.mjs';

export default defineConfig({
  site: 'https://hnrly.github.io',
  base: '/pydantic_ai_factory',
  trailingSlash: 'always',
  vite: { resolve: { dedupe: ['@astrojs/starlight'] } },
  markdown: { processor: unified({ remarkPlugins: [docsLinks] }) },
  integrations: [
    // Diagrams are written as ```mermaid blocks and drawn in the browser, in both themes.
    mermaid({ autoTheme: true }),
    starlight({
      title: 'Pydantic AI Factory',
      description: 'Composable, evidence-driven AI factories built on Pydantic AI.',
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/hnrly/pydantic_ai_factory' }],
      // hnrly look: tokens in src/styles/hnrly.css, fonts as on hnrly.com.
      customCss: ['./src/styles/hnrly.css'],
      components: { Footer: './src/components/Footer.astro' },
      credits: false,
      expressiveCode: { themes: [hnrlyDark, hnrlyLight] },
      head: [
        { tag: 'link', attrs: { rel: 'preconnect', href: 'https://fonts.googleapis.com' } },
        { tag: 'link', attrs: { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: true } },
        { tag: 'link', attrs: { rel: 'stylesheet', href: 'https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap' } },
        { tag: 'meta', attrs: { name: 'author', content: 'hnrly' } },
      ],
      markdown: { processedDirs: ['../docs'] },
      // One entry per page. A pull request that adds a page adds its entry here.
      sidebar: [
        { label: 'Start here', items: [{ label: 'Overview', slug: '' }, 'concepts'] },
        { label: 'The contract', items: ['artifacts', 'stations', 'gates'] },
        { label: 'Project', items: ['decisions', 'contributing'] },
      ],
    }),
  ],
});
