# Background Send via Queue Service — Implementation Plan

Ports the `vintasend-ts` background-send architecture: an injectable queue-service seam, an id-only
queue payload, and context generation in the worker. **This is a 2.0 plan** — it changes the
signature of an existing seam method.

## 1. Goals

1. Introduce `BaseNotificationQueueService` / `AsyncIOBaseNotificationQueueService` as an injectable
   seam whose single method takes a notification id, so enqueueing stops being the adapter's job.
2. Reduce the queue payload to the notification id alone, deleting the serialize/restore protocol
   and, with it, the reason `vintasend-celery` cannot send attachments in the background.
3. Move context generation from the enqueueing process into the worker, so a scheduled notification
   renders against data current at delivery time on the background path as it already does on the
   foreground path.
4. Give `AsyncIONotificationService` background-send support, which it has never had.

Non-goals:

- **Multi-backend replication.** `BaseNotificationReplicationQueueService`, `processReplication`,
  `verifyNotificationSync`, `getBackendSyncStats` and `migrateToBackend` are a separate feature that
  happens to also use a queue. Out of scope entirely; no partial scaffolding for it.
- **Logger injection.** TS routes every diagnostic on this path through an injected `BaseLogger`.
  Python keeps module-level `logging` here; the logger seam is its own gap.
- **Removing `AsyncBaseNotificationAdapter`.** Background support stays declared by subclassing, per
  the Step 0 decision. This plan reshapes the class's contract; it does not replace the mechanism
  with a boolean flag.
- **Periodic-task redesign.** [periodic_tasks.py](../vintasend/tasks/periodic_tasks.py) is simplified
  as a consequence of the payload change, not redesigned.
- **A `vintasend-celery` release.** Downstream scope for this plan is core + `vintasend-django`;
  Celery is the most affected package and gets its own plan in its own repo.

## 2. Guiding Decisions

