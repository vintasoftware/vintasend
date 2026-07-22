---
name: implement-plan
description: Execute a phased implementation plan from `ai-plans/` in vintasend by orchestrating one subagent per phase (using whatever model the plan suggests and the runtime supports), pushing one stacked branch per phase to GitHub, and tracking progress. Use when the user says "implement the plan", "execute plan X", "start implementation", "run phase N of plan Y", "implement {feature} plan", or asks to drive a `*_IMPLEMENTATION_PLAN.md` file phase-by-phase. NOT for one-off changes, single-file edits, or work that doesn't have an existing plan. Agents open the PR on GitHub through the prs-context file plus the bundled `open-pr.sh` — never a raw `gh pr create`.
---

# Implement Plan

Drive a phased plan in [`ai-plans/`](ai-plans/) to completion. This skill is a **thin conductor**: it parses the plan once, resolves one `WORKROOT`, then runs a fixed three-step pipeline per phase, delegating the real work to focused sub-skills:

1. [implement-phase](../implement-phase/SKILL.md) — compose prompt, pick model, spawn the implementer.
2. [review-phase](../review-phase/SKILL.md) — three-layer review + fix loop.
3. the resolved integrate-phase variant — [integrate-phase-stacked](../integrate-phase-stacked/SKILL.md) when `run_options.commit_strategy_resolved = stacked-branches`, else [integrate-phase-modular](../integrate-phase-modular/SKILL.md) — push the branch + open the PR via context file.

The conductor itself owns only: plan parsing, phase classification, `WORKROOT` resolution, the loop, the progress-tracking file, the pause gate, and the final report. Harness-agnostic — claude-code, OpenAI Codex, Google's runtime, or any framework with a "spawn subagent with model + prompt" primitive.

Execution counterpart to [plan-feature](../plan-feature/SKILL.md). Plan = contract; this skill = build pipeline.

## Working assumptions

- Repo: vintasend (Poetry + Python 3.10-3.14 + pytest + tox + ruff + mypy). Conventions: [AGENTS.md](AGENTS.md).
- Plan files: [`ai-plans/YYYY-MM-DD-FEATURE_NAME_IMPLEMENTATION_PLAN.md`](ai-plans/).
- Lint: `poetry run ruff check .`. Format: `poetry run ruff format .`.
- Type / build gate: `poetry run mypy` — `mypy` IS the repo-wide type gate. This is a pure-Python library with no compile step; `poetry build` only packages and is never part of a phase gate.
- Unit / integration tests: `poetry run pytest` (everything); scope to one file with `poetry run pytest vintasend/tests/test_services/test_notification_service.py`.
- Release: never part of a phase. Publishing happens by tagging `vX.Y.Z`, which triggers `.github/workflows/publish.yml`. See the [release-package](../release-package/SKILL.md) skill.
- Cross-version check: `poetry run tox` runs the suite on 3.10-3.14. Slow — run it when a phase touches typing constructs, syntax, or stdlib behavior.
- Code host: **GitHub**. PR policy: **agents create PRs** — via the prs-context file + `open-pr.sh`, never a raw `gh pr create`.
- **Commit attribution: human author only.** Never add `Co-Authored-By:` trailers or any other AI attribution to a commit message.

## Step 0 — Locate + parse plan

Parse once, reuse for every phase:

1. **Identify plan file.** Ask user which plan (path or feature name). Feature name: `ls ai-plans/` + grep; confirm before proceeding.
2. **Extract structured fields**, in order:
   - **Feature name** + **plan id** — derived from filename's `FEATURE_NAME` portion only: strip `YYYY-MM-DD-` prefix + `_IMPLEMENTATION_PLAN.md` suffix. Kebab variant for branch names.
   - **Goals + Non-goals** section — verbatim, used in every phase prompt.
   - **Guiding Decisions** section — verbatim. Pay attention to: feature flag (key, scope, default, flip-on criterion), storage shape, tenant scoping, API contract decisions.
   - **Data Model Changes** section — keep full body; later phases reference earlier subsections.
   - **Phased Rollout** section — parse into phase records: `{ id, title, goal, body, spec_use_case, suggested_model_tier, reviewer_model_tier, fixer_model_tier, reusable_skills, has_e2e, acceptance, is_cross_repo, is_flag_removal }`. `reviewer_model_tier` / `fixer_model_tier` come from the phase's optional `**Review models**:` line (null when the phase doesn't override — most phases).
   - **Risk & Rollout Notes**, **Open Questions**, **Touch List** sections — keep available; include in phase prompts only when relevant.
