---
title: Documentation development
description: Build and maintain the Astro Starlight site, verify links, and keep docs aligned with code.
sidebar:
  order: 1
---

The site lives in `docs/site`, separate from the Python workspace and existing project notes in `docs/`. It uses Astro and Starlight with a static build, local full-text search, accessible navigation, syntax highlighting, light/dark themes, and per-page contents.

## Local development

Install Node.js 24 or newer and pnpm 11.19.0. From the repository root:

```sh
cd docs/site
pnpm install --frozen-lockfile
pnpm dev
```

Open the URL printed by Astro. The default base path is `/StopSlop/`, matching the project Pages URL. For a root-path local preview set `DOCS_BASE=/` before starting.

## Validate changes

```sh
pnpm check
pnpm build
pnpm check:links
pnpm preview
```

The link check inspects generated HTML for missing local pages, assets, and fragment targets, including the configured base path. It does not test remote links or run provider requests. Run it after each build. Pull requests run the same checks without deploying.

## Add or update content

Create a Markdown or MDX file under `src/content/docs/`, with `title` and `description` frontmatter. Integration, policy, operations, reference, and contributor sidebars are generated from those directories. Use `sidebar.order` for deliberate ordering.

Use relative links with trailing slashes so pages work under both project and custom-domain base paths. Avoid hard-coded `/StopSlop/` in content. Code examples should state required dependencies, model approvals, credentials, and any cost or storage behavior relevant to the task.

## Content structure

The docs follow the separation commonly used by SDK and CLI documentation:

- **Start here:** a first successful request and compatibility boundaries.
- **Integrations:** copyable application patterns.
- **Policy and routing:** focused task guides with behavior and tradeoffs.
- **Operations:** deployment, observability, storage, and recovery.
- **Reference:** exact flags, fields, defaults, and error contracts.

Keep reference tables precise and guides task-oriented. When changing settings, supported request fields, rule schemas, quotas, permissions, or CLI arguments, update the corresponding reference and examples in the same change. Provider defaults should be described as source defaults rather than recommendations or availability guarantees.

## Dependency updates

Versions are recorded in `package.json` and `pnpm-lock.yaml`. Update them together, review peer compatibility, and rerun type checking, builds, and link validation. The workspace explicitly allows esbuild's install script. Generated `.astro/`, `dist/`, and `node_modules/` are not committed.

See [GitHub Pages](../github-pages/) to enable publishing when the project launches. Framework references: [Starlight](https://starlight.astro.build/) and [Astro Pages deployment](https://docs.astro.build/en/guides/deploy/github/).
