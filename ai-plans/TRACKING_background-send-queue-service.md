# Implementation Tracking — Background Send via Queue Service

- **Feature name**: background-send-queue-service
- **Plan**: `ai-plans/2026-07-22-BACKGROUND_SEND_QUEUE_SERVICE_IMPLEMENTATION_PLAN.md`
- **Started**: 2026-07-22
- **Last updated**: 2026-07-22
- **Feature flag**: none — the plan states the compatibility boundary is the 2.0 release plus its migration guide, not a runtime flag.

## Run options

| Option | Value |
|---|---|
| `pause_between_phases` | `false` (auto-flow) |
| `generate_inline_comments` | `false` (project default) |
| `full_test_suite` | `true` (full `poetry run pytest` on every outer gate) |
| `commit_strategy_resolved` | `stacked-branches` |
| `use_worktree` | `true` |
| `worktree_path` | `/Users/hugobessa/Workspaces/vintasend/.claude/worktrees/plan-background-send-queue-service` |
| `worktree_branch` | `plan-background-send-queue-service` |
| `worktree_summary` | `.vinta-ai-workflows/worktrees/plan-background-send-queue-service.yaml` |
| `sandbox_tier` | `enforced` (`sandbox-exec` present) |

`WORKROOT` = the worktree path above. `BASE_BRANCH` = `plan-background-send-queue-service`, cut from
`origin/main` @ `e0c93b7`. Every phase branch stacks on the previous one inside that worktree.

### Resolved models

| Role | Source | Tier | Model |
|---|---|---|---|
| Phase 1 implementer | plan | 2 | sonnet (phase touches >3 files) |
| Phase 2 implementer | plan | 4 | opus |
| Phase 3 implementer | plan | 3 | sonnet |
| Phase 4 implementer | plan | 2 | haiku |
| Reviewer | `agent_models.reviewer` | 4 | opus (Phase 2 also names reviewer tier 4) |
| Fixer | `agent_models.fixer` | 3 | sonnet |
| worktree_prep / integrate | `agent_models` | 1 | haiku |

## Environment notes

- The `ai-plans/*.md` plan files are untracked in this repo, so the plan was copied into the worktree
  by hand after provisioning. It is not committed by this run.
- `.claude/worktrees/` was added to `.git/info/exclude` (local-only, uncommitted) so the worktree
  cannot be swept into a commit from the main checkout.
- **Two other implement-plan sessions were active in the main checkout** when this run started,
  ping-ponging between `plan/attachment-manager-seam/phase-1` and
  `plan/notification-filtering-api/phase-1`. This run is isolated in its own worktree and is
  unaffected, but the three plans all target a shared 2.0 wave — see the plan's
  **Risk & Rollout Notes**.

## Completed phases

### Phase 1 — Add the queue-service seam ✅

- **Model**: sonnet (plan tier 2, stepped up per the tier-2 note because the phase touches >3 files).
- **Branch**: `plan/background-send-queue-service/phase-1`, based on `main`.
- **PR**: https://github.com/vintasoftware/vintasend/pull/7
- **Review**: reviewer opus (tier 4), fixer sonnet (tier 3) ×2 rounds. No BLOCKERs from the
  independent reviewer; one blocker found by the conductor's own plan-compliance walkthrough.
- **Final gate**: ruff clean, `mypy` clean on 48 source files, `pytest` 223 passed.

Summary of what landed:

- `BaseNotificationQueueService` / `AsyncIOBaseNotificationQueueService` in
  `vintasend/services/notification_queue_services/`, importing only `abc` and `uuid` so the
  `tasks/__init__.py` import cycle stays unreachable.
- `FakeQueueService` / `FakeAsyncIOQueueService` stubs recording ids in
  `enqueued_notification_ids`.
- `get_notification_queue_service` / `get_asyncio_notification_queue_service` in `helpers.py`.
- `NOTIFICATION_QUEUE_SERVICE` and `NOTIFICATION_SERVICE_FACTORY` settings, typed `str | None`,
  reaching the three framework dicts through the existing `{**DEFAULT_SETTINGS}` spread.
