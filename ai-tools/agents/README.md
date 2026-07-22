# Sub-agents

Canonical, vendor-agnostic sub-agent definitions. `ai-tools/scripts/setup-ai-tools.mjs` reads every
`*.yaml` here and emits per-vendor copies:

| Vendor | Generated path | Format |
|---|---|---|
| Claude Code | `.claude/agents/<name>.md` | markdown + frontmatter |
| Cursor | `.cursor/agents/<name>.md` | markdown + frontmatter |
| VS Code Copilot | `.github/agents/<name>.agent.md` | markdown + frontmatter |
| Codex | `.codex/agents/<name>.toml` | TOML |

**Edit the YAML here, never the generated vendor files** — they are overwritten on the next setup
run.

## Schema

Validated by
[`vinta-ai-workflows/schemas/sub-agent.v1.schema.json`](../../node_modules/vinta-ai-workflows/schemas/sub-agent.v1.schema.json).
Add the `yaml-language-server` comment at the top of each file for editor validation.

```yaml
# yaml-language-server: $schema=./node_modules/vinta-ai-workflows/schemas/sub-agent.v1.schema.json
schema_version: 1                 # required
name: <kebab-case>                # required, must match the filename stem
description: <text>               # required; when the agent should be used, and what it never does
access: read-only | read-write    # required; drives each vendor's default tool grant
body: |                           # required; markdown, as a YAML literal block
  # Agent Name
  ...

# Optional
claude-tools: <comma-separated>   # overrides the Claude default derived from `access`
model: <model id>
is_background: true | false
overrides:
  claude:  { tools: ... }
  cursor:  { model, readonly, is_background }
  copilot: { tools: [...], model, user-invocable, disable-model-invocation }
  codex:   { sandbox_mode, model, model_reasoning_effort }
```

## Agents in this project

| Agent | Access | Role |
|---|---|---|
| `implementer` | read-write | Writes one phase of an `ai-plans/` plan |
| `reviewer` | read-only | Adversarially reviews one phase, emits BLOCKER / SHOULD-FIX / NIT |
| `fixer` | read-write | Applies one reviewer finding or one named failure |

No stack specialists. The `python-package` stack notes call for none — the foundation trio covers a
library of this shape.

## Conventions every agent body here encodes

These come from `ai-tools/AGENTS.md`; they are repeated inline in each body so an agent has them
without a second read:

- **Sync/AsyncIO parity** — every behavior change lands in both service classes, both backend base
  classes, both fakes, and both test cases.
- **ABC seams are a public contract** — a new `@abstractmethod` breaks every downstream
  `vintasend-*` package at instantiation. Major version bump, coordinated release.
- **Stubs stay complete** — a new seam method gets a working fake in the same commit.
- **Python 3.10 is the floor** — never raise ruff's `target-version` or mypy's `python_version`.
- **No agent creates branches, pushes, or opens PRs.** The orchestrator owns git remotes.
- **No AI co-author trailers.** Commits are attributed to the human author only.
- **Never `git add -A`.** `.claude/`, `package.json`, and `package-lock.json` are untracked and not
  gitignored.

## Adding an agent

1. Write `ai-tools/agents/<new-name>.yaml` following the schema above.
2. Run `node ai-tools/scripts/setup-ai-tools.mjs`.
3. Commit the YAML together with the generated vendor files.
