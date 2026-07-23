# Tracking — Multi-Backend Replication

- **Feature**: Multi-Backend Replication
- **Plan**: `ai-plans/2026-07-23-MULTI_BACKEND_REPLICATION_IMPLEMENTATION_PLAN.md`
- **Started**: 2026-07-23
- **Last updated**: 2026-07-23 (Phase 1 complete)
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

## PR

- **PR #24**: https://github.com/vintasoftware/vintasend/pull/24 (one PR for the whole plan; updated per phase)

## Completed phases

### Phase 1 — Backend registry, identity, and read routing ✅

- **Implementer model**: claude-sonnet-5 (Tier 3) · **Reviewer**: claude-opus-4-8 (Tier 4) · **Fixer**: claude-sonnet-5 (Tier 3)
- **Commits**: `Add BackendNotFoundError exception`; `Add optional backend identity and get_all_notifications defaults`; `Add multi-backend registry and read routing to notification services`; `Raise on duplicate backend identifier at registration`; `Guard get_all_notifications default against empty pages`
- **Files**: `vintasend/exceptions.py`, `vintasend/services/notification_backends/base.py` + `asyncio_base.py`, `vintasend/services/notification_service.py`, `vintasend/tests/test_services/test_multi_backend_reads.py`
- **Summary**: Both services accept `additional_backends` (instances or dotted strings), build an ordered registry keyed by identifier (primary first). Identifier resolves via `get_backend_identifier()` with `backend-{n}` fallback (primary → `backend-0`, additional → `backend-{position}`). Registration raises `DuplicateBackendIdentifierError` on any identifier collision (incl. collision with the primary, which would otherwise silently reroute reads). New service methods: `get_primary_backend_identifier`, `get_all_backend_identifiers`, `get_additional_backend_identifiers`, `has_backend`, private `_get_backend`. Every read method gained an optional trailing `backend_identifier` (absent → primary, unknown → `BackendNotFoundError`), preserving all positional call sites. Backend base + asyncio base got concrete `get_backend_identifier` (→ `None`) and concrete paged `get_all_notifications` (with empty-page guard, tested past 100 records). Writes still hit only the primary — fan-out is Phase 2.
- **Review**: 3 findings (2 SHOULD-FIX + 1 NIT), all fixed in-phase: duplicate-identifier guard, multi-page pagination test, empty-page terminator. No BLOCKERs.
- **Gate**: mypy clean; full suite 487 passed, 2 skipped (pre-existing).

## Current phase

- Phase 2 — Inline write fan-out

## Remaining phases

- Phase 2 — Inline write fan-out (Tier 4; reviewer Tier 4)
- Phase 3 — Queued replication (Tier 4; reviewer Tier 4)
- Phase 4 — Sync verification and health stats (Tier 3)
- Phase 5 — Backend migration (Tier 3)
- Phase 6 — Documentation (Tier 2)

## Deferred phases

- Phase 5b — `vintasend-django` implementation (cross-repo, separate repo; cannot merge until core releases)
