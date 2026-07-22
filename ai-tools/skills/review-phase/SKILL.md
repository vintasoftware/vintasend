---
name: review-phase
description: Internal review gate of [implement-plan] / [amend-plan] / [systematic-debugging] — NOT a standalone entry point. Runs the mandatory three-layer review (mechanical checks, plan-compliance walkthrough, independent reviewer subagent) plus the fix loop against one phase's diff in vintasend, spawning reviewer + fixer subagents and looping until all three layers are clean. The invoking conductor passes the diff, the phase body to walk against, and the resolved `WORKROOT`; do not invoke directly to "review my code" — use the project's standard code-review path for that.
---

# Review one phase

The single review implementation shared by every plan-execution conductor: [implement-plan](../implement-plan/SKILL.md) (after an implementer runs), [amend-plan](../amend-plan/SKILL.md) (after a rewrite), and [systematic-debugging](../systematic-debugging/SKILL.md) (against the fix diff). Read-only orchestration: this skill **never edits code** — every issue becomes a fix-up subagent task.

## Inputs (passed by the conductor)

- The phase diff (on the current branch inside `WORKROOT`).
- The phase body to walk against (the **new** body when invoked by amend-plan).
- `WORKROOT`, `SANDBOX_TIER` — resolved once by the conductor.
- `main_checkout` — the repo root the run was invoked from (equals `WORKROOT` when no worktree).
- `run_options.full_test_suite` — resolves which outer gate Layer 1 item 3 verifies ran (false = scoped suite; true = full repo suite).
- The project's `reviewer` + `fixer` agent types, plus their `agent_models.reviewer` / `agent_models.fixer` tiers (when set in `.vinta-ai-workflows.yaml`).
- Optional per-phase `reviewer_model_tier` / `fixer_model_tier` overrides — the tiers parsed from this phase's `**Review models**:` line in the plan (null when the phase didn't set one, which is the common case).

## Resolve the reviewer + fixer model

Each of `reviewer` and `fixer` spawns at an **effective tier**, resolved per role with this precedence:

1. The phase's `reviewer_model_tier` / `fixer_model_tier` override, when the conductor passed one (the plan chose a non-default review model for this critical phase).
2. Else the project-wide `agent_models.reviewer` / `agent_models.fixer` tier from `.vinta-ai-workflows.yaml`.
3. Else unset → the runtime default model.

Feed that effective tier into the resolution below (it turns a tier into a concrete spawn model). A phase override applies to that phase only; the next phase falls back to the `agent_models` default unless it too overrides.

## Resolve an `agent_models` tier to a spawn model

`.vinta-ai-workflows.yaml` may carry an `agent_models` section mapping a role/task (`reviewer`, `fixer`, `worktree_prep`, `integrate`) to a **tier** (1–4) into the same table the per-phase implementer suggestion uses — [`ai-tools/skills/plan-feature/resources/ai-models.yaml`](../plan-feature/resources/ai-models.yaml). `agent_models` is the **project default** for these roles; for `reviewer` / `fixer`, a plan phase's optional `**Review models**:` line may override the tier for that one phase (the caller resolves that precedence and hands this block the effective tier). The mechanical steps (`worktree_prep`, `integrate`) are never plan-named. To turn a tier into the model a spawn actually uses:

1. Determine the **effective tier** for the role: for `reviewer` / `fixer`, a per-phase override the conductor passed wins over `agent_models.<role>`; for the mechanical steps, it's simply `agent_models.<role>`.
2. **No effective tier (override absent AND key unset, or the whole `agent_models` section absent) → do not force a model.** Spawn with the runtime's default model (today's behavior). Skip the rest.
3. Open [`ai-tools/skills/plan-feature/resources/ai-models.yaml`](../plan-feature/resources/ai-models.yaml), take that tier's `models`, **filter to the vendors the runtime actually exposes**, pick the cheapest/fastest survivor, and translate it to the runner's spawn form — the same resolution [implement-phase](../implement-phase/SKILL.md) runs for the implementer, only keyed by a config tier instead of a plan line.
4. `ai-models.yaml` missing, or the tier has no runtime-available vendor → fall back to the runtime default and surface the fallback once. Never hard-fail a phase over a model-selection miss.

Record the **model actually used** in tracking: for `reviewer` / `fixer`, alongside the review note; for the mechanical steps, in the phase's tracking row next to the branch/PR fields.

## Review

Three layers, all required, in order. The reviewing orchestrator never edits — every issue surfaces as a fix-up subagent task.

## Layer 1 — Mechanical checks

1. `git -C <WORKROOT> status` + `git -C <WORKROOT> diff --stat`: confirm the file list matches the agent's report.
2. **Read the full diff** for every changed file using `git -C <WORKROOT> diff`. Spot-checking is not enough.
3. **Verify the outer gate** ran + green. By default that is `poetry run mypy` (repo-wide) AND the scoped suite ``poetry run pytest vintasend/tests/test_services/` (the touched test dir)` covering the touched apps. {If run_options.full_test_suite = true:} the outer gate runs `poetry run mypy` AND the full `poetry run pytest` instead — verify that. Look in the report for explicit confirmation the applicable gate was executed + passed. Vague confirmation → **re-run yourself** (in `<WORKROOT>`).
4. **Scope creep**: file touched outside the expected surface area? Unrelated formatting churn? Surface it.
5. **No-secrets scan**: `git -C <WORKROOT> diff` for `password|secret|token|api_key|AKIA|BEGIN [A-Z]+ KEY`.
6.**Stray main-checkout writes — only when `WORKROOT != <main_checkout>` (i.e. a worktree run).** A subagent told to work inside the worktree can resolve an absolute path back to the **main checkout** and silently edit files there; because worktrees have independent working trees, those edits never reach the phase commit — they sit as uncommitted thrash in the main checkout and read as a silent implementer/fixer failure. **When `SANDBOX_TIER = enforced`, the OS sandbox already blocks these writes and this becomes a cheap backstop (a clean `git status` is the expected result). When `SANDBOX_TIER = none`, it is the *only* defense — run it religiously.** After **every** implementer **and** fixer subagent returns, run:

