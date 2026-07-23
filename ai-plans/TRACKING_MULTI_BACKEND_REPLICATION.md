# Tracking — Multi-Backend Replication

- **Feature**: Multi-Backend Replication
- **Plan**: `ai-plans/2026-07-23-MULTI_BACKEND_REPLICATION_IMPLEMENTATION_PLAN.md`
- **Started**: 2026-07-23
- **Last updated**: 2026-07-23 (Phase 2 complete)
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
- **Template decision** (user, mid-run): in Phase 3, add `replication_queue_service.py` + `test_replication_queue_service.py` to `templates/vintasend-implementation-template/` mirroring the existing `queue_service` stub (the new `BaseNotificationReplicationQueueService` seam is abstract). Leave the template `backend.py` untouched — the multi-backend backend methods are concrete-by-design and must not be stubbed. Add a short template-README note in Phase 6.

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

### Phase 2 — Inline write fan-out ✅

- **Implementer model**: claude-opus-4-8 (Tier 4) · **Reviewer**: claude-opus-4-8 (Tier 4) · **Fixer**: claude-sonnet-5 (Tier 3)
- **Commits**: `Add ApplyResult and default snapshot-apply to backend seam`; `Fan notification writes out to additional backends inline`; `Isolate replication snapshot read from primary write`; `Cover multi-backend mark_read_bulk fan-out`; `Converge replica status through intermediate transitions`
- **Files**: `vintasend/services/dataclasses.py` (`ApplyResult`), `notification_backends/base.py` + `asyncio_base.py` (concrete `apply_replication_snapshot_if_newer` default), `notification_backends/stubs/fake_backend.py` (working upsert override on both fakes), `notification_service.py` (wrapper + routing), `tests/test_services/test_multi_backend_writes.py`
- **Summary**: One `_execute_multi_backend_write` wrapper on both services: primary write first (its result/exception is the user's), then fan out to each additional backend in registry order under try/except — replica failures logged and swallowed, never re-raised. Single-backend service short-circuits (no replication path). Replica path prefers `apply_replication_snapshot_if_newer` (concrete default → `ApplyResult(applied=False)`), else read-then-write convergence. `_is_likely_duplicate_replication_conflict` flips create↔update once on a duplicate/conflict. Every write routed: create/create_one_off/update/resend, mark sent/failed/read (+bulk), cancel, store_context_used, store_git_commit_sha, and the send()/delayed_send() points. `replication_mode` param accepted (only "inline" behaves; "queued" is Phase 3).
- **Review**: 1 BLOCKER + 3 SHOULD-FIX + 1 NIT. BLOCKER (a replication-only snapshot re-read could fail an already-committed primary write on a non-`NotificationError`) fixed by widening the catch to swallow+log+return None. SHOULD-FIX: added declining-backend *create* test (proves the documented no-inline-create gap) + `mark_read_bulk` convergence test; improved read-then-write status transitions (reach READ via SENT). NIT (fold one helper) deferred to Phase 3.
- **Known best-effort limitation** (→ Phase 6 release note): the read-then-write fallback (non-snapshot-apply backends) does not converge attachments or intermediate audit fields, and cannot create a row with the primary's id inline — backends needing full-fidelity replication implement `apply_replication_snapshot_if_newer`.
- **Gate**: ruff clean; mypy clean; full suite 512 passed, 2 skipped (pre-existing).

## Current phase

- Phase 3 — Queued replication

## Remaining phases

- Phase 3 — Queued replication (Tier 4; reviewer Tier 4) — ALSO add `replication_queue_service.py` + `test_replication_queue_service.py` to `templates/vintasend-implementation-template/` (see Template decision above)
- Phase 4 — Sync verification and health stats (Tier 3)
- Phase 5 — Backend migration (Tier 3)
- Phase 6 — Documentation (Tier 2)

## Deferred phases

- Phase 5b — `vintasend-django` implementation (cross-repo, separate repo; cannot merge until core releases)
