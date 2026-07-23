# Multi-Backend Replication — Implementation Plan

Full port of `vintasend-ts`'s multi-backend architecture (its v0.8.1 / v0.9.0 line): a primary
backend plus N additional backends, every write fanned out to the replicas (inline or via a
replication queue), per-record sync verification and repair, health stats, and paged migration
between backends. Baseline is `vintasend` 2.0.0, whose queue-service seam and host-factory worker
model this plan reuses for queued replication.

## 1. Goals

1. Let a service hold one primary backend and zero or more additional backends, each addressable by a
   stable identifier, with reads routable to any of them.
2. Fan every write out from the primary to the additional backends — inline by default, or through a
   replication queue when configured — with replica failures isolated from the primary write.
3. Provide the monitoring surface a dashboard needs: verify one notification's sync across backends,
   report per-backend health stats, replicate or repair a single record, and migrate all
   notifications from one backend to another in pages.

Non-goals:

- **No conflict resolution beyond last-writer / newer-snapshot.** Replication applies the primary's
  snapshot; `apply_replication_snapshot_if_newer` is the only merge policy. No field-level merge.
- **No cross-backend transactions.** The primary write commits first; replication is best-effort.
  There is no two-phase commit and no rollback of the primary if a replica fails.
- **No automatic failover of reads or writes.** A down primary is an error, not a trigger to promote
  a replica. Promotion is an operational action outside this library.
- **No `vintasend-sqlalchemy` work.** Downstream scope is core + `vintasend-django`.
- **No new queue *transport*.** Queued replication reuses the 2.0 queue-service shape and the
  host-factory worker model; it does not introduce a second queue mechanism.

## 2. Guiding Decisions

| Decision | Resolution |
|---|---|
| **Backends held in an ordered map keyed by identifier** | TS keeps `backends: Map<string, Backend>` plus a `primaryBackendIdentifier`. Python mirrors it with a dict preserving insertion order, so `get_all_backend_identifiers()` is deterministic and read routing is a dict lookup. |
| **Identifier resolution falls back to `backend-{n}`** | A backend may implement `get_backend_identifier()`; if it does not, the service assigns `backend-{index}`. This is why the identity method is **optional**, not abstract — see the compatibility row. |
| **Multi-backend backend methods are concrete/optional, diverging from the project's abstract-by-default posture** | The three 2.0 ports added abstract methods because every backend must persist, query and store attachments. Multi-backend is different: it is an **opt-in** feature most single-backend deployments never touch. Making `get_backend_identifier`, `apply_replication_snapshot_if_newer` and `get_all_notifications` abstract would break every downstream backend at instantiation to serve a feature they do not use. So these ship as **concrete defaults** (identifier → `backend-{n}` fallback handled service-side; snapshot-apply → absent means the service falls back to read-then-write; `get_all_notifications` → default implemented via `filter_notifications({})` paged). This is a deliberate, documented exception to the `AGENTS.md` default; call it out in the release note as a *non-breaking* minor. |
| **Every write goes through one `execute_multi_backend_write` wrapper** | TS routes `createNotification`, `updateNotification`, `markAsSent/Failed`, `storeAdapterAndContextUsed`, git-sha persistence and the one-off variants through a single helper: primary write first, then replicate. Centralising it means replica fan-out is written once per service, not per write method, and the failure policy lives in one place. |
| **Primary failure propagates; replica failure is logged, not raised** | The primary is the source of truth. A replica that rejects a write must not fail the user's operation — it is reconciled later by `process_replication` or the next write. This is TS's policy and the only one consistent with best-effort replication. |
| **`replication_mode: 'inline' | 'queued'`, default `'inline'`** | Inline replication needs no extra infrastructure and is the right default. Queued replication (one task per destination backend) moves replica writes off the request path for hosts that already run the 2.0 worker; when the enqueue itself fails, the service falls back to inline for the affected backend so a broken queue never silently drops replication. |
| **Queued replication reuses the 2.0 queue-service shape** | The background-send 2.0 work already established `BaseNotificationQueueService` and the host-factory worker. The replication queue seam is its sibling — `enqueue_replication(notification_id, backend_identifier)` — and the worker resolves its service the same way. No second worker model. |
| **Duplicate-conflict retry on replication** | Replicating a create that the replica already has (or an update for a row it lacks) is normal under retries. The service detects a duplicate/unique/conflict error by substring and flips create↔update, matching TS's `isLikelyDuplicateReplicationConflict`. |
| **Read routing is an optional trailing `backend_identifier` on every read** | Absent → primary. Unknown → raise `BackendNotFoundError`. This keeps every existing read call site working unchanged while letting a dashboard target a specific replica. |
| **Reuses the 2.0 `raise_on_failed_send` error posture, adds no new options object** | The service already carries `raise_on_failed_send`. `replication_mode` joins the constructor as a plain keyword rather than resurrecting a bundled options object. |
| **No feature flag** | No flag module in this library, and multi-backend is inert unless a host passes `additional_backends`. A single-backend deployment sees byte-for-byte identical behaviour, which is the moral equivalent of the flag defaulting off. The compatibility boundary is the release. |