- Three exceptions: `NotificationQueueServiceMissingError`, `NotificationServiceFactoryError`,
  `NotificationQueueServiceResolutionError`.
- New shared test helper `vintasend/tests/utils.py` holding
  `_reset_notification_settings_singleton`, de-duplicated out of three test modules.

Two corrections made during review that later phases depend on:

1. The resolvers' unset-config guard used `is None`, but `get_config()` returns `{}` — not `None` —
   when `detect_framework()` is `"Unknown"`, which is the ordinary result for a plain Python worker.
   The guard now requires a non-empty `str`, so the typed error is raised instead of an
   `AttributeError` from `_import_class({})`.
2. The error contract was split. `NotificationQueueServiceMissingError` now means "nothing
   configured"; the new `NotificationQueueServiceResolutionError` means "configured but could not be
   imported, instantiated, or is the wrong type".

## Carried into Phase 2 — read before implementing

- **Catch the narrow error.** Phase 2's `NotificationService.__init__` must treat a missing queue
  service as benign, so it catches `NotificationQueueServiceMissingError` only. Letting
  `NotificationQueueServiceResolutionError` propagate is the point of the split: a typo'd
  `NOTIFICATION_QUEUE_SERVICE` must surface loudly instead of being read as "no queue configured",
  which would silently never deliver background notifications.
- **`NotificationServiceFactoryError`'s docstring already promises** it covers a factory that
  "cannot be imported **or called**". Phase 2 must raise it for invocation failure too, or amend the
  docstring.
- **`queue_service_kwargs` on both resolvers is currently unreachable** — no setting supplies it and
  the planned `__init__` signature has no matching parameter. Phase 2 decides whether to thread it
  through or drop it. It is spec'd by the plan, so it was left in place.
- **The seam's docstrings are a published 2.0 contract** for `vintasend-celery`: broker failures must
  be wrapped in a `NotificationError` subclass, returning means the broker accepted the id, and
  delivery is at-least-once. The Phase 2 worker must tolerate redelivery of the same id.

## Tracked follow-ups (not blocking any phase)

- `helpers.py` now holds six near-identical resolvers (~330 lines) of shape
  *import → instantiate → isinstance-check → cast*, differing only in expected base class, error
  class and a noun. A private `_resolve_instance(...)` with thin public wrappers would remove roughly
  200 lines. Deliberately deferred: the phase body said to follow the existing
  `get_notification_backend` pattern, and doing the refactor mid-plan would widen every phase diff.
  Worth its own small plan after the 2.0 lands.

### Phase 2 — Rewire the sync send path to id-only ✅

- **Model**: opus (plan tier 4).
- **Branch**: `plan/background-send-queue-service/phase-2`, stacked on phase-1.
- **PR**: https://github.com/vintasoftware/vintasend/pull/9 (base = phase-1 branch).
- **Review**: reviewer opus (tier 4), fixer sonnet (tier 3) ×1 round. The reviewer confirmed the
  send/delayed_send control-flow rewrite is correct — no path marks a notification sent without
  sending, none enqueues then marks failed, and the old early `return` is correctly a `continue`.
  One SHOULD-FIX: the abstract `delayed_send` docstring on `AsyncBaseNotificationAdapter` told
  implementers to deliver from that method, but core calls `adapter.send()` — fixed to say the
  method is only a marker and delivery belongs in `send()`.
- **Final gate**: ruff clean, `mypy` clean on 49 source files, `pytest` 252 passed. Acceptance grep
  `grep -rn "AsyncNotificationProtocol\|NotificationDict" vintasend/` returns nothing.

Summary of what landed:

- `NotificationService.__init__` gained `notification_queue_service` and `raise_on_failed_send`
  (both keyword, defaulted), plus `register_queue_service`. The `__init__` catches
  `NotificationQueueServiceMissingError` only, so a typo'd import string
  (`NotificationQueueServiceResolutionError`) propagates loudly.
