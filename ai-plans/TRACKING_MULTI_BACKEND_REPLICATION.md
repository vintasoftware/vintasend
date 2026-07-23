# Tracking — Multi-Backend Replication

- **Feature**: Multi-Backend Replication
- **Plan**: `ai-plans/2026-07-23-MULTI_BACKEND_REPLICATION_IMPLEMENTATION_PLAN.md`
- **Started**: 2026-07-23
- **Last updated**: 2026-07-23 (Phase 3 complete)
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

### Phase 3 — Queued replication ✅

- **Implementer model**: claude-opus-4-8 (Tier 4) · **Reviewer**: claude-opus-4-8 (Tier 4) · **Fixer**: claude-sonnet-5 (Tier 3)
- **Commits**: `Add ReplicationError exception`; `Add replication queue service seam and fake stub`; `Add NOTIFICATION_REPLICATION_MODE and queue service settings`; `Resolve replication queue service in helpers`; `Wire queued replication into notification services`; `Add process_replication worker entrypoint to notification services`; `Add replication queue stub to implementation template`; + fix commits `Classify uncreatable replicas as process_replication failures`, `Validate replication_mode and cover its resolution`, `Cover a failing middle backend in queued enqueue fallback`
- **Files**: new `notification_queue_services/replication_base.py` + `asyncio_replication_base.py` + `stubs/fake_replication_queue_service.py`; `exceptions.py` (`ReplicationError`); `helpers.py` (resolver); `app_settings.py` (2 settings × 6 layers); `notification_service.py` (queued branch + `process_replication`); `README.md` + `ai-tools/AGENTS.md` (env-var docs, required by add-env-var); template `replication_queue_service.py` + `test_replication_queue_service.py`; tests `test_multi_backend_replication.py`, `test_app_settings.py`, `test_notification_queue_services.py`
- **Summary**: New abstract replication-queue seam `BaseNotificationReplicationQueueService.enqueue_replication(id, backend_identifier)` (+ AsyncIO twin + Fake stub), sibling of the send-queue seam. Both services accept/resolve `replication_queue_service` and honour `replication_mode="queued"` (resolved from `NOTIFICATION_REPLICATION_MODE`, explicit arg wins, invalid value raises). Queued branch enqueues one task per additional backend; falls back to inline-all + warning when no queue service or unresolvable id, and inline-only-that-backend on a per-backend enqueue error. `process_replication(id, target=None)` worker entrypoint converges targets via the shared Phase 2 path, returns `{successes, failures}`, raises `BackendNotFoundError` on unknown target and `ReplicationError` on missing-on-primary; a target that can't be created (declining backend lacking the row) is classified as a failure, not false success. `replicate_notification` is the no-target alias. Two new settings across all six layers (via add-env-var). Template gained a replication-queue stub (per user decision).
- **Review**: 0 BLOCKER, 2 SHOULD-FIX (process_replication false-success on uncreatable target; untested mode resolution) + 2 NIT (invalid-mode fail-loud; 3-replica middle-failure test) — all fixed in-phase.
- **Env-var-in-worktree caveat**: `test_clone_produces_an_immediately_green_test_suite` fails ONLY in this worktree (effective `poetry run` venv is the main checkout's, whose editable `vintasend` lacks the unmerged modules; the clone subprocess resolves that stale source). Proven benign — the template suite passes 62/62 when `vintasend` resolves to the worktree source, and it passes in CI/single-checkout. Every phase's gate verifies this is the SOLE failure.
- **Gate**: ruff clean; mypy clean (74 files); full suite 607 passed, 2 skipped, 1 failed (the clone-test artifact above only).

## Current phase

- Phase 4 — Sync verification and health stats

## Remaining phases

- Phase 4 — Sync verification and health stats (Tier 3)
- Phase 5 — Backend migration (Tier 3)
- Phase 6 — Documentation (Tier 2)

## Deferred phases

- Phase 5b — `vintasend-django` implementation (cross-repo, separate repo; cannot merge until core releases)