## 3. Data Model Changes

No persisted-model changes in core. The changes are to service construction, the backend seam's
optional surface, and a new replication-queue seam.

### 3.1 Service construction

Both services gain, defaulted so existing construction is unaffected:

```python
additional_backends: Iterable[B | str] | None = None,
replication_queue_service: BaseNotificationReplicationQueueService | str | None = None,
replication_mode: Literal["inline", "queued"] = "inline",
```

### 3.2 New replication-queue seam

```python
# vintasend/services/notification_queue_services/replication_base.py
class BaseNotificationReplicationQueueService(ABC):
    @abstractmethod
    def enqueue_replication(
        self, notification_id: int | str | uuid.UUID, backend_identifier: str
    ) -> None: ...
# + AsyncIO twin
```

Lives beside the existing `base.py` / `asyncio_base.py` under
[notification_queue_services/](../vintasend/services/notification_queue_services/).

### 3.3 New optional backend methods (concrete defaults, per the compatibility decision)

On both `BaseNotificationBackend` and `AsyncIOBaseNotificationBackend`:

- `get_backend_identifier() -> str | None` — default `None`; service supplies `backend-{n}`.
- `apply_replication_snapshot_if_newer(snapshot) -> ApplyResult` — default absent behaviour (service
  falls back to read-then-write). Ships as a concrete method returning "not applied" so a backend can
  override with a newer-wins upsert.
- `get_all_notifications() -> Iterable[...]` — concrete default over `filter_notifications({})`,
  overridden by ORMs for efficiency; feeds `get_backend_sync_stats`.

### 3.4 New setting

`NOTIFICATION_REPLICATION_QUEUE_SERVICE` and `NOTIFICATION_REPLICATION_MODE`, across the six
`add-env-var` layers.

### 3.5 New exceptions

`BackendNotFoundError`, `ReplicationError`, `BackendMigrationError`, on `NotificationError`.

## 4. Phased Rollout

Ordered so each phase leaves a working system and the slowest-to-review write-fanout lands after the
registry it depends on. Every phase is written for both the sync and asyncio services.

### Phase 1 — Backend registry, identity, and read routing

**Goal**: a service accepts additional backends, exposes their identifiers, and routes any read to a
named backend. Writes still hit the primary only — replication is explicitly the next phase.

**Feature flag**: none — additive; with no `additional_backends` the service behaves exactly as 2.0.

Changes:

1. [notification_service.py](../vintasend/services/notification_service.py) `__init__` (`:168`,
   `:1157`): accept `additional_backends`; build the ordered backend map; resolve each identifier via
   `get_backend_identifier()` with the `backend-{n}` fallback; store the primary identifier.
2. New service methods on both classes: `get_primary_backend_identifier`,
   `get_all_backend_identifiers`, `get_additional_backend_identifiers`, `has_backend`, and a private
   `_get_backend(backend_identifier=None)` that returns the primary when `None` and raises
   `BackendNotFoundError` on an unknown id.
3. Every read method (`get_notification`, `filter_notifications`, the future/pending/in-app getters):
   add an optional trailing `backend_identifier: str | None = None`, routing through `_get_backend`.