- `send()` restructured: adapter loop runs before context generation; a background adapter with a
  queue service enqueues `notification.id` and `continue`s with no context and no status change; no
  queue service logs and continues, raising only under `raise_on_failed_send=True`.
- `delayed_send(notification_id)`: reloads the notification, skips it if already
  `SENT`/`READ`/`CANCELLED` (at-least-once redelivery guard), generates context at delivery time,
  sends, marks. Errors when no background adapter matched.
- `async_base.py`: deleted `NotificationDict`, `OneOffNotificationDict`, `AsyncNotificationProtocol`
  and its eight serialize/restore hooks. `AsyncBaseNotificationAdapter` is now a marker with one
  abstract `delayed_send(notification_id)`.
- `background_tasks.py` rewritten: `get_notification_service()` resolves `NOTIFICATION_SERVICE_FACTORY`
  once and caches at module scope (a failed resolution is not cached);
  `send_notification(notification_id, notification_service=None)` swallows all exceptions so a
  poisoned task cannot crash a worker. `periodic_tasks.py` builds the service from the factory.
- `raise_on_failed_send=False` is applied at all seven failure points across the two methods, both
  modes tested. Attachments now survive the background round trip — proven by a test reading the
  stored bytes back, the case `PlaceholderAttachmentFile` made impossible.

Decisions carried into later phases:

- `queue_service_kwargs` was deliberately **not** threaded through `__init__` — no setting supplies
  it and the plan's signature has no matching parameter. A host needing per-instance kwargs uses
  `register_queue_service` with a pre-built instance. The resolver parameter stays (Phase 1 spec'd it).
- `delayed_send` and `register_queue_service` remain on `ServiceMethodParityTestCase`'s allowlist of
  intentional sync/AsyncIO differences. **Phase 3 removes them from the allowlist** once the AsyncIO
  service gains both.
- The `delayed_send` abstract method on the adapter is vestigial (core never calls it; it is only a
  subclass marker). The structural cleanup of the duplicated failure-handling ladder in
  `send`/`delayed_send` is deferred to after Phase 3, when all four sync/async variants exist.

## Carried into Phase 3 — read before implementing

- Mirror the Phase 2 sync work onto `AsyncIONotificationService`: `__init__` gains
  `notification_queue_service` (typed `AsyncIOBaseNotificationQueueService | str | None`) and
  `raise_on_failed_send`; add async `register_queue_service`; add the enqueue branch to `send`
  (awaiting `enqueue_notification`); add a new async `delayed_send(notification_id)` mirroring the
  sync version including the redelivery guard.
- **Naming decision (was Open Question in the plan):** the asyncio background-marker adapter is named
  `AsyncIOBackgroundNotificationAdapter`, in a new module
  `vintasend/services/notification_adapters/asyncio_background_base.py`. The sync
  `AsyncBaseNotificationAdapter` is **renamed** to `BackgroundNotificationAdapter`, with
  `AsyncBaseNotificationAdapter` kept as a deprecated alias (cheap in a major, and it untangles the
  `async_base`/`asyncio_base` naming trap AGENTS.md calls out). This is the conductor's resolution of
  the plan's Open Question and Touch List — see the run log.
- `background_tasks.py` gains `async_send_notification(notification_id)` for asyncio hosts.
- Extend the parity test so `delayed_send` and `register_queue_service` leave the allowlist.

## Current phase

Phase 3 — Background sending on the asyncio service.

## Remaining phases
- Phase 4 — Documentation and 2.0 migration guide.

## Deferred phases

Cross-repo work, called out by the plan as out of scope and living in its own repo:

- `vintasend-celery` — `CeleryNotificationAdapter` becomes a `CeleryNotificationQueueService`; most of
  `celery_adapter_factory.py` (dict conversion, attachment serialization, `PlaceholderAttachmentFile`)
  becomes dead. Core must release first.
- `vintasend-sqlalchemy` — no required code change; its README should document the
  `NOTIFICATION_SERVICE_FACTORY` pattern as the supported way to give a worker a live session.

No flag-removal phase exists for this plan.
