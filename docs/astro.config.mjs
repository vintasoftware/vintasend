// @ts-check
import { defineConfig } from "astro/config";
import mdx from "@astrojs/mdx";

// Published at https://vintasoftware.github.io/vintasend/ by .github/workflows/pages.yml.
// `base` is what makes every internal link carry the repository prefix; use the
// `withBase()` helper in src/lib/urls.ts rather than hardcoding "/vintasend/".
export default defineConfig({
  site: "https://vintasoftware.github.io",
  base: "/vintasend",
  trailingSlash: "always",
  integrations: [mdx()],
  markdown: {
    shikiConfig: {
      theme: "github-dark-default",
      wrap: false,
    },
  },
});
