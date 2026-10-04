import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// Actions supplies the repository name so forks deploy without source edits.
const repository = process.env.GITHUB_REPOSITORY || 'Frankoslaw/StopSlop';
const [owner, name] = repository.split('/');
const site = process.env.DOCS_SITE || `https://${owner.toLowerCase()}.github.io`;
const base = process.env.DOCS_BASE || (name.toLowerCase() === `${owner.toLowerCase()}.github.io` ? '/' : `/${name}`);

export default defineConfig({
  site,
  base,
  output: 'static',
  trailingSlash: 'always',
  integrations: [starlight({
    title: 'StopSlop',
    description: 'Policy enforcement, token budgets, and authenticated agent permissions for text chat.',
    favicon: '/favicon.svg',
    social: [{ icon: 'github', label: 'GitHub', href: `https://github.com/${repository}` }],
    editLink: { baseUrl: `https://github.com/${repository}/edit/main/docs/site/` },
    customCss: ['./src/styles/custom.css'],
    sidebar: [
      { label: 'Start here', items: [
        { label: 'Introduction', slug: 'start/introduction' },
        { label: 'Quickstart', slug: 'start/quickstart' },
        { label: 'Supported usage', slug: 'start/compatibility' },
      ] },
      { label: 'Integrations', items: [{ autogenerate: { directory: 'integrations' } }] },
      { label: 'Policy and routing', items: [{ autogenerate: { directory: 'policies' } }] },
      { label: 'Operations', items: [{ autogenerate: { directory: 'operations' } }] },
      { label: 'Reference', items: [{ autogenerate: { directory: 'reference' } }] },
      { label: 'Contributing', items: [{ autogenerate: { directory: 'contributing' } }] },
    ],
  })],
});
