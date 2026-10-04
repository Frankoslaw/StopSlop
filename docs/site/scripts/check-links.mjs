import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { resolve, relative, sep } from 'node:path';

const dist = resolve('dist');
if (!existsSync(resolve(dist, 'index.html'))) {
  throw new Error('Build the documentation before checking links.');
}

function walk(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name);
    return entry.isDirectory() ? walk(path) : entry.name.endsWith('.html') ? [path] : [];
  });
}

function decode(value) {
  return value.replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
}

// Derive the deployed prefix from generated output, so root and project builds
// are verified without needing to repeat their environment variables.
const home = readFileSync(resolve(dist, 'index.html'), 'utf8');
const canonicalTag = home.match(/<link\b[^>]*rel="canonical"[^>]*>/)?.[0];
const canonicalHref = canonicalTag?.match(/href="([^"]+)"/)?.[1];
if (!canonicalHref) throw new Error('The homepage must have a canonical URL.');
const canonical = new URL(decode(canonicalHref));
const base = canonical.pathname.replace(/\/$/, '');
const files = walk(dist);
const errors = new Set();
const htmlCache = new Map();
let checked = 0;

for (const file of files) {
  const html = readFileSync(file, 'utf8');
  const page = relative(dist, file).split(sep).join('/');
  const pagePath = page === 'index.html' ? '/' : page.replace(/index\.html$/, '');
  const pageUrl = new URL(base + '/' + pagePath.replace(/^\//, ''), canonical.origin);

  // Inspect navigation and asset attributes; remote URLs are intentionally out
  // of scope, keeping this check deterministic and offline.
  for (const match of html.matchAll(/\b(?:href|src)="([^"]+)"/g)) {
    const tagStart = html.lastIndexOf('<', match.index);
    const tagEnd = html.indexOf('>', match.index);
    const tag = html.slice(tagStart, tagEnd + 1);
    // Canonical metadata (notably the framework's /404/ canonical for
    // 404.html) is not a navigable link or fetched asset.
    if (/<link\b/.test(tag) && /\brel="canonical"/.test(tag)) continue;
    const raw = decode(match[1]);
    if (/^(?:data:|mailto:|tel:|javascript:)/i.test(raw)) continue;
    const url = new URL(raw, pageUrl);
    if (url.origin !== canonical.origin) continue;
    checked++;
    if (base && url.pathname !== base && !url.pathname.startsWith(base + '/')) {
      errors.add(`${page}: path escapes deployment base: ${raw}`);
      continue;
    }
    const pathname = decodeURIComponent(url.pathname.slice(base.length)).replace(/^\//, '');
    let target = resolve(dist, pathname);
    const inside = relative(dist, target);
    if (inside.startsWith('..') || inside.includes(':')) {
      errors.add(`${page}: path escapes output directory: ${raw}`);
      continue;
    }
    if (existsSync(target) && statSync(target).isDirectory()) target = resolve(target, 'index.html');
    if (!existsSync(target)) {
      errors.add(`${page}: missing target: ${raw}`);
      continue;
    }
    if (url.hash && target.endsWith('.html')) {
      if (!htmlCache.has(target)) {
        htmlCache.set(target, new Set([...readFileSync(target, 'utf8').matchAll(/\bid="([^"]+)"/g)].map((m) => decode(m[1]))));
      }
      if (!htmlCache.get(target).has(decodeURIComponent(url.hash.slice(1)))) {
        errors.add(`${page}: missing fragment: ${raw}`);
      }
    }
  }
}

if (errors.size) {
  console.error([...errors].join('\n'));
  process.exitCode = 1;
} else {
  console.log(`Checked ${checked} local links/assets across ${files.length} HTML pages at ${base || '/'}.`);
}