4. [notification_backends/base.py](../vintasend/services/notification_backends/base.py) +
   [asyncio_base.py](../vintasend/services/notification_backends/asyncio_base.py): concrete
   `get_backend_identifier` returning `None`; concrete `get_all_notifications` over
   `filter_notifications({})`.
5. [exceptions.py](../vintasend/exceptions.py): `BackendNotFoundError`.

Spec use-case: no spec — ports TS's backend map and read routing.

Tests:

- **Unit**: a service with two additional backends reports three identifiers in order; a backend with
  a custom `get_backend_identifier` uses it, one without gets `backend-{n}`; `has_backend` is
  accurate; reads route to the named backend; an unknown id raises `BackendNotFoundError`.
- **Integration**: a read with no `backend_identifier` hits the primary; the same read against a
  replica id hits that replica (populate the replica directly to prove routing).
- **Backwards compatibility**: a single-backend service (no `additional_backends`) passes the entire
  existing suite unchanged, and every read call site still works positionally.

**Suggested AI model**: Tier 3 (IDs in [resources/ai-models.yaml](../.claude/skills/plan-feature/resources/ai-models.yaml)).
Registry plus read-routing across both service classes; mechanical but wide.

Acceptance: a service with additional backends exposes ordered identifiers and routes reads to any
named backend, an unknown identifier raises, and a single-backend service is behaviourally identical
to 2.0.

### Phase 2 — Inline write fan-out

**Goal**: with additional backends configured, every write replicates to them inline; replica
failures are logged and never fail the primary write.

**Feature flag**: none — this is the multi-backend feature; inert without additional backends.

Changes:

1. New private `_execute_multi_backend_write(primary_write, additional_write, replication_notification_id=None)`
   on both services: run `primary_write(primary)`, then for each additional backend run
   `additional_write(backend)` under try/except, logging failures. Detect
   duplicate/unique/conflict errors and retry create↔update
   (`_is_likely_duplicate_replication_conflict`).
2. Route every write through it: `create_notification`, `update_notification`,
   `mark_pending_as_sent`/`_failed`, `mark_sent_as_read`(+bulk), `cancel_notification`,
   `store_context_used`, `store_git_commit_sha` (if the git-SHA plan has landed) and the one-off
   variants, in both services.
3. [notification_backends/base.py](../vintasend/services/notification_backends/base.py) +
   asyncio twin: concrete `apply_replication_snapshot_if_newer` returning "not applied"; the service
   uses it when present, else falls back to read-then-write.
4. `replication_mode` accepted in `__init__` (only `'inline'` reachable until Phase 3).

Spec use-case: no spec — ports `executeMultiBackendWrite` and inline replication.

Tests:

- **Unit**: a create fans out to both replicas; a replica raising does not fail the primary write and
  is logged; a duplicate-conflict on replicate flips create↔update and succeeds; `mark_*` and
  `cancel` replicate; a single-backend service takes no replication path (assert the wrapper
  short-circuits).
- **Integration**: create → update → mark-sent against a two-replica service leaves all three
  backends holding the same notification state.
- **Failure isolation**: with one replica configured to always raise, the primary write still
  succeeds and the notification is retrievable from the primary.

**Suggested AI model**: Tier 4. Wrapping every write in both services while preserving each write's
existing guarantees and isolating replica failures — high blast radius, subtle ordering.

**Review models**: reviewer Tier 4 — a mistake here either fails a user's primary write on a replica
error (availability regression) or silently skips replication (data divergence). Both are invisible
to a happy-path suite.

Acceptance: every write replicates to the additional backends inline, a failing replica never fails
the primary write, duplicate-conflict retries reconcile, and a single-backend service replicates
nothing.

### Phase 3 — Queued replication

**Goal**: hosts running the 2.0 worker can push replica writes onto a queue, one task per destination
backend, with inline fallback when the enqueue fails.

**Feature flag**: none.

Changes:

1. New `@vintasend/services/notification_queue_services/replication_base.py` and its asyncio twin per
   **Data Model Changes**; a `FakeReplicationQueueService` stub recording `(id, backend_identifier)`
   pairs.
