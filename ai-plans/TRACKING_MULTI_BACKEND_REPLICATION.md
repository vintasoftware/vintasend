# Tracking — Multi-Backend Replication

- **Feature**: Multi-Backend Replication
- **Plan**: `ai-plans/2026-07-23-MULTI_BACKEND_REPLICATION_IMPLEMENTATION_PLAN.md`
- **Started**: 2026-07-23
- **Last updated**: 2026-07-23
- **Feature flag**: none (inert unless a host passes `additional_backends`)

## Run options

- `pause_between_phases`: false
- `generate_inline_comments`: false
- `full_test_suite`: false
- `use_worktree`: true
- `worktree_path`: `.claude/worktrees/plan-multi-backend-replication`
- `worktree_branch`: `plan/multi-backend-replication`
- `worktree_summary`: `.vinta-ai-workflows/worktrees/plan-multi-backend-replication.yaml`
- `sandbox_tier`: enforced
- `commit_strategy_resolved`: modular-commits
- `plan_branch`: `plan/multi-backend-replication`
- **Version decision**: fold into unreleased 2.0.0 RELEASE_NOTES; correct `pyproject.toml` back to `2.0.0` in Phase 6 (do NOT create a new version entry).

## Completed phases

_(none yet)_

## Current phase

- Phase 1 — Backend registry, identity, and read routing

## Remaining phases

- Phase 1 — Backend registry, identity, and read routing (Tier 3)
- Phase 2 — Inline write fan-out (Tier 4; reviewer Tier 4)
- Phase 3 — Queued replication (Tier 4; reviewer Tier 4)
- Phase 4 — Sync verification and health stats (Tier 3)
- Phase 5 — Backend migration (Tier 3)
- Phase 6 — Documentation (Tier 2)

## Deferred phases

- Phase 5b — `vintasend-django` implementation (cross-repo, separate repo; cannot merge until core releases)
