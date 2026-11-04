// Every internal link goes through here so the GitHub Pages prefix (`base` in
// astro.config.mjs) is applied exactly once, with exactly one slash.
const base = import.meta.env.BASE_URL.replace(/\/+$/, "");

export function withBase(path: string): string {
  if (path.startsWith("#")) return path;
  return `${base}/${path.replace(/^\/+/, "")}`;
}

export const GITHUB_PY = "https://github.com/vintasoftware/vintasend";
export const GITHUB_TS = "https://github.com/vintasoftware/vintasend-ts";
export const PYPI = "https://pypi.org/project/vintasend/";
export const NPM = "https://www.npmjs.com/package/vintasend";