2. `__init__`: accept `replication_queue_service`; honour `replication_mode='queued'`.
3. `_execute_multi_backend_write`: in `'queued'` mode, resolve the replication notification id and
   `enqueue_replication(id, backend_identifier)` per additional backend; on a missing id or missing
   queue service, warn and fall back to inline; on an enqueue error for a given backend, inline-
   replicate only that one.
4. New `process_replication(notification_id, target_backend_identifier=None)` worker entrypoint on
   both services: read the snapshot from the primary; for each target, prefer
   `apply_replication_snapshot_if_newer`, else read-then-write with the duplicate-conflict retry;
   return `{successes, failures}`. `replicate_notification(id)` as the no-target alias.
5. `register_replication_queue_service` on both services.
6. [helpers.py](../vintasend/services/helpers.py): resolver for the replication queue service.
7. [app_settings.py](../vintasend/app_settings.py): the two new settings across six layers.

Spec use-case: no spec — ports `BaseNotificationReplicationQueueService`, queued mode and
`processReplication`.

Tests:

- **Unit**: queued mode enqueues one task per additional backend with the right identifier; a missing
  id falls back to inline; an enqueue error inline-replicates only the failed backend;
  `process_replication` applies the snapshot to all targets, or to a single named target; an unknown
  target raises.
- **Integration**: enqueue in the web path, drain in a worker via `process_replication`, assert the
  replica converges to the primary — end to end against the fakes.
- **Fallback**: with no replication queue service but `replication_mode='queued'`, writes still
  replicate inline and a warning is logged.

**Suggested AI model**: Tier 4. Queue fan-out with per-backend inline fallback and a worker
reconciliation entrypoint — concurrency-flavoured and easy to get subtly wrong.

**Review models**: reviewer Tier 4 — the fallback and duplicate-conflict paths are exactly where
replication silently diverges under partial failure.

Acceptance: queued mode enqueues one task per replica, degrades to inline on enqueue failure, and
`process_replication` converges a replica to the primary snapshot.

### Phase 4 — Sync verification and health stats

**Goal**: a dashboard can check whether one notification is in sync across backends and see
per-backend health at a glance.

**Feature flag**: none.

Changes:

1. `verify_notification_sync(notification_id)` on both services: read the record from every backend
   and produce a field-by-field diff report over the notification's comparable fields (the ~16 TS
   compares, adjusted to the Python dataclass — id, user_id, type, templates, status, context_name,
   send_after, sent_at, read_at, tenant, adapter_used, git_commit_sha where present, etc.). Report
   per-backend present/absent and per-field agreement.
2. `get_backend_sync_stats()` on both services: per backend, `{total_notifications, status:
   'healthy'|'error', error?}`, driven by `get_all_notifications` / a count, catching backend errors
   into the `error` field rather than raising.

Spec use-case: no spec — ports `verifyNotificationSync` and `getBackendSyncStats`.

Tests:

- **Unit**: a fully-synced record reports all-agree; a record differing in one field on one replica
  is flagged with that field and backend; a record missing from a replica is reported absent; stats
  report counts and mark a raising backend `error` without propagating.
- **Integration**: create against the primary, replicate to one of two replicas, and assert
  `verify_notification_sync` flags the un-replicated one.

**Suggested AI model**: Tier 3. Field-diff and stats aggregation over the backend map; contained once
the registry exists.

Acceptance: `verify_notification_sync` reports per-field, per-backend agreement and absence, and
`get_backend_sync_stats` returns counts with a per-backend health flag that survives a raising
backend.

### Phase 5 — Backend migration

**Goal**: copy every notification from one backend to another in pages, for onboarding a new replica
or retiring an old backend.

**Feature flag**: none.

Changes:

1. `migrate_to_backend(destination_backend_identifier, batch_size, source_backend_identifier=None)`
   on both services: page through the source (default primary) with `filter_notifications({})` or
   `get_all_notifications`, writing each into the destination via the snapshot-apply / read-then-write
   path with the duplicate-conflict retry. Return a count and any per-record failures. Resumable by
   re-running (idempotent via the conflict retry).

