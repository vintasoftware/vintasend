# VintaSend website

The landing page and documentation published at
<https://vintasoftware.github.io/vintasend/>. One site covers both libraries, `vintasend`
(Python) and `vintasend-ts` (TypeScript): a language switch in the header shows the examples
for one or the other and remembers the choice.

Built with [Astro](https://astro.build) and its MDX integration. No framework on the client: the
page is static HTML, a stylesheet and one small script.

## Working on it

```bash
cd docs
npm install
npm run dev        # http://localhost:4321/vintasend/
npm run build      # writes dist/, what Pages publishes
npm run preview    # serves dist/
```

Node.js 20 or newer. The site has its own `package.json`; nothing here is used by the Python
package or its tests.

## Layout

```
docs/
  astro.config.mjs          site + base URL (the /vintasend/ prefix), MDX, code theme
  src/
    pages/index.astro       the landing page
    pages/dashboard/        the notifications dashboard and API page
    pages/managed-templates/ the managed templates page (lifecycle, composition, its API and dashboard)
    pages/docs/             the docs routes, one per MDX file
    content/docs/*.mdx      the documentation pages
    content.config.ts       the frontmatter schema for those pages
    layouts/                Base (head, nav, footer) and DocsLayout (sidebar, TOC)
    components/             nav, toggles, and the MDX helpers below
    styles/site.css         the stylesheet, shared with the other Vinta landing pages
    scripts/site.ts         theme, language switch, copy buttons, table of contents
```

## Writing a docs page

Add `src/content/docs/<slug>.mdx`; the file name is the URL. Frontmatter:

```yaml
---
title: Attachments
description: One sentence, shown under the title.
section: Guides          # Getting started | Concepts | Guides | Ecosystem
order: 20                # position inside the section
---
```

Every page covers both languages. Three components are available without importing them:

- `<Py>…</Py>` and `<Ts>…</Ts>` show their content for one language only. Leave a blank line
  after the opening tag and before the closing tag so the Markdown inside is parsed.
- `<Sw py="create_notification" ts="createNotification" />` is an inline code name that switches
  with the language.
- `<Callout kind="note|tip|warning" title="…">…</Callout>` is a highlighted box.

Keep headings outside the language blocks when a section applies to both languages, so the
table of contents reads the same in either. Wrap a whole section, heading included, when it
applies to one language only.

Links between pages are absolute with the site prefix: `/vintasend/docs/<slug>/`.

## Publishing

`.github/workflows/pages.yml` builds the site and deploys it on every push to `main` that
touches `docs/`. It can also be run by hand from the Actions tab. The repository's Pages source
must be set to "GitHub Actions" once, in Settings → Pages.
