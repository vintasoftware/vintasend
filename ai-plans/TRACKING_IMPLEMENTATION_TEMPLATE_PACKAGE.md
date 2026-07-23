# Tracking — Implementation Template Package

- **Feature**: Implementation Template Package (contributor tooling)
- **Plan**: `ai-plans/2026-07-23-IMPLEMENTATION_TEMPLATE_PACKAGE_IMPLEMENTATION_PLAN.md`
- **Started**: 2026-07-23
- **Last updated**: 2026-07-23
- **Feature flag**: none (no runtime change)

## Run options

- `commit_strategy_resolved`: modular-commits
- `plan_branch`: `plan/implementation-template-package`
- `pause_between_phases`: false (auto-flow)
- `generate_inline_comments`: true
- `full_test_suite`: false (Quick)
- `use_worktree`: true
- `worktree_path`: `.claude/worktrees/plan-implementation-template-package`
- `worktree_branch`: `plan-implementation-template-package`
- `worktree_summary`: `.vinta-ai-workflows/worktrees/plan-implementation-template-package.yaml`
- `sandbox_tier`: enforced

## Agent models

- implementer: Tier 2 (plan-owned)
- reviewer: Tier 4 (claude-opus-4-8)
- fixer: Tier 3 (claude-sonnet-5)
- worktree_prep: Tier 1 (claude-haiku-4-5) — done
- integrate: Tier 1 (claude-haiku-4-5)

## Phases

### Phase 1 — Package skeleton, stubs, and scaffold tests
- **Status**: ✅ complete
- **Model used**: claude-sonnet-5 (Tier 2, stepped up for >3 files)
- **Commits** (on `plan/implementation-template-package`):
  - `b9d47bb` Scaffold vintasend-implementation-template package
  - `ea88110` Add backend seam stub and scaffold test
  - `847cb29` Add template renderer seam stub and scaffold test
  - `09d795e` Add adapter seam stub and scaffold test
  - `14ee2f0` Add queue service seam stub and scaffold test
  - `b1b961a` Add attachment manager seam stub and scaffold test
  - `be03cab` Exclude templates/ from root ruff scope and guard the exclusion
  - `2dda32a` Add async fails-loudly test to backend scaffold test (review fix)
- **Summary**: Added `templates/vintasend-implementation-template/` — an installable Poetry
  package skeleton. One `TODO`-stubbed module per seam subclassing the real, current ABC with
  every abstract method present and a `raise NotImplementedError` body: backend (sync + AsyncIO),
  adapter (sync + AsyncIO + background + AsyncIO-background), template renderer (base + email +
  SMS; no AsyncIO twin exists for this seam), queue service (sync + AsyncIO), attachment manager
  (sync + AsyncIO — `reconstruct_attachment_file` stays sync per the ABC). One scaffold test per
  stub asserts subclass, full abstract-method coverage (`__abstractmethods__ == frozenset()`),
  instantiability, and fails-loudly. Root `pyproject.toml` gains `templates` in ruff's
  `extend-exclude` (mypy `files` / pytest `testpaths` already `vintasend`-scoped) plus a `tomli`
  mypy override; `vintasend/tests/test_template_exclusion.py` guards the exclusion. No runtime
  change to `vintasend`; `templates/` stays out of the wheel.
- **Review**: 3 layers clean; reviewer (Tier 4) cross-checked all seam method sets against the
  ABCs; one NIT (async-backend test parity) fixed by the Tier-3 fixer.
- **Gates**: `ruff` clean, `mypy` clean (59 files), root `pytest` 412 passed / 2 skipped,
  template suite green.

### Phase 2 — Clone script and workflow documentation
- **Status**: pending

## Deferred phases

- None (no cross-repo or flag-removal phases).