Spec use-case: no spec — ports `migrateToBackend`.

Tests:

- **Unit**: migration copies all records in pages; re-running is idempotent (no duplicates); a
  destination write failure is reported without aborting the whole migration; an unknown source or
  destination raises.
- **Integration**: populate the primary, migrate to an empty replica, assert the replica matches and
  a second run changes nothing.

**Suggested AI model**: Tier 3. Paged copy on established patterns, with idempotency the main care.

Acceptance: `migrate_to_backend` copies every notification into the destination in pages, is
idempotent on re-run, and reports per-record failures without aborting.

### Phase 5b — `vintasend-django` implementation (parallel track, separate repo)

**Goal**: the Django backend implements the optional multi-backend hooks efficiently. Runs alongside
Phases 1-5; **cannot merge until core has released**.

**Feature flag**: none.

Changes:

1. `get_backend_identifier()` — return a configured identifier if the host sets one, else leave the
   service fallback.
2. `apply_replication_snapshot_if_newer(snapshot)` — an `update_or_create`-style newer-wins upsert
   keyed on id, comparing `modified` so an older snapshot never clobbers a newer row.
3. `get_all_notifications()` — an efficient queryset rather than the `filter_notifications({})`
   default; a `.count()`-backed contribution to `get_backend_sync_stats`.

Spec use-case: no spec — downstream adoption.

Tests: snapshot-apply respects the newer-wins guard; `get_all_notifications` streams; identifier is
stable.

**Suggested AI model**: Tier 3.

Acceptance: `DjangoDbNotificationBackend` applies a newer snapshot, refuses an older one, and reports
a stable identifier and an efficient count.

### Phase 6 — Documentation

**Goal**: an operator can configure multiple backends and reason about the failure modes.

**Feature flag**: none.

Changes:

1. [README.md](../README.md): a "Multi-Backend Configuration" section — primary + additional backends,
   inline vs queued replication and when to pick each, read routing, verify/stats/migrate, and a plain
   statement of the failure semantics (primary propagates, replicas best-effort, no failover).
2. [RELEASE_NOTES.md](../RELEASE_NOTES.md): minor entry. Because the new backend methods are
   **concrete**, this is a non-breaking minor — say so, and note the deliberate divergence from the
   abstract-by-default seam rule and why (opt-in feature, no forced downstream change).

Spec use-case: no spec — release documentation.

Tests: none beyond a green suite; every README example must run.

**Suggested AI model**: Tier 2.

**Reusable skills**: `Skill(release-package)`; `Skill(deslop-comments)`.

Acceptance: the README documents configuration and failure semantics, and `RELEASE_NOTES.md` records
a non-breaking minor with the concrete-methods rationale.

## 5. Risk & Rollout Notes

- **Deliberately non-breaking despite touching the backend seam.** The new backend methods are
  concrete with working defaults, so no downstream backend breaks at instantiation — the exception to
  the abstract-by-default rule is justified by multi-backend being opt-in. `vintasend-django`'s
  Phase 5b is an *optimisation*, not a requirement; the concrete defaults work without it.
- **Best-effort replication is a consistency model, not a bug.** Replicas can lag or diverge under
  partial failure; `verify_notification_sync` and `process_replication` are the reconciliation tools.
  The README must state plainly that this is not synchronous multi-master and offers no read-your-
  writes guarantee across backends.
- **Queued replication inherits the 2.0 worker constraints**: the replication worker needs the same
  host-factory service and shared settings as background send. A replication task for a backend the
  worker cannot construct silently fails — call it out.
- **Write amplification.** Every write now potentially touches N+1 backends. Inline mode puts that on
  the request path; queued mode moves it off but multiplies task volume. Note the throughput
  implication.
- **`_execute_multi_backend_write` is the highest-risk change** — it wraps every write in both
  services. Land Phase 2 behind thorough failure-isolation tests; a regression here is a data-
  divergence or availability incident, not a crash.
- **Rollback**: Phases 1, 4, 5, 6 are independently revertible. Phase 2 is revertible before any host
  configures additional backends in production. Phase 3's queue seam is additive and revertible.
  Because nothing changes a persisted schema in core, rollback is code-only.
