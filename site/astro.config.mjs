import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://yorch.github.io',
  base: '/harnessbench',
  trailingSlash: 'always',
  build: {
    format: 'directory',
  },
  integrations: [
    starlight({
      title: 'harnessbench',
      description:
        'Compare agentic coding harnesses on token efficiency, task effectiveness, and time efficiency.',
      social: [
        { label: 'GitHub', icon: 'github', href: 'https://github.com/yorch/harnessbench' },
      ],
      head: [
        {
          tag: 'link',
          attrs: {
            rel: 'preconnect',
            href: 'https://fonts.googleapis.com',
          },
        },
        {
          tag: 'link',
          attrs: {
            rel: 'preconnect',
            href: 'https://fonts.gstatic.com',
            crossorigin: 'anonymous',
          },
        },
        {
          tag: 'link',
          attrs: {
            rel: 'stylesheet',
            href: 'https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap',
          },
        },
      ],
      sidebar: [
        { label: 'Getting Started', slug: 'docs/getting-started' },
        { label: 'Architecture', slug: 'docs/architecture' },
        { label: 'Gateway Proxy', slug: 'docs/gateway-proxy' },
        { label: 'Orchestrator', slug: 'docs/orchestrator' },
        { label: 'Docker Runner', slug: 'docs/docker-runner' },
        { label: 'Evaluators', slug: 'docs/evaluators' },
        { label: 'Adapters', slug: 'docs/adapters' },
        { label: 'CLI Reference', slug: 'docs/cli-reference' },
        { label: 'Configuration', slug: 'docs/configuration' },
        { label: 'Reporting', slug: 'docs/reporting' },
        { label: 'Statistics', slug: 'docs/statistics' },
        { label: 'Development', slug: 'docs/development' },
      ],
      customCss: ['./src/styles/custom.css'],
    }),
  ],
});