3. **Classify each phase**: `is_cross_repo`, `is_flag_removal` — the conductor does NOT auto-execute these (see [Cross-repo phases](#cross-repo-phases) + [Flag-removal phase](#flag-removal-phase-always-out-of-scope)).
4. **Ask the user three opt-in questions** via `AskUserQuestion`. Defaults are project-specific (see below); record every answer in tracking under `run_options`:

   a. **Pause between phases?** *"Do you want me to pause and wait for confirmation after each phase, before starting the next one? Lets you review the diff / branch / PR / tracking summary before moving on."* Options: `Auto-flow (default) — keep going phase to phase`, `Pause between phases — wait for go after each one`.

   b. **Draft inline review comments per phase?** *"On top of the standard PR description, do you want me to scan each phase's diff and add 3–10 inline comments calling out non-obvious decisions (subtle invariants, feature-flag short-circuits, cross-phase coupling, upstream-contract naming)? Off by default — say yes when reviewers will appreciate annotated diffs."* Options: `Yes — include inline comments`, `No — PR description only`.

   c. **Run phases in a worktree?** *"Do you want every phase's subagent to work inside an isolated git worktree (its own runnable copy of the app with its own dev + test DB, env files, docker-compose project name) instead of sharing your main checkout? Lets you keep using `main` for unrelated work while this plan runs; survives parallel plans on the same repo without DB / port / docker collisions. Costs one extra checkout's worth of disk + the time it takes [prepare-worktree](../prepare-worktree/SKILL.md) to provision it."* Options: `No — run in current checkout`, `Yes — provision one shared worktree for the whole plan`. Default = value of `run_options.implement-plan.use_worktree` in `.vinta-ai-workflows.yaml` (`No` when unset).

      When `Yes`: **the same worktree is used for every executable phase** — all phase branches stack inside it. The skill never provisions a second worktree mid-plan. If the user wants per-phase worktrees, that's a different workflow (split the plan into independent plans).

      Skip this question entirely when `foundation_skills.prepare-worktree` is `disabled` in `.vinta-ai-workflows.yaml`: record `run_options.use_worktree = false`; surface a one-line note that worktree isolation is available if the team opts in via `vinta-sync-ai-tools` (run from the `vinta-ai-workflows` CLI).

   d. **Full test suite each phase?** *"Each phase's outer gate always runs the repo-wide type/build gate. For tests, do you want the quick path (run only the scoped suite covering the apps/files that phase touched — faster phases) or the full repo test suite every phase (slower, but guards against regressions in untouched code)? New tests still pass individually in the inner loop either way."* Options: `Quick — scoped tests only each phase (default)`, `Full — run the whole test suite each phase`. Default = value of `run_options.implement-plan.full_test_suite` in `.vinta-ai-workflows.yaml` (`Quick`/false when unset). Records `run_options.full_test_suite` (`true` only for the `Full` answer).

   PR opening itself is **not** asked here — it's governed by the project's PR creation policy captured at bootstrap (see `agents create PRs` above). When that policy = "agents create PRs", the the resolved integrate-phase variant — [integrate-phase-stacked](../integrate-phase-stacked/SKILL.md) when `run_options.commit_strategy_resolved = stacked-branches`, else [integrate-phase-modular](../integrate-phase-modular/SKILL.md) step always opens the PR via [open-pr.sh](../open-pr-from-context/scripts/open-pr.sh) regardless of the comment opt-in.

   c. **Commit strategy?** *"This project's `commit_strategy` is set to `ask`. Pick one for this run: one branch + one PR per phase (stacked), or one branch + one PR for the whole plan with one atomic commit per logical unit (modular)?"* Options: `Stacked branches — one branch + PR per phase`, `Modular commits — atomic commits, one PR for whole plan`. Cache the answer in tracking under `run_options.commit_strategy_resolved`.

5. **Confirm with user before starting.** Show plan path, phase list (id + title + tier + cross-repo/flag-removal flags + e2e flag), phases this skill will execute vs defer, branch naming pattern (depends on `run_options.commit_strategy_resolved` — resolved at Step 0), captured `run_options.pause_between_phases` + `run_options.generate_inline_comments` + `run_options.use_worktree` + `run_options.full_test_suite` + `run_options.commit_strategy_resolved`, and that each phase's PR is opened on GitHub automatically once its review passes.

   Wait for "go". After that, the per-phase pause behavior follows `run_options.pause_between_phases`. Inline-comment drafting follows `run_options.generate_inline_comments`. Worktree isolation follows `run_options.use_worktree`. Outer-gate test scope follows `run_options.full_test_suite`. Commit-strategy behavior follows `run_options.commit_strategy_resolved`.

## Agent models — reviewer, fixer, and mechanical steps

The per-phase **implementer** model stays plan-owned (each phase's `**Suggested AI model**:` line — see [implement-phase](../implement-phase/SKILL.md)). Every *other* model this conductor spawns — the review sub-agents and the mechanical steps — is chosen from `.vinta-ai-workflows.yaml`'s `agent_models` section, never from the plan. Read that section once in [Step 0](#step-0--locate--parse-plan) alongside `run_options`.

## Resolve an `agent_models` tier to a spawn model

`.vinta-ai-workflows.yaml` may carry an `agent_models` section mapping a role/task (`reviewer`, `fixer`, `worktree_prep`, `integrate`) to a **tier** (1–4) into the same table the per-phase implementer suggestion uses — [`ai-tools/skills/plan-feature/resources/ai-models.yaml`](../plan-feature/resources/ai-models.yaml). `agent_models` is the **project default** for these roles; for `reviewer` / `fixer`, a plan phase's optional `**Review models**:` line may override the tier for that one phase (the caller resolves that precedence and hands this block the effective tier). The mechanical steps (`worktree_prep`, `integrate`) are never plan-named. To turn a tier into the model a spawn actually uses:

1. Determine the **effective tier** for the role: for `reviewer` / `fixer`, a per-phase override the conductor passed wins over `agent_models.<role>`; for the mechanical steps, it's simply `agent_models.<role>`.
2. **No effective tier (override absent AND key unset, or the whole `agent_models` section absent) → do not force a model.** Spawn with the runtime's default model (today's behavior). Skip the rest.
3. Open [`ai-tools/skills/plan-feature/resources/ai-models.yaml`](../plan-feature/resources/ai-models.yaml), take that tier's `models`, **filter to the vendors the runtime actually exposes**, pick the cheapest/fastest survivor, and translate it to the runner's spawn form — the same resolution [implement-phase](../implement-phase/SKILL.md) runs for the implementer, only keyed by a config tier instead of a plan line.
4. `ai-models.yaml` missing, or the tier has no runtime-available vendor → fall back to the runtime default and surface the fallback once. Never hard-fail a phase over a model-selection miss.

Record the **model actually used** in tracking: for `reviewer` / `fixer`, alongside the review note; for the mechanical steps, in the phase's tracking row next to the branch/PR fields.

## Delegate a mechanical step to a configured model

Two steps the conductor would otherwise run **inline in its own (usually pricier) session** — provisioning the worktree ([prepare-worktree](../prepare-worktree/SKILL.md)) and integrating a phase (the resolved variant — [integrate-phase-stacked](../integrate-phase-stacked/SKILL.md) or [integrate-phase-modular](../integrate-phase-modular/SKILL.md): push the branch + open/update the PR through the bundled `open-pr.sh`) — are mechanical, precedent-driven work that a cheap model handles fine. The `agent_models.worktree_prep` / `agent_models.integrate` tiers let a project push that work down.

- **Tier set** (`worktree_prep` / `integrate`) → **spawn exactly one subagent** at the [resolved model](#resolve-an-agent_models-tier-to-a-spawn-model), hand it the step's SKILL.md plus the same inputs the conductor would use, and consume its returned report exactly as if the conductor had done the work inline. This subagent is a **labor delegate, not a decision-maker**: the conductor still owns git topology (which branch stacks on which base) and still holds every value the step returns (`WORKROOT` / `BASE_BRANCH` / worktree summary for `worktree_prep`; branch + PR-context path + `status` for `integrate`). The delegate executes and reports those back.
- **Tier unset** → run the step inline in the conductor's own session — today's behavior, no subagent.

Rules that hold **regardless of who runs the step**:

- The **PR-context file + `open-pr.sh` is still the only PR-creation path.** An `integrate` delegate uses the bundled script; it never calls raw `gh pr create` / `glab mr create`.
- The delegate is `read-write` (worktree provisioning writes dirs/DBs; integrate pushes + writes the PR-context file) but **makes no plan or code decisions** — a malformed or failed delegate report is surfaced to the user, never worked around.
- This delegation is **separate from the phase-work sub-agents** (implementer / reviewer / fixer). Those still never branch, push, or open PRs — that prohibition is about code-authoring agents, not the dedicated mechanical delegate the conductor spawns to run the integrate step itself.

## Step 0.5 — Resolve `WORKROOT`

Resolve three values **once**, before any phase runs, and record them in tracking. Every later step uses them as data — no step re-derives worktree state.

| Value | `run_options.use_worktree = false` | `run_options.use_worktree = true` |
|---|---|---|
| `WORKROOT` | the main checkout root (the repo the skill was invoked from) | `<worktree_path>` returned by prepare-worktree |
| `BASE_BRANCH` | `main` | `<worktree_branch>` prepare-worktree created |
| `SANDBOX_TIER` | `none` | `enforced` or `none` (probed by prepare-worktree) |

**When `use_worktree = false`:** set `WORKROOT` = main checkout, `BASE_BRANCH = main`, `SANDBOX_TIER = none`. Make `BASE_BRANCH` current + up to date: `git -C <WORKROOT> checkout main && git -C <WORKROOT> pull --ff-only`. Jump to Step 1.

**When `use_worktree = true`:** run [prepare-worktree](../prepare-worktree/SKILL.md) **once**. This is a mechanical step: when `agent_models.worktree_prep` is set, **delegate it to a subagent** per the [Delegate a mechanical step to a configured model](#delegate-a-mechanical-step-to-a-configured-model) pattern (hand the subagent prepare-worktree's SKILL.md + the inputs below; consume its returned `worktree_path` / `worktree_branch` / `worktree_summary` / `sandbox_tier` report). When the tier is unset, run it inline — the steps below read the same either way:

1. **Inputs.** Plan path (so prepare-worktree can read it for deps / migrations / env / compose churn — see prepare-worktree's **Plan inspection** step), suggested worktree name = `plan-{plan-id-kebab}`, plan-driven mode.
2. **Pre-run sanity.** Confirm no existing worktree at the target path (`git worktree list | grep <name>` — refuse if collision). Confirm `git -C <main_checkout> status` of the main checkout (warn if dirty; defer to prepare-worktree's **Sanity checks** step for the call).
3. **Run prepare-worktree.** Pass the plan file + worktree name. It returns:
   - `worktree_path` → `WORKROOT`.
   - `worktree_branch` → `BASE_BRANCH` (prepare-worktree based it on `origin/main`, so it is already current).
   - `worktree_summary` — `.vinta-ai-workflows/worktrees/<name>.yaml` (read by teardown).
   - `sandbox_tier` → `SANDBOX_TIER`: `enforced` (the [Filesystem sandbox](../prepare-worktree/SKILL.md#step-55--filesystem-sandbox-os-level-write-guard) step found `sandbox-exec` / `bwrap` and will OS-block main-checkout writes) or `none` (no sandbox tool — prevention degrades to the review-phase stray-write backstop).
4. **Persist to tracking.** Write `run_options.worktree_path`, `run_options.worktree_branch`, `run_options.worktree_summary`, `run_options.sandbox_tier` into `ai-plans/TRACKING_{plan-id}.md`. All later phases read them — never re-provision mid-plan.
5. **Report to user.** Quote the prepare-worktree summary back: which dirs symlinked vs copied vs forked; which DB(s) forked + their names; compose project name; teardown command. Hold here until the user confirms (`AskUserQuestion`: `Looks good — start phase 1`, `Stop — let me adjust`).

Failure modes:
- **prepare-worktree returns an error** (disk full, branch exists, DB clone failed) → surface to the user; do NOT fall back to "just run in the main checkout" silently — that defeats the opt-in. Ask: `Retry`, `Run in main checkout instead (flip use_worktree to false)`, `Stop`.
- **User cancels at the confirmation gate** → tear the worktree down (run the teardown command from prepare-worktree's report) before exiting, so the next run starts clean.

**`WORKROOT` topology rule.** Every phase branches off the previous phase (first executed phase off `<BASE_BRANCH>`), and **every** `git` / lint / test / build / migrate call runs with `git -C <WORKROOT>` (or after `cd <WORKROOT>`). When `use_worktree = false`, `WORKROOT` is the main checkout and this is exactly today's in-place behavior; when `true`, `WORKROOT` is the worktree and branches / commits stack inside it, never touching the main checkout's working tree. One uniform path — no per-step worktree branching.

## Step 1 — Per-phase loop

For each phase that's `not is_cross_repo and not is_flag_removal`, in plan order, run the pipeline:

### 1a. Implement

Invoke [implement-phase](../implement-phase/SKILL.md), passing the phase record, the plan-level decisions (**Goals + Non-goals**, **Guiding Decisions**, the relevant **Data Model Changes** subsection), the prior-phase tracking summaries, `run_options.full_test_suite`, and `WORKROOT` / `BASE_BRANCH` / `SANDBOX_TIER`. It returns the implementer's report.

**Model escalation.** implement-phase escalates one tier + retries once on a clear capability gap. After Tier 4 fails, it stops and hands back the failure — update tracking with `❌`, post the report to the user, ask how to proceed. Don't silently re-derive tier.

### 1b. Review

Invoke [review-phase](../review-phase/SKILL.md) against the phase diff, passing the phase body to walk, `WORKROOT`, `main_checkout`, `run_options.full_test_suite`, and the `reviewer` / `fixer` agent types with their `agent_models.reviewer` / `agent_models.fixer` tiers **plus this phase's `reviewer_model_tier` / `fixer_model_tier` overrides (null when the phase didn't set a `**Review models**:` line)**. review-phase prefers a phase override over the `agent_models` default. It loops its three layers + fix loop until clean, then returns `PASS` (or the surfaced findings). Do not proceed to integrate while any layer is red.

### 1c. Integrate

Invoke the resolved integrate-phase variant — [integrate-phase-stacked](../integrate-phase-stacked/SKILL.md) when `run_options.commit_strategy_resolved = stacked-branches`, else [integrate-phase-modular](../integrate-phase-modular/SKILL.md), passing `WORKROOT` / `BASE_BRANCH`, the `agents create PRs` policy, and `run_options.generate_inline_comments`. It pushes the branch and routes the PR through the context file, returning the branch + PR-context path + status. This is a mechanical step: when `agent_models.integrate` is set, run it as a delegated subagent per the [Delegate a mechanical step to a configured model](#delegate-a-mechanical-step-to-a-configured-model) pattern (the delegate pushes + writes the PR-context file + runs `open-pr.sh`, then reports the branch / path / status back); when unset, run it inline. Either way the PR-context file + `open-pr.sh` is the only PR-creation path.

### 1d. Update tracking file

Tracking lives at `ai-plans/TRACKING_{plan-id}.md`. Commit on the **current** phase's branch — deletion in [Step 2](#step-2--final-report).

Schema: feature-name, plan path, started/last-updated dates, optional feature-flag info, **run options** (`pause_between_phases`, `generate_inline_comments`, `full_test_suite`, `use_worktree`, `worktree_path`, `worktree_branch`, `worktree_summary`, `sandbox_tier` — last four only when `use_worktree = true`), {If `run_options.commit_strategy_resolved = modular-commits`:} a top-level `plan_branch:` field {Else (`stacked-branches`): no top-level branch field — the per-phase branch is recorded inline}, completed-phases (with status, model{If `run_options.commit_strategy_resolved = stacked-branches`:}, branch, base{Else (`modular-commits`): nothing — no per-phase branch}, e2e+screenshots if any, 5–15 line summary), current phase, remaining phases, deferred phases.

The conductor writes this from the git diff + the agent's summary — not from the agent's narration.

### 1e. Send brief update to user

One short paragraph: phase N done, branch pushed and its PR opened, what got built, and — when the [Integrate](#1c-integrate) step ran — the PR-context file path with its `status` (`published` + URL when `open-pr.sh` opened the PR; `pending` when the script wasn't run because PR policy = branches only or deps were missing). When `status: pending`, mention how to publish later (`bash ai-tools/skills/open-pr-from-context/scripts/open-pr.sh <path>`). Moving to phase N+1. No long retrospective — the tracking file is the durable record.

### 1f. Per-phase pause gate (opt-in)

`run_options.pause_between_phases = false` (default) → **immediately start the next phase**. Do not wait.

`run_options.pause_between_phases = true` → ask the user via `AskUserQuestion`:

- `Continue — start phase N+1`
- `Pause — stop here, I'll resume later by re-invoking the skill` (conductor exits cleanly; tracking file already records progress so the next invocation resumes mid-plan per [Re-running mid-plan](#re-running-mid-plan)).
- `Stop — abort the plan run` (conductor stops; user decides next steps manually).

Wait for the answer. Don't spawn anything in the meantime. The pause is the user's review window — they may inspect the diff, the branch, the PR-context file, or the tracking file before agreeing to continue.

## Cross-repo phases

Phase in another repo:
1. **Do not implement.**
2. Mark in tracking under "Deferred Phases".
3. Continue to the next in-repo phase. Don't block on cross-repo work.

## Flag-removal phase (always out of scope)

Plan declared a flag → last phase is `Phase N — Remove the {flag-key} feature flag`. This skill **never** executes that phase. Flag removal is gated on real-world soak signal + is the exclusive responsibility of a dedicated flag-removal skill (separate skill).

What this skill does instead:
1. Identify the phase during Step 0; always exclude.
2. Mark in tracking as deferred.
3. End the run with a `/schedule` offer pointing at the dedicated flag-removal skill.
4. Refuse + redirect if the user asks this skill to remove the flag.

## Re-running mid-plan

User invokes the skill against a partially-done plan:

1. Read `ai-plans/TRACKING_{plan-id}.md` if present. Extract `run_options.*` — including `worktree_path` / `worktree_branch` / `worktree_summary` when set. Never re-prompt the Step 0 opt-in questions on resume; the original answers stick.
2. **Worktree resume.** When `run_options.use_worktree = true`:
   - Confirm the worktree still exists (`git worktree list | grep <worktree_path>`). Missing → ask user: `Reprovision (run prepare-worktree again with the same name)`, `Switch to main checkout (flip use_worktree to false for the rest of the run)`, `Stop`.
   - Confirm the worktree summary file still parses; if not, regenerate from the existing worktree state.
   - **Re-probe `SANDBOX_TIER`** (`command -v sandbox-exec || command -v bwrap`) — a resume may run on a different machine than the original provisioning. Update `run_options.sandbox_tier` in tracking before spawning; the implement-phase spawn wrapping follows the re-probed value.
   - All resumed phases use the existing worktree — do not provision a second one.
3. `git -C <WORKROOT> branch -a | grep plan/{plan-id-kebab}` to detect already-pushed phase branches.
4. Cross-reference with the plan's phase list.
5. Confirm the resumption point with the user.

## Step 2 — Final report

After all executable phases complete:

1. **Delete `TRACKING_{plan-id}.md`** on the last phase's branch. Commit. The plan file stays.
2. Send the user a final summary: {If `run_options.commit_strategy_resolved = stacked-branches`:} branches pushed (with bases, in stack order) {Else (`modular-commits`):} the single plan branch `plan/{plan-id-kebab}` with its commit log organized by phase; for UI-flow phases — list of `pr-screenshots/` files (if applicable); deferred phases (cross-repo + flag-removal); next steps for the human. When `run_options.use_worktree = true`: include the worktree path + branch + summary file path + the teardown command (`git worktree remove <path>` + the per-engine drop-db / `docker compose -p <project> down -v` lines from `<worktree_summary>`). Do NOT auto-run teardown — the user may still want the worktree to debug review feedback or land follow-ups.
   Include every PR URL opened during the run, plus any PR-context file left `status: pending` with the command to publish it.
3. Flag-removal phase deferred → end with `/schedule` offer for the dedicated flag-removal skill.

## Important rules

- **Read AGENTS.md** in every phase prompt.
- **Stage explicitly.** No `git add -A`.
- **Subagents work in fresh sessions.** Each phase = a new subagent. Tracking + plan files = the context handoff.
- **Conductor owns git topology.** Phase-work subagents (implementer / reviewer / fixer) commit but never branch, push, or open PRs. The one exception is a **mechanical `integrate` delegate** spawned per `agent_models.integrate` — it exists precisely to run the conductor's integrate step (push + PR via `open-pr.sh`) on a cheaper model, and the conductor still dictates the branch/base topology it uses.
- **No AI co-author trailers.** Never add `Co-Authored-By:` or any other AI attribution to a commit. Commits are attributed to the human author only.
- **Trust the plan's per-phase model suggestion.** Implementer model selection lives in [implement-phase](../implement-phase/SKILL.md); the conductor never re-derives tiers.
- **Reviewer / fixer / mechanical-step models come from `agent_models`, not the plan.** Resolve each configured tier via the [Agent models](#agent-models--reviewer-fixer-and-mechanical-steps) step; an unset key means the spawn uses the runtime default. The plan never names these models.
- **Don't re-implement what a project skill encodes.**

- **Two-tier verification, in order, every phase.** Inner scoped, then the outer gate — enforced inside [implement-phase](../implement-phase/SKILL.md). The outer gate always runs the repo-wide type/build gate; its test scope follows `run_options.full_test_suite` (scoped suite by default, full repo suite when opted in).
- **Three-layer review, every phase, no exceptions** — [review-phase](../review-phase/SKILL.md) is not optional and not inlined here.
- **Orchestrator never edits code.**
- **Feature flags = gates, not toggles for tests.**
- **Never remove a feature flag from this skill.**
- **Stop on Tier-4 failure.**
- **Honor opt-in flags.** `run_options.pause_between_phases` controls the [Per-phase pause gate](#1f-per-phase-pause-gate-opt-in); `run_options.generate_inline_comments` controls whether the resolved integrate-phase variant — [integrate-phase-stacked](../integrate-phase-stacked/SKILL.md) when `run_options.commit_strategy_resolved = stacked-branches`, else [integrate-phase-modular](../integrate-phase-modular/SKILL.md) drafts inline comments (always writes the file when that step runs at all — empty comments when off); `run_options.use_worktree` controls whether the [Resolve WORKROOT step](#step-05--resolve-workroot) provisions a worktree and thus what `WORKROOT` / `BASE_BRANCH` / `SANDBOX_TIER` resolve to; `run_options.full_test_suite` controls the outer-gate test scope ([Implement](#1a-implement) + [Review](#1b-review) Layer 1) — scoped suite by default, full repo suite when `true`.
- **One worktree per plan run.** When `use_worktree = true`, provision once in the [Resolve WORKROOT step](#step-05--resolve-workroot) and reuse for every phase. Never spawn a second worktree mid-plan; never silently fall back to the main checkout on prepare-worktree failure (ask the user).
- **Don't auto-tear-down the worktree.** Step 2 surfaces the teardown command; the user runs it when ready.
- **`WORKROOT` is resolved once, used everywhere.** Every sub-skill takes `WORKROOT` / `BASE_BRANCH` / `SANDBOX_TIER` as data — no step re-derives worktree state. OS-level prevention (sandbox wrap in implement-phase when `SANDBOX_TIER = enforced`) plus the review-phase stray-write backstop keep main-checkout writes out; see [worktree-seam](../implement-phase/SKILL.md#3-spawn-the-subagent).
- **PR-context file + `open-pr.sh` is the only PR-creation path.** No raw `gh pr create` / `glab mr create` calls outside the bundled script.
- **License check before any new dep.** Refuse `poetry add` / `pip install` / `uv add` when the package's SPDX license is in the forbidden list — see AGENTS.md **Dependency licenses**. The user can grant a one-off override after acknowledging the violation; record it in `policies.dependency_licenses.allowed_overrides` before re-running.
- **Never use `§N` shorthand to point at sections** — neither in this skill body nor in any rendered file (tracking, prs-context, branch description). Always use the section's full name with a markdown link when possible.

## Quick checklist (conductor, per phase)

- [ ] Plan parsed; structured fields cached.
- [ ] Cross-repo + flag-removal phases identified + deferred.
- [ ] `WORKROOT` / `BASE_BRANCH` / `SANDBOX_TIER` resolved once ([Resolve WORKROOT step](#step-05--resolve-workroot)); worktree provisioned + summary captured + user confirmed when `use_worktree = true`.
- [ ] [implement-phase](../implement-phase/SKILL.md) run: prompt composed with **Goals + Non-goals** + **Guiding Decisions** + relevant **Data Model Changes** subsection + tracking summaries + this phase's body; model picked from `**Suggested AI model**:` (cheapest available); implementer report received.
- [ ] [review-phase](../review-phase/SKILL.md) run: Layers 1–3 clean; BLOCKERs fixed; SHOULD-FIX fixed or noted; outer gate re-run after any fix; when a worktree is in use, `git -C <main_checkout> status --short` clean after the implementer and after every fixer.
- [ ] the resolved integrate-phase variant — [integrate-phase-stacked](../integrate-phase-stacked/SKILL.md) when `run_options.commit_strategy_resolved = stacked-branches`, else [integrate-phase-modular](../integrate-phase-modular/SKILL.md) run: {If `run_options.commit_strategy_resolved = stacked-branches`:} Stacked branch created; pushed. {Else (`modular-commits`):} Plan branch updated with phase commits; pushed. PR opened via the context file; PR URL captured.
  {If `run_options.commit_strategy_resolved = modular-commits`, also require:}
  - [ ] Commit units listed upfront before any staging.
  - [ ] Each commit covers exactly one logical unit (no "and" in commit messages).
  - [ ] Tests landed in the same commit as the code they cover (never a test-only commit).
  - [ ] All unit commits pushed to `plan/{plan-id-kebab}` at end of phase.
  - [ ] **Open PR via context file** decision applied per matrix (PR policy + `generate_inline_comments`): file written when at least one of policy=create / comments=true holds; `open-pr.sh` run when policy=create AND deps available (PR URL captured); per-comment failures (exit 1) and hard failures (exit 2) surfaced.
- [ ] `TRACKING_{plan-id}.md` updated.
- [ ] One-paragraph user update sent (PR URL or pending-file path included).
- [ ] If `run_options.pause_between_phases = true`: prompted user (`Continue` / `Pause` / `Stop`); honored answer. Else: next phase started immediately.
- [ ] On final phase: tracking file deleted; final summary lists branches + PR URLs; any `status: pending` PR-context files listed with publish command; `/schedule` offer for flag-removal if applicable.