- **Coordinate with the queue seam.** This plan assumes the 2.0 background-send queue is in place
  (confirmed in the current tree). If the replication queue seam is ever factored to share code with
  `BaseNotificationQueueService`, do it as a small refactor before Phase 3, not inside it.

## 6. Open Questions

| Question | Recommended default |
|---|---|
| Concrete vs abstract for the new backend methods? | **Concrete**, diverging from the project's abstract-by-default rule, because multi-backend is opt-in and forcing every backend to implement replication hooks for a feature they never use is the wrong trade. Flagged for explicit sign-off since it is a documented exception. |
| Should read routing default-fan-out (query all backends) anywhere? | **No.** Reads target one backend; a dashboard that wants to compare uses `verify_notification_sync`. Implicit fan-out reads would multiply load invisibly. |
| How are the ~16 sync-compare fields chosen? | **Every persisted, comparable field on the notification dataclass**, excluding volatile/derived ones (e.g. `modified` timestamps that legitimately differ per backend). Enumerate them explicitly in Phase 4 rather than "all fields", and keep the list next to the dataclass so it tracks new fields. |
| Should `migrate_to_backend` delete from the source? | **No — copy only.** Retiring the source is an operational decision after verifying the destination. A migrate-and-delete would make an un-verified destination a data-loss risk. |
| Newer-wins comparison key? | **`modified` timestamp.** It is the only monotonic-ish field every backend carries. Document that clock skew across backends can misorder; the primary-first write order makes this rare. |

## 7. Touch List

**Phase 1**

- [notification_service.py](../vintasend/services/notification_service.py) — `:168`, `:1157`, all read methods, new identifier methods.
- [notification_backends/base.py](../vintasend/services/notification_backends/base.py) + [asyncio_base.py](../vintasend/services/notification_backends/asyncio_base.py) — concrete `get_backend_identifier`, `get_all_notifications`.
- [exceptions.py](../vintasend/exceptions.py) — `BackendNotFoundError`.
- `@vintasend/tests/test_services/test_multi_backend_reads.py` — new.

**Phase 2**

- [notification_service.py](../vintasend/services/notification_service.py) — `_execute_multi_backend_write`; every write method in both services.
- [notification_backends/base.py](../vintasend/services/notification_backends/base.py) + asyncio twin — concrete `apply_replication_snapshot_if_newer`.
- `@vintasend/tests/test_services/test_multi_backend_writes.py` — new.

**Phase 3**

- `@vintasend/services/notification_queue_services/replication_base.py` (+ asyncio twin, + stub) — new.
- [notification_service.py](../vintasend/services/notification_service.py) — `__init__`, `_execute_multi_backend_write` queued branch, `process_replication`, `replicate_notification`, `register_replication_queue_service`.
- [helpers.py](../vintasend/services/helpers.py), [app_settings.py](../vintasend/app_settings.py), [exceptions.py](../vintasend/exceptions.py) — `ReplicationError`.
- `@vintasend/tests/test_services/test_multi_backend_replication.py` — new.

**Phase 4**

- [notification_service.py](../vintasend/services/notification_service.py) — `verify_notification_sync`, `get_backend_sync_stats`.
- `@vintasend/tests/test_services/test_multi_backend_management.py` — new.

**Phase 5**

- [notification_service.py](../vintasend/services/notification_service.py) — `migrate_to_backend`; [exceptions.py](../vintasend/exceptions.py) — `BackendMigrationError`.
- `@vintasend/tests/test_services/test_backend_migration.py` — new.

**Phase 5b (cross-repo — `vintasend-django`, own PR, after core releases)**

- `vintasend_django/services/notification_backends/django_db_notification_backend.py` — `get_backend_identifier`, `apply_replication_snapshot_if_newer`, `get_all_notifications`.
- `vintasend_django/services/tests/…`; `pyproject.toml` — widen the `vintasend` pin.

**Phase 6**

- [README.md](../README.md), [RELEASE_NOTES.md](../RELEASE_NOTES.md), [pyproject.toml](../pyproject.toml).
