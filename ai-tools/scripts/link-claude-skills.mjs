#!/usr/bin/env node
// Link ai-tools/skills/<name> into .claude/skills/<name>, one symlink per skill.
//
// Why this exists: setup-ai-tools.mjs wants to symlink the whole `.claude/skills`
// directory at ai-tools/skills. It can't here, because `.claude/skills/` is a real
// directory already holding the `vinta-*` skills installed by the vinta-ai-workflows
// CLI (each itself a symlink into node_modules). setup-ai-tools.mjs correctly refuses
// to clobber a real directory and prints "exists and is not a symlink — leaving as-is",
// which would otherwise leave Claude Code unable to see any project skill.
//
// So we link per-skill instead. Both sets coexist:
//   .claude/skills/vinta-*      -> ../../node_modules/vinta-ai-workflows/skills/vinta-*
//   .claude/skills/<project>    -> ../../ai-tools/skills/<project>
//
// Idempotent. Prunes stale project-skill links whose ai-tools/skills source is gone.
// Never touches vinta-* entries or anything that is not a symlink.

import { existsSync, lstatSync, mkdirSync, readdirSync, readlinkSync, symlinkSync, unlinkSync } from 'node:fs';
import { join } from 'node:path';

const SRC = 'ai-tools/skills';
const DEST = '.claude/skills';
const LINK_PREFIX = '../../ai-tools/skills/';

if (!existsSync(SRC)) {
  console.error(`[link-claude-skills] ${SRC} not found — run from the repo root.`);
  process.exit(1);
}
mkdirSync(DEST, { recursive: true });

const skills = readdirSync(SRC, { withFileTypes: true })
  .filter((e) => e.isDirectory() && existsSync(join(SRC, e.name, 'SKILL.md')))
  .map((e) => e.name);

let linked = 0;
let pruned = 0;

for (const name of skills) {
  const linkPath = join(DEST, name);
  const target = LINK_PREFIX + name;

  let st = null;
  try {
    st = lstatSync(linkPath);
  } catch {
    /* absent */
  }

  if (st) {
    if (!st.isSymbolicLink()) {
      console.warn(`[link-claude-skills] ${linkPath} is a real path, not a symlink — leaving as-is`);
      continue;
    }
    if (readlinkSync(linkPath) === target) continue; // already correct
    unlinkSync(linkPath);
  }

  symlinkSync(target, linkPath);
  linked++;
}

// Prune project-skill links whose source no longer exists. Only ever removes symlinks
// that point into ai-tools/skills, so vinta-* links are untouched by construction.
for (const entry of readdirSync(DEST)) {
  const p = join(DEST, entry);
  if (!lstatSync(p).isSymbolicLink()) continue;
  const t = readlinkSync(p);
  if (!t.startsWith(LINK_PREFIX)) continue;
  if (!skills.includes(entry)) {
    unlinkSync(p);
    pruned++;
  }
}

console.log(
  `[link-claude-skills] ${skills.length} project skill(s): ${linked} link(s) written, ${pruned} stale link(s) pruned.`,
);
