# Tracking — Multi-Backend Replication

- **Feature**: Multi-Backend Replication
- **Plan**: `ai-plans/2026-07-23-MULTI_BACKEND_REPLICATION_IMPLEMENTATION_PLAN.md`
- **Started**: 2026-07-23
- **Last updated**: 2026-07-23 (all phases complete)
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

### Phase 4 — Sync verification and health stats ✅

- **Implementer model**: claude-sonnet-5 (Tier 3) · **Reviewer**: claude-opus-4-8 (Tier 4) · **Fixer**: claude-sonnet-5 (Tier 3)
- **Commits**: `Add verify_notification_sync to notification services`; `Add get_backend_sync_stats to notification services`; + fix commits `Flag heterogeneous record types in sync verification`, `Pin the sync-comparable field lists with a review-gate test`
- **Files**: `dataclasses.py` (report TypedDicts + comparable-field constants), `notification_service.py` (both methods + shared `_build_notification_sync_report`), `tests/test_services/test_multi_backend_management.py`
- **Summary**: `verify_notification_sync(id)` reads the record from every registered backend, reports `backends_with_record`/`backends_missing_record` and per-field cross-backend agreement (`NotificationSyncReport` / `NotificationSyncFieldReport` TypedDicts). Comparable fields derived from the dataclass minus a documented volatile set (`created`/`modified`/`attachments`), pinned by a review-gate test so a new dataclass field forces a conscious comparable-vs-volatile decision. Heterogeneous record types (Notification vs OneOffNotification for one id) are flagged not-in-sync via a synthetic `record_type` field entry. `get_backend_sync_stats()` returns per-backend `{total_notifications, status, error?}` driven by `count_notifications({})`, isolating a raising backend as `status='error'` without propagating. Pure service orchestration — no seam/ABC change.
- **Review**: 0 BLOCKER, 2 SHOULD-FIX (field-derivation safety net; heterogeneous-type under-reporting) + 1 NIT (double call) — all fixed in-phase.
- **Gate**: ruff clean; mypy clean (75 files); full suite 626 passed, 2 skipped, 1 failed (clone-test artifact only).

### Phase 5 — Backend migration ✅

- **Implementer model**: claude-sonnet-5 (Tier 3) · **Reviewer**: claude-opus-4-8 (Tier 4) · **Fixer**: claude-sonnet-5 (Tier 3)
- **Commits**: `Add BackendMigrationError exception`; `Add migrate_to_backend to notification services`; + fix commit `Validate migrate_to_backend batch_size`
- **Files**: `exceptions.py` (`BackendMigrationError`), `dataclasses.py` (`BackendMigrationResult`/`BackendMigrationFailure` TypedDicts), `notification_service.py` (`migrate_to_backend` on both services), `tests/test_services/test_backend_migration.py`
- **Summary**: `migrate_to_backend(destination, batch_size, source=None)` pages the source (default primary) via `filter_notifications({})` and writes each record into the destination through the SHARED convergence primitive `_replicate_write_to_backend` (same path as inline/queued/process_replication) — snapshot-apply destinations get an id-preserving upsert; non-snapshot-apply destinations converge an existing row. COPY only (never deletes source). Idempotent on re-run (duplicate-conflict retry / newer-wins upsert — tests assert row count unchanged on 2nd run). Per-record failures captured in `{migrated, failures}` without aborting; an uncreatable destination record is classified a failure (mirrors Phase 3). Unknown source/destination → `BackendNotFoundError`; source==destination or `batch_size<=0` → `BackendMigrationError`. Docstring notes migration assumes a quiescent source.
- **Review**: 0 BLOCKER, 1 SHOULD-FIX (unvalidated batch_size → silent no-op) + 2 NIT (redundant read left per reviewer; quiescent-source doc added) — SHOULD-FIX + doc NIT fixed in-phase.
- **Gate**: ruff clean; mypy clean (76 files); full suite 646 passed, 2 skipped, 1 failed (clone-test artifact only).

### Phase 6 — Documentation ✅

- **Implementer model**: claude-sonnet-5 (Tier 2) · **Reviewer**: claude-opus-4-8 (Tier 4)
- **Commits**: `Document multi-backend configuration in the README`; `Record multi-backend in the 2.0.0 release notes`; `Note the replication queue stub in the template README`; `Deslop multi-backend comments and docstrings`
- **Files**: `README.md` (Multi-Backend Configuration section, absorbing the Phase 3 Queued Replication subsection), `RELEASE_NOTES.md` (folded into the unreleased 2.0.0 section — no new version heading), `templates/.../README.md` (+ `test_template_checklist.py` `_ABC_LOCATIONS` data for the new checklist block), comment-only deslop in `notification_service.py`/`fake_backend.py`/two test files
- **Summary**: README documents primary+additional backends, identifier resolution, read routing, inline vs queued replication + fallback, the monitoring/repair surface (verify/stats/process_replication/migrate), and a plain failure-semantics statement (primary propagates, replicas best-effort, no read-your-writes, no failover). RELEASE_NOTES entry folded into 2.0.0: non-breaking (concrete backend methods), the deliberate documented divergence from abstract-by-default + rationale, backwards-compatibility notes for downstream backends, and the read-then-write fidelity limitation. `pyproject.toml` verified at `2.0.0` (unchanged — corrected earlier by the main-merge). Template README gained the replication-queue stub note. Deslop: 5 comment/docstring rewrites, no behavior change.
- **Review**: 0 findings — every documented API cross-checked against source; RELEASE_NOTES confirmed folded (no new heading); no behavior change.
- **Gate**: ruff clean; mypy clean (76 files); full suite 646 passed, 2 skipped, 1 failed (clone-test artifact only).

## Remaining phases

- _(none — all executable phases complete)_

## Deferred phases

- Phase 5b — `vintasend-django` implementation (cross-repo, separate repo; cannot merge until core releases)
