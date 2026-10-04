# StopSlop documentation

Static Astro + Starlight site. Requires Node.js 24+ and pnpm 11.19.0.

```sh
pnpm install --frozen-lockfile
pnpm dev
pnpm check
pnpm build
pnpm check:links
pnpm preview
```

The default local/project path is `/StopSlop/`. `DOCS_SITE` and `DOCS_BASE` override hosting settings; use `DOCS_BASE=/` for root hosting.

Deployment stays off until the repository variable `DOCS_PAGES_ENABLED=true` is set. Select **GitHub Actions** in **Settings → Pages**, then run the **Documentation** workflow. Full launch and custom-domain instructions are in [GitHub Pages](src/content/docs/contributing/github-pages.md); authoring instructions are in [Documentation development](src/content/docs/contributing/documentation.md).

Content lives in `src/content/docs/`. Existing project notes in the parent `docs/` directory remain separate. Commit the lockfile; generated output and dependencies are ignored.