| Decision | Resolution |
|---|---|
| **Id-only payload** | TS enqueues `notificationId` and nothing else; the worker reloads the row. That deletes the entire reason Python serializes a notification dict, three import strings, backend kwargs and config into every task. It also fixes attachments-in-background for free: `vintasend-celery`'s `PlaceholderAttachmentFile` exists solely because file handles cannot be serialized, and under an id-only payload the worker reads the real attachment from the backend. |
| **Host-supplied service factory in the worker** | The id-only payload forces the question "where does the worker's service come from?". Settings alone cannot answer it: `NOTIFICATION_BACKEND` yields an import string and kwargs, but a SQLAlchemy backend needs a live session or engine. That is precisely what `serialize_config`/`restore_config` works around today — see [example_app/celery.py:48-59](../implementations/vintasend-celery/example_app/celery.py#L48-L59) round-tripping a `Decimal` and a `datetime`, and [fake_backend.py:544](../vintasend/services/notification_backends/stubs/fake_backend.py#L544), which exists to test it. A `NOTIFICATION_SERVICE_FACTORY` setting pointing at a host callable moves construction to the host, where the session already lives. This matches TS, whose worker calls `delayedSend(id)` on a service the application built. |
| **Factory result is cached per process** | The worker resolves and calls the factory once, then reuses the service. Rebuilding a backend per task is what the current design does and it is wasteful; more importantly, an ORM session should be process-scoped, not task-scoped. Document that the factory must be safe to call once per worker process. |
| **`delayed_send` changes signature — 2.0** | `AsyncBaseNotificationAdapter.delayed_send` is abstract with `(notification_dict, context_dict)`; the id-only design makes it `(notification_id)`. `AGENTS.md` classes renaming or reordering seam parameters as a major bump, and this is worse than a rename. Rather than carry a second method name for a release, this ships as 2.0 with a migration guide, following the `MIGRATION_TO_1.0.0.md` precedent. |
| **Serialize/restore hooks are deleted, not deprecated** | Under the factory design they have nothing to do: no kwargs, no config, and no import strings cross the wire. Keeping them as no-ops would leave eight abstract-adjacent methods on the seam that mean nothing, and downstream implementers would keep implementing them. A 2.0 is the right place to remove them outright. |
| **`raise_on_failed_send` defaults to `False`** | Matches TS's `VintaSendOptions.raiseErrorOnFailedSend`. This is a real behavioural break — existing callers stop seeing `NotificationSendError` and friends — and it is acceptable only because this release is already a major. It gets its own `### Backwards compatibility` paragraph and a migration-guide entry telling callers to pass `raise_on_failed_send=True` to keep 1.x semantics. |
| **Worker and web process must share settings** | A new operational constraint that does not exist today, since the payload currently carries everything the worker needs. It is inherent to the id-only design and must be stated in the README and migration guide, not discovered in production. |
| **No feature flag** | This library has no feature-flag module and no runtime to flip one in. The compatibility boundary is the 2.0 release plus its migration guide. Introducing flag infrastructure to gate a major-version architecture change would mean shipping both architectures side by side indefinitely. |

## 3. Data Model Changes

No persisted-model changes. This plan changes what travels over the queue, not what is stored.

### 3.1 Types deleted

From [async_base.py](../vintasend/services/notification_adapters/async_base.py): `NotificationDict`
(lines 9-23), `OneOffNotificationDict` (lines 25-41), and the entire `AsyncNotificationProtocol`
(lines 43-65) with its eight serialize/restore members. `AsyncBaseNotificationAdapter` keeps only
`delayed_send`, reshaped.

### 3.2 New seam

```python
# vintasend/services/notification_queue_services/base.py
class BaseNotificationQueueService(ABC):
    @abstractmethod
    def enqueue_notification(self, notification_id: int | str | uuid.UUID) -> None: ...


# vintasend/services/notification_queue_services/asyncio_base.py
class AsyncIOBaseNotificationQueueService(ABC):
    @abstractmethod
    async def enqueue_notification(self, notification_id: int | str | uuid.UUID) -> None: ...
```

Kept free of imports from `notification_service` — see **Risk & Rollout Notes** on the import cycle.

### 3.3 New settings

`NOTIFICATION_QUEUE_SERVICE` and `NOTIFICATION_SERVICE_FACTORY`, both dotted import strings. Each
must be threaded through all six layers `Skill(add-env-var)` enumerates: the `NotificationSettingsDict`
TypedDict, `DEFAULT_SETTINGS`, the Django / Flask / FastAPI default dicts, the `NotificationSettings`
singleton, tests, and the README. Note the env-var-wins precedence and that `get_config` returns `{}`
rather than `None` when no framework is detected.

## 4. API Design

### 4.1 Service construction

Both services gain two keyword arguments, defaulted so existing positional construction is
unaffected:

```python
NotificationService(
    notification_adapters, notification_backend, notification_backend_kwargs=None, config=None,
    notification_queue_service: BaseNotificationQueueService | str | None = None,
    raise_on_failed_send: bool = False,
)
```

Accepting either an instance or an import string mirrors how adapters and backends are already
accepted. Plus `register_queue_service(queue_service)` for post-construction injection, matching TS's
`registerQueueService`.

### 4.2 Send path

`send(notification)` gains an enqueue branch **before** context generation:

- Adapter is an `AsyncBaseNotificationAdapter` and a queue service is configured →
  `queue_service.enqueue_notification(notification.id)`, then `continue`. No context generated, no
  status change.
- Adapter is an `AsyncBaseNotificationAdapter` and no queue service is configured → log an error and
  `continue`; raise `NotificationQueueServiceMissingError` only when `raise_on_failed_send=True`.
- Otherwise → today's foreground path, unchanged.

### 4.3 Worker entrypoint

`delayed_send(notification_id)` on both services: load the notification from the backend, select
adapters declaring background support, generate context, call `adapter.send(notification, context)`,
then `mark_pending_as_sent` / `mark_pending_as_failed` and `store_context_used`.

`vintasend.tasks.background_tasks.send_notification(notification_id)` resolves the cached service
from `NOTIFICATION_SERVICE_FACTORY` and delegates.

## 5. Phased Rollout

Phases are bundled per the Step 0 answer, but the seam, the sync path and the asyncio path stay
separate so each is independently reviewable and revertible.

### Phase 1 — Add the queue-service seam

**Goal**: the queue-service ABCs, their stubs, and the settings plumbing exist and are constructible.
Ship value: none on its own — nothing calls them yet. Justified as a foundation phase because
Phases 2 and 3 both consume it, and landing the seam separately lets downstream implementers start
against a published interface while the service rewiring is still in review.

**Feature flag**: none — purely additive new surface, per **Guiding Decisions**.

Changes:

1. New `@vintasend/services/notification_queue_services/__init__.py`, `base.py`, `asyncio_base.py`
   as shown in **Data Model Changes**. Import only ABC machinery and typing — no import of
   `notification_service`, `helpers`, or the backend bases.
2. New `@vintasend/services/notification_queue_services/stubs/fake_queue_service.py`:
   `FakeQueueService` and `FakeAsyncIOQueueService`, recording enqueued ids in a list. Per
   `AGENTS.md`, stubs are a deliverable and must be complete, not raise.
3. [helpers.py](../vintasend/services/helpers.py): `get_notification_queue_service(import_str, kwargs, config)`
   and its asyncio twin, following the existing `get_notification_backend` pattern at `:149-206`.
4. [app_settings.py](../vintasend/app_settings.py): add `NOTIFICATION_QUEUE_SERVICE` and
   `NOTIFICATION_SERVICE_FACTORY` across all six layers.
5. [exceptions.py](../vintasend/exceptions.py): add `NotificationQueueServiceMissingError` and
   `NotificationServiceFactoryError`, both deriving from `NotificationError`.

Spec use-case: no spec — ports `BaseNotificationQueueService` from `vintasend-ts`.

Tests:

- **Unit**: `@vintasend/tests/test_services/test_notification_queue_services.py` — the fakes record
  ids; both ABCs reject instantiation when `enqueue_notification` is unimplemented; the helper
  resolves an import string and raises a typed error on a bad one.
- **Integration**: `@vintasend/tests/test_app_settings.py` — the two new settings resolve from env
  vars and from each framework's config, env wins, and an unset value reads correctly given
  `get_config()` returns `{}`.

**Suggested AI model**: Tier 2 (IDs in [resources/ai-models.yaml](../.claude/skills/plan-feature/resources/ai-models.yaml)).
Small new ABCs plus settings plumbing with exact precedent, but the six-layer settings work is easy
to do incompletely.

**Reusable skills**: `Skill(add-env-var)` for the two settings.

Acceptance: `FakeQueueService().enqueue_notification("abc")` records the id, both new settings
resolve from env and framework config, and the full existing suite passes with no service behaviour
changed.

### Phase 2 — Rewire the sync send path to id-only

**Goal**: a sync service with a queue service enqueues only the notification id, and a worker
reconstitutes the service from the host factory, loads the notification, generates context and sends.

**Feature flag**: none — this is the 2.0 architecture change itself, gated by the release.

Changes:

1. [async_base.py](../vintasend/services/notification_adapters/async_base.py): delete
   `NotificationDict`, `OneOffNotificationDict` and `AsyncNotificationProtocol` in full. Reduce
   `AsyncBaseNotificationAdapter` to `@abstractmethod delayed_send(self, notification_id) -> None`.
2. [notification_service.py](../vintasend/services/notification_service.py)
   `NotificationService.__init__` ([:99-140](../vintasend/services/notification_service.py#L99-L140)):
   accept `notification_queue_service` and `raise_on_failed_send`; add `register_queue_service`.
3. `NotificationService.send` ([:234-287](../vintasend/services/notification_service.py#L234-L287)):
   restructure so the adapter loop runs **before** `get_notification_context` at `:249`. Add the
   enqueue branch per **API Design**. Replace the `isinstance(...): return` at `:266-267` — that
   early return also skips every remaining adapter, so it becomes `continue`. Honour
   `raise_on_failed_send` at each failure point.
4. `NotificationService.delayed_send` (`:710-756`): resignature to `(notification_id)`. Load via
   `self.notification_backend.get_notification(...)`, select adapters by background support,
   generate context, send, mark. Replace the `return None` at `:730` — it currently abandons the
   whole loop on the first non-background adapter — with `continue`, plus a guard that errors when
   no background adapter matched.
5. [background_tasks.py](../vintasend/tasks/background_tasks.py): replace the whole file.
   `send_notification(notification_id)` resolves `NOTIFICATION_SERVICE_FACTORY` once, caches the
   service at module scope, and calls `delayed_send`. Keep the `logger.exception` swallow so a
   poisoned task cannot crash a worker.
6. [periodic_tasks.py](../vintasend/tasks/periodic_tasks.py) (`:14-31`): delete the adapter scan
   that existed only to find `restore_backend_kwargs` / `restore_config`. Build the service from the
   factory.
7. [fake_adapter.py](../vintasend/services/notification_adapters/stubs/fake_adapter.py): update
   `FakeAsyncEmailAdapter` to the new `delayed_send`; delete `notification_from_dict` /
   `one_off_notification_from_dict` (`:117-187`).

Spec use-case: no spec — ports TS's `send` enqueue branch and `delayedSend` worker entrypoint.

Tests:

- **Unit**: [test_notification_service.py](../vintasend/tests/test_services/test_notification_service.py)
  — enqueue branch records exactly the id and generates **no** context; missing queue service logs
  and continues under `raise_on_failed_send=False` and raises under `True`; a foreground adapter and
  a background adapter configured together both run (the `continue` fix); `delayed_send` loads,
  generates context and marks sent; context is generated at delivery time, proven with `freeze_time`.
- **Integration**: `@vintasend/tests/test_tasks/test_background_tasks.py` — **new; this module does
  not exist today**. Factory resolution, per-process caching, id-only round trip end to end against
  `FakeFileBackend`, and a notification with attachments surviving the round trip — the case
  `PlaceholderAttachmentFile` made impossible.
- **Regression**: [test_periodic_tasks.py](../vintasend/tests/test_tasks/test_periodic_tasks.py)
  `:52-88` patches `restore_backend_kwargs` and `restore_config`, which no longer exist. Rewrite
  against the factory.

**Suggested AI model**: Tier 4. Restructuring the send path while preserving every existing
foreground guarantee, changing a seam contract, and introducing process-scoped caching in a worker
— multi-file, subtle ordering, and the failure mode is silently unsent notifications.

**Review models**: reviewer Tier 4 — this phase rewrites the send path's control flow and inverts the
error-raising contract. A missed branch means notifications that are enqueued and never delivered, or
marked sent without being sent, with no exception to signal it.

Acceptance: with a `FakeQueueService` and a background adapter, `send()` enqueues exactly
`notification.id` and generates no context; `send_notification(notification_id)` in a worker with
`NOTIFICATION_SERVICE_FACTORY` set delivers the notification including its attachments and marks it
sent; `grep -r "AsyncNotificationProtocol\|NotificationDict" vintasend/` returns nothing.

### Phase 3 — Background sending on the asyncio service

**Goal**: `AsyncIONotificationService` gains the background-send support it has never had, restoring
the sync/AsyncIO parity `AGENTS.md` requires.

**Feature flag**: none — additive new surface on the asyncio service.

Changes:

1. [notification_service.py](../vintasend/services/notification_service.py)
   `AsyncIONotificationService.__init__` (`:767-804`): accept
   `notification_queue_service: AsyncIOBaseNotificationQueueService | str | None` and
   `raise_on_failed_send`; add `register_queue_service`.
2. `AsyncIONotificationService.send` (`:883-960`): the enqueue branch, awaiting
   `enqueue_notification`.
3. New `AsyncIONotificationService.delayed_send(notification_id)`, mirroring the sync version.
4. New `AsyncIOAsyncBaseNotificationAdapter` — the asyncio counterpart of the background marker.
   **Name this deliberately**: the existing `async_base` / `asyncio_base` split is already described
   in `AGENTS.md` as a trap, and a third name compounds it. Prefer
   `AsyncIOBackgroundNotificationAdapter` and, if adopted, rename the sync one to
   `BackgroundNotificationAdapter` with the old name kept as a deprecated alias — cheap in a major.
5. [background_tasks.py](../vintasend/tasks/background_tasks.py): `async_send_notification(notification_id)`
   for asyncio hosts, alongside the sync entrypoint.
6. Update the asyncio stubs to match.

Spec use-case: no spec — net-new surface required by the sync/AsyncIO parity invariant.

Tests:

- **Unit**: `AsyncIONotificationServiceTestCase` in
  [test_notification_service.py](../vintasend/tests/test_services/test_notification_service.py) —
  every Phase 2 sync assertion, mirrored.
- **Integration**: `@vintasend/tests/test_tasks/test_background_tasks.py` — asyncio round trip
  against `FakeAsyncIOFileBackend`.
- **Parity**: extend the public-method-set parity test from the
  [shared-helpers plan](2026-07-22-SHARED_SERVICE_HELPERS_IMPLEMENTATION_PLAN.md) so `delayed_send`
  and `register_queue_service` are no longer on the allowlist of intentional differences.

**Suggested AI model**: Tier 3. Mirrors Phase 2's now-established shape, but genuinely new surface
rather than a translation, and the naming decision needs judgement.

Acceptance: `AsyncIONotificationService` exposes `delayed_send` and `register_queue_service`, an
asyncio background round trip delivers and marks sent, and the parity test passes without
`delayed_send` on its allowlist.

### Phase 4 — Documentation and 2.0 migration guide

**Goal**: an application on 1.x can follow a written path to 2.0 without reading the diff.

**Feature flag**: none — documentation.

Changes:

1. New `@MIGRATION_TO_2.0.0.md`, modelled on
   [MIGRATION_TO_1.0.0.md](../MIGRATION_TO_1.0.0.md). Must cover: the `delayed_send` signature
   change; deletion of `AsyncNotificationProtocol` and its eight hooks; the
   `NOTIFICATION_SERVICE_FACTORY` requirement and a worked example returning a service with a live
   SQLAlchemy session; the worker/web shared-settings constraint; `raise_on_failed_send` defaulting
   to `False` and how to restore 1.x behaviour; and attachments now working in background sends.
2. [README.md](../README.md): queue-service section, factory wiring, the AsyncIO note at `:265-310`.
3. [RELEASE_NOTES.md](../RELEASE_NOTES.md): 2.0.0 entry with `### Backwards compatibility`.
4. [pyproject.toml](../pyproject.toml): version to 2.0.0.

Spec use-case: no spec — release documentation.

Tests: none beyond `poetry run pytest` staying green. Verify every code sample in the migration guide
actually runs against the new API rather than being written from memory.

**Suggested AI model**: Tier 2. Prose, but it must be accurate against the shipped API — an
aspirational migration guide is worse than none.

**Reusable skills**: `Skill(release-package)` for version choice and release-note shape;
`Skill(deslop-comments)` over the new prose.

Acceptance: `MIGRATION_TO_2.0.0.md` exists covering all six breaking changes above, every code sample
in it executes, and `RELEASE_NOTES.md` carries a 2.0.0 entry with a `### Backwards compatibility`
section.

## 6. Risk & Rollout Notes

- **This is a major release.** Coordinate with the [attachment](2026-07-22-ATTACHMENT_MANAGER_SEAM_IMPLEMENTATION_PLAN.md)
  and [filtering](2026-07-22-NOTIFICATION_FILTERING_API_IMPLEMENTATION_PLAN.md) plans so downstream
  packages absorb one breaking wave, not three. Suggested order: ship the
  [correctness fixes](2026-07-22-NOTIFICATION_CORRECTNESS_FIXES_IMPLEMENTATION_PLAN.md) and
  [shared helpers](2026-07-22-SHARED_SERVICE_HELPERS_IMPLEMENTATION_PLAN.md) as 1.x, then land all
  three ports behind a single 2.0.
- **Celery task-name and wire-format break.** `celery_app.task(send_notification)` binds the task
  name to the core function's `__name__`. Tasks already queued when the worker upgrades carry the old
  payload and will fail against the new signature. Operational requirement: **drain the queue before
  deploying the 2.0 worker**, or register the new entrypoint under a new task name and run both
  workers until the old queue empties. This must be in the migration guide as a deploy step, not a
  footnote.
- **Worker and web must share `NOTIFICATION_*` settings.** New constraint, inherent to the id-only
  design. A worker with a different `NOTIFICATION_BACKEND` silently fails to find notifications.
- **Import cycle.** [tasks/__init__.py](../vintasend/tasks/__init__.py) does
  `from .background_tasks import *`, which transitively imports `NotificationService`. The queue
  service modules must not import `notification_service`; use `TYPE_CHECKING` guards, as
  [notification_adapters/base.py:8-14](../vintasend/services/notification_adapters/base.py#L8-L14)
  already does.
- **Settings singleton.** `NotificationSettings` uses `SingletonMeta`, so config is read on first
  construction only and `_instances` is a `MappingProxyType`. A worker whose factory builds a service
  with different config gets the first-seen settings. Test isolation needs the singleton reset;
  document the supported way to do it.
- **`raise_on_failed_send=False` is a silent behavioural break.** Callers currently catching
  `NotificationSendError` stop seeing it. Migration guide must lead with this.
- **Rollback**: Phases 1 and 4 are independently revertible. Phases 2 and 3 are not safely
  revertible once a 2.0 worker has drained a 2.0 queue — rolling back means the old worker cannot
  read id-only payloads. Treat the 2.0 deploy as forward-only and stage it: deploy the new worker
  alongside the old, cut over, then retire the old.
- **Downstream**: `vintasend-celery` is rewritten by this change — its `CeleryNotificationAdapter`
  becomes a `CeleryNotificationQueueService` and most of `celery_adapter_factory.py` (the dict
  conversion, attachment serialization, `PlaceholderAttachmentFile`) becomes dead. That is its own
  plan in its own repo, and core must release first. `vintasend-django` is affected only if its
  adapter subclasses the background base. `vintasend-sqlalchemy` is the main beneficiary — the
  factory is what lets a live async session exist in a worker.

## 7. Open Questions

| Question | Recommended default |
|---|---|
| Should `NOTIFICATION_SERVICE_FACTORY` be a setting, or should the worker entrypoint take the service directly? | **Setting, with the entrypoint accepting an optional override.** The setting keeps `send_notification` a zero-argument-configuration Celery task, which is what makes `celery_app.task(send_notification)` work. An optional parameter covers hosts that would rather wire it explicitly. |
| Rename `AsyncBaseNotificationAdapter` → `BackgroundNotificationAdapter` in this major? | **Yes.** `AGENTS.md` already calls the `async_base` / `asyncio_base` naming a trap, Phase 3 adds a third class to the confusion, and a major is the only cheap moment to fix it. Keep the old name as a deprecated alias. |
| Should the factory be called once per process or once per task? | **Once per process, cached at module scope.** An ORM session should outlive a single task. If a host needs per-task construction, it can return a fresh service from the factory itself — but the default should not rebuild a connection pool per notification. |
| Does `store_context_used` still make sense when context is generated in the worker? | **Yes, unchanged.** It records what was actually rendered, which is now strictly more accurate — it captures the worker's context rather than the enqueueing process's. Worth a sentence in the migration guide since the stored value may now differ for scheduled notifications. |

## 8. Touch List

**Phase 1**

- `@vintasend/services/notification_queue_services/__init__.py`, `base.py`, `asyncio_base.py` — new.
- `@vintasend/services/notification_queue_services/stubs/__init__.py`, `fake_queue_service.py` — new.
- `@vintasend/tests/test_services/test_notification_queue_services.py` — new.
- [helpers.py](../vintasend/services/helpers.py) — resolver functions, following `:149-206`.
- [app_settings.py](../vintasend/app_settings.py) — six layers, two settings.
- [exceptions.py](../vintasend/exceptions.py) — two new exceptions.

**Phase 2**

- [async_base.py](../vintasend/services/notification_adapters/async_base.py) — lines 9-108, mostly deleted.
- [notification_service.py](../vintasend/services/notification_service.py) — `:99-140`, `:234-287`, `:710-756`.
- [background_tasks.py](../vintasend/tasks/background_tasks.py) — whole file replaced.
- [periodic_tasks.py](../vintasend/tasks/periodic_tasks.py) — `:14-37`.
- [fake_adapter.py](../vintasend/services/notification_adapters/stubs/fake_adapter.py) — `:94-187`.
- `@vintasend/tests/test_tasks/test_background_tasks.py` — new.
- [test_periodic_tasks.py](../vintasend/tests/test_tasks/test_periodic_tasks.py) — `:52-88` rewritten.

**Phase 3**

- [notification_service.py](../vintasend/services/notification_service.py) — `:767-804`, `:883-960`,
  new `delayed_send`.
- `@vintasend/services/notification_adapters/asyncio_background_base.py` — new (name per Open Questions).
- [background_tasks.py](../vintasend/tasks/background_tasks.py) — asyncio entrypoint.
- [test_notification_service.py](../vintasend/tests/test_services/test_notification_service.py) —
  asyncio mirrors.

**Phase 4**

- `@MIGRATION_TO_2.0.0.md` — new.
- [README.md](../README.md) — `:155-160`, `:230-232`, `:265-310`.
- [RELEASE_NOTES.md](../RELEASE_NOTES.md), [pyproject.toml](../pyproject.toml).

**Cross-repo (not in this plan — separate plans in their own repos)**

- `vintasend-celery`: `celery_adapter_factory.py` largely deleted; new
  `CeleryNotificationQueueService`; task factories rewired; `example_app/celery.py` rewired; README
  actually documenting the wiring. Core releases first.
- `vintasend-sqlalchemy`: no required change, but the factory pattern should be documented in its
  README as the supported way to give a worker a live session.