```bash
git -C <main_checkout> status --short | grep -vE '^\?\?'   # tracked modifications only
```

Any output is a BLOCKER for this phase:
- Diff the stray files (`git -C <main_checkout> diff -- <path>`) to recover intent.
- If the edit belongs in the worktree, re-dispatch the fixer/implementer with an explicit instruction to write to `WORKROOT` (the change is missing from the phase commit until it does).
- Once recovered (or confirmed superseded by the correctly-committed worktree version), discard the stray edits with `git -C <main_checkout> restore -- <path>` so the main checkout returns clean. Never leave the main checkout dirty between phases — a later phase can't tell new thrash from old.

`<main_checkout>` is the repo root the skill was invoked from (NOT `WORKROOT`). When `WORKROOT == <main_checkout>` (`use_worktree = false`), skip this check entirely — your work legitimately lives in that tree.
6. **Dependency license scan**: `git -C <WORKROOT> diff pyproject.toml poetry.lock` — for every added dep look up its SPDX license (PyPI project page, `poetry run pip show <pkg>`, upstream `LICENSE`). A license in `policies.dependency_licenses.forbidden_spdx` and not in `allowed_overrides` is a BLOCKER. A missing / `UNKNOWN` / undeclared license is **always a BLOCKER** — there is no override that silently blesses undisclosed terms. Any new entry under `[tool.poetry.dependencies]` (as opposed to the dev group) is a BLOCKER pending human approval regardless of license: the runtime dep set is deliberately three packages.
7. **Co-author trailer scan**: `git -C <WORKROOT> log --format=%B <BASE_BRANCH>..HEAD | grep -i 'co-authored-by'` — any hit is a BLOCKER. This repo attributes commits to the human author only; the offending commit must be amended.

## Layer 2 — Plan compliance walkthrough

Open the phase body alongside the diff and walk:

1. **Every numbered "Changes" item implemented.**
2. **Every "Tests" entry materialized**, with assertions actually exercising the called-out behavior.
3. **Acceptance line satisfiable** by the diff.
4. **Repo conventions** from AGENTS.md.
5. **Reusable-skill compliance.**

6. **Feature-flag wiring** if the plan's **Guiding Decisions** declared a flag — flag-OFF is byte-for-byte pre-feature behavior, ≥1 test asserts it.
7. **Cross-phase consistency** with prior tracking summaries.
8. **Comment hygiene** — new or changed comments and doc blocks in the diff read as Simple English, one idea per sentence, no AI-slop vocabulary or negative framing, per the `deslop-comments` skill ([ai-tools/skills/deslop-comments/SKILL.md](ai-tools/skills/deslop-comments/SKILL.md)). Flag any that need rewriting; the fix loop dispatches a fixer to run `deslop-comments` on the phase's touched files.

## Layer 3 — Independent reviewer subagent

After Layers 1–2 pass, spawn a **separate** subagent (different session, no implementation context) using the project's `reviewer` agent type ([ai-tools/agents/reviewer.md](ai-tools/agents/reviewer.md)) at the model resolved from `agent_models.reviewer` (see the [Resolve the reviewer + fixer model](#resolve-the-reviewer--fixer-model) step; unset → runtime default). Read-only by design.

Reviewer prompt template — see the reviewer agent's body for the standard form. Triage findings:
- **BLOCKER**: must fix before the phase is pushed (the conductor's integrate step).
- **SHOULD-FIX**: fix in-phase if cheap; else follow-up issue + tracking note.
- **NIT**: ignore unless trivially cheap.

The reviewer also applies a condensed **structural-simplification lens** — one question: is there an obvious "code-judo" reframe that would make whole branches, helpers, modes, or layers disappear, rather than polishing what's there? Routine phases stop at that one question. When the phase touched core architecture, pushed a file past ~1,000 lines, or the lens surfaces a structural smell too big to resolve inline, escalate to the full [thermo-nuclear-code-quality-review](ai-tools/skills/thermo-nuclear-code-quality-review/SKILL.md) skill against the phase diff — an opt-in deep audit, run deliberately, never on every phase.

The reviewer finds nothing on a >300-LoC multi-file phase → suspicious. Read once more.

## Fix loop

1. Spawn a **new** subagent — the project's `fixer` agent type ([ai-tools/agents/fixer.md](ai-tools/agents/fixer.md)) at the model resolved from `agent_models.fixer` (see the [Resolve the reviewer + fixer model](#resolve-the-reviewer--fixer-model) step; unset → runtime default). The fix prompt quotes the finding verbatim. For comment-hygiene findings (Layer 2 item 8), the fix prompt tells the fixer to run the `deslop-comments` skill ([ai-tools/skills/deslop-comments/SKILL.md](ai-tools/skills/deslop-comments/SKILL.md)) scoped to the phase's touched files — comment-only edits, no behavior change.
2. The `fixer`'s system prompt mandates re-running the inner loop + outer gate (in `<WORKROOT>`).
3. After the fixer returns, redo Layer 1 in full + the affected portion of Layer 2.
4. Loop until Layers 1, 2, 3 are all clean.

## Output

Return to the conductor: `PASS` (all three layers clean) with a one-line note, or the list of BLOCKER / SHOULD-FIX findings and what the fix loop applied. The conductor owns branch / push / PR — this skill hands back a clean (or annotated) working tree in `WORKROOT`.
