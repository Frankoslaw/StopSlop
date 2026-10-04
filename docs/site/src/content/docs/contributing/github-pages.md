---
title: Publish with GitHub Pages
description: Enable the prepared Actions workflow at launch, configure project paths, and use a custom domain.
sidebar:
  order: 2
---

The repository includes `.github/workflows/docs.yml`. Documentation builds are validated on relevant pushes to `main` and pull requests. Deployment is gated by a repository variable so you can prepare the site before the project goes live.

## Enable at launch

1. Push the documentation source, lockfile, and workflow to the repository's `main` branch.
2. In **Settings → Pages**, select **GitHub Actions** as the build and deployment source.
3. In **Settings → Secrets and variables → Actions → Variables**, add `DOCS_PAGES_ENABLED` with value `true`.
4. Open **Actions → Documentation → Run workflow** and run it on `main`.
5. Wait for the build and deploy jobs. The `github-pages` environment links to the deployed site.

With the current repository name, the default URL is `https://frankoslaw.github.io/StopSlop/`. The owner and repository are derived from `GITHUB_REPOSITORY` in Actions, so forks do not need hard-coded URL edits. An owner repository named `OWNER.github.io` automatically uses `/` as its base.

If the default branch changes, update the workflow's `push.branches` and the Starlight edit-link branch in `astro.config.mjs`.

## What the workflow does

The build job checks out source, installs Node and pnpm, uses the frozen lockfile, runs Astro type checks, builds static HTML, and validates local links. When publishing is enabled and the event is not a pull request, it uploads only `docs/site/dist` as the Pages artifact.

The deployment job has `pages: write` and `id-token: write` permissions and uses the `github-pages` environment. The build job only needs repository read permission. Pull requests validate content without deployment credentials.

No gateway runs on Pages: this publishes static documentation only. Credentials, SQLite state, `.env`, and Python runtime files are not part of the artifact.

## Custom domain

Set repository variables:

```text
DOCS_SITE=https://docs.example.com
DOCS_BASE=/
```

Configure the domain and DNS through GitHub Pages settings, then rerun the workflow. `DOCS_SITE` controls canonical URLs and `DOCS_BASE` controls route and asset paths. For a custom domain under a subdirectory, use that subdirectory as the base instead.

No variables are required for the default project URL. Empty values fall back to the derived project settings; set `/` explicitly for root hosting. Do not bake a production custom-domain `CNAME` into `public/` unless it is needed for your hosting setup.

## Check both deployment paths locally

Default project build:

```sh
pnpm build
pnpm check:links
```

Root/custom-domain build in PowerShell:

```powershell
$env:DOCS_SITE = "https://docs.example.com"
$env:DOCS_BASE = "/"
pnpm build
pnpm check:links
Remove-Item Env:DOCS_SITE, Env:DOCS_BASE
```

For POSIX shells: `DOCS_SITE=https://docs.example.com DOCS_BASE=/ pnpm build`, then `pnpm check:links`.

## Disable publishing

Set `DOCS_PAGES_ENABLED` to `false` or remove it. Future runs still validate documentation but skip upload and deployment. This does not remove an already published site; unpublish it separately in Pages settings if needed.

If a deployment fails, check the Pages source, repository variable, environment approvals, branch protection, and organization Actions policies. Asset 404s usually indicate a mismatched base path or stale build. The implementation follows the [official Astro Pages guidance](https://docs.astro.build/en/guides/deploy/github/).
