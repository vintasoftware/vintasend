# Notification Correctness Fixes — Implementation Plan

Two defects found while comparing this library against
[vintasend-ts](https://github.com/vintasoftware/vintasend-ts). Neither belongs to any of the three
architecture ports; both are small, independent, and shippable now.

## 1. Goals

1. Reject duplicate adapters for the same notification type at service-construction time, so a
   notification can never be sent twice and have its status corrupted by the second send.
2. Validate `email_or_phone` when a one-off notification is created, so malformed or empty
   recipients fail at creation rather than silently at delivery.
3. Land both in one release with an explicit `### Backwards compatibility` note, since (1) rejects a
   configuration that is accepted today.

Non-goals:

- **No adapter-selection redesign.** The fix is a constructor-time guard, not a change to how
  adapters are matched to notification types at send time.
- **No `resend`/retry.** The dead `mark_pending_as_sent` path this exposes might tempt a retry
  feature; that lands in the [filtering plan](2026-07-22-NOTIFICATION_FILTERING_API_IMPLEMENTATION_PLAN.md).
- **No phone-number normalization or an `email-validator` dependency.** Format validation only,
  using the stdlib. The runtime dependency set stays at three.
- **No validation on `update_notification`.** One-off notification updates are a separate TS method
  (`updateOneOffNotification`) that Python does not have; adding it is out of scope.

## 2. Guiding Decisions

| Decision | Resolution |
|---|---|
| **Reject duplicates at construction, not at send** | Raising when the service is built makes the misconfiguration fail loudly at application startup, where it is trivially diagnosable, instead of on the first send of a given type in production. This is what `vintasend-ts` chose in its v0.13.0 breaking change, with the stated rationale that duplicates "end up messing with the statuses, as notification could be sent successfully by one adapter and fail on another". |
| **Raise, don't warn or de-duplicate silently** | Silently dropping the second adapter would make a real configuration error invisible, and picking *which* duplicate to keep is arbitrary. Two adapters for one type is never intentional — the sender is already looping over all matching adapters with no `break`. |
| **New exception subclass, not bare `ValueError`** | `AGENTS.md` requires exceptions to derive from `NotificationError` in [exceptions.py](../vintasend/exceptions.py). Add `DuplicateNotificationAdapterError` and `InvalidOneOffNotificationRecipientError` rather than raising `ValueError` directly. `NotificationError` already derives from `ValueError`, so existing `except ValueError` handlers keep working. |
| **Error message names the offending type and adapters** | Mirror TS's message shape — `Duplicate adapter notification types are not allowed. Found duplicates for: EMAIL (adapter-a, adapter-b)` — because the whole value of a startup guard is that it tells you what to delete. Use `adapter_import_str`, which every adapter already sets in `BaseNotificationAdapter.__init__`. |
| **Validate format, not deliverability** | `email_or_phone` is a single field holding either an email or a phone number, so validation is a two-branch regex check matching TS's: `^.+@.+\..+$` for email, `^\+?[0-9]{10,15}$` for phone. Deliberately permissive — the goal is catching empty strings and obvious garbage, not implementing RFC 5322. |
| **No feature flag** | Both changes are corrections to defects in a library with no flag infrastructure and no runtime to flip a flag in. Gating a validation fix behind a flag would mean shipping the known-broken path as the default. The release is the compatibility boundary; the `### Backwards compatibility` note is how it is communicated. |
| **Minor bump** | No seam method is added, renamed, or removed, so this is not a major under the `AGENTS.md` rules. But a configuration accepted at 1.2.0 now raises, which is more than a patch. Minor, with the mandatory note. |

## 3. Data Model Changes

No dataclass, model, or seam signature changes.

### 3.1 New exceptions

Both added to [exceptions.py](../vintasend/exceptions.py), deriving from the existing
`NotificationError`:

```python
class DuplicateNotificationAdapterError(NotificationError):
    """Raised when two or more adapters declare the same notification type."""


class InvalidOneOffNotificationRecipientError(NotificationError):
    """Raised when a one-off notification's email_or_phone is empty or malformed."""
```

Nothing is exported from `vintasend/__init__.py` — it is empty by convention and this plan does not
introduce a package-root export surface.

## 4. Phased Rollout

Two phases. The Step 0 answer opted into bundling closely-related use-cases, but these two fixes
share nothing beyond being small: different files, different call sites, different tests, different
blast radius. Bundling them would produce one PR whose revert drags an unrelated fix with it.

### Phase 1 — Reject duplicate adapters per notification type

**Goal**: an application configured with two adapters for the same notification type fails at
service construction with a message naming both, instead of double-sending every notification of
that type.

**Feature flag**: none — defect correction in a library with no flag module, per **Guiding Decisions**.

The defect, at [notification_service.py:258-287](../vintasend/services/notification_service.py#L258-L287):
the adapter loop has no `break`. With two EMAIL adapters, both `adapter.send(...)` calls run, then
`mark_pending_as_sent` runs twice — and the second call raises `NotificationUpdateError` (wrapped as
`NotificationMarkSentError`) because the row is no longer `PENDING_SEND`. If the first adapter fails
and the second succeeds, the notification is marked FAILED and then overwritten as SENT.
`AsyncIONotificationService.send` has the identical shape at `:909-938`.

Changes:

1. [exceptions.py](../vintasend/exceptions.py): add `DuplicateNotificationAdapterError`.
2. New module-level `validate_unique_adapter_notification_types(adapters)` in
   [notification_service.py](../vintasend/services/notification_service.py) — group the adapters by
   `adapter.notification_type`, collect every group with more than one member, and raise naming each
   type and the `adapter_import_str` of every adapter in it. A single shared function, called from
   both service constructors; do not write it twice.
3. `NotificationService.__init__` ([:99-140](../vintasend/services/notification_service.py#L99-L140)):
   call the validator immediately after `self.notification_adapters` is resolved, so it covers both
   live instances and adapters built from import strings.
4. `AsyncIONotificationService.__init__` (`:767-804`): same call, same position.

Spec use-case: no spec — parity with `vintasend-ts` v0.13.0, which fixed this as a deliberate
breaking change.

Tests:

- **Unit**: [test_notification_service.py](../vintasend/tests/test_services/test_notification_service.py)
  — constructing a service with two `FakeEmailAdapter`s raises `DuplicateNotificationAdapterError`;
  the message contains the notification type and both import strings; two adapters of *different*
  types construct fine; a single adapter constructs fine; the same assertions on
  `AsyncIONotificationServiceTestCase`.
- **Integration**: a test that would have caught the original bug — with the guard temporarily
  bypassed, assert `send()` on a two-EMAIL-adapter service double-sends. Keep it as a regression
  marker documenting *why* the guard exists, or drop it if bypassing the guard proves too invasive;
  the constructor tests are the ones that must exist.
- **Existing suite**: check no fixture in `vintasend/tests/` or in the Django submodule's tests
  builds a service with duplicate types. If one does, that fixture is itself the bug.

**Suggested AI model**: Tier 2 (IDs in [resources/ai-models.yaml](../.claude/skills/plan-feature/resources/ai-models.yaml)).
Small diff across two constructors plus tests, exact precedent available in the TS implementation.

Acceptance: `NotificationService([FakeEmailAdapter(...), FakeEmailAdapter(...)], backend)` raises
`DuplicateNotificationAdapterError` naming `EMAIL` and both adapters, on both the sync and asyncio
services, and the full existing suite still passes.

### Phase 2 — Validate one-off notification recipients

**Goal**: creating a one-off notification with an empty or malformed `email_or_phone` fails
immediately with a clear error, instead of persisting and failing at delivery time.

**Feature flag**: none — same rationale as Phase 1.

Today `create_one_off_notification`
([notification_service.py:345-392](../vintasend/services/notification_service.py#L345-L392)) passes
`email_or_phone` straight to the backend with no checks. An empty string persists happily.

Changes:

1. [exceptions.py](../vintasend/exceptions.py): add `InvalidOneOffNotificationRecipientError`.
2. New `validate_email_or_phone(email_or_phone)` in
   [service_utils.py](../vintasend/services/service_utils.py) — the module created by the
   [shared-helpers plan](2026-07-22-SHARED_SERVICE_HELPERS_IMPLEMENTATION_PLAN.md). Reject empty and
   whitespace-only, then require a match against either the email or the phone pattern. One
   implementation, called from both services. If that plan has not merged, put the function in
   `notification_service.py` at module scope and note the follow-up.
3. `NotificationService.create_one_off_notification` (`:345-392`): call the validator before
   anything is persisted.
4. `AsyncIONotificationService.create_one_off_notification` (`:997-1044`): same.

Spec use-case: no spec — parity with `vintasend-ts`'s `validateEmailOrPhone`.

Tests:

- **Unit**: `@vintasend/tests/test_services/test_service_utils.py` (or the service test module if the
  helpers plan has not merged) — table-driven over the validator: valid emails, valid phones with and
  without a leading `+`, boundary lengths 10 and 15, empty string, whitespace-only, `"not-an-email"`,
  a 9-digit and a 16-digit number.
- **Integration**: [test_notification_service.py](../vintasend/tests/test_services/test_notification_service.py)
  — `create_one_off_notification` with `email_or_phone=""` raises and **persists nothing** (assert
  the backend is unchanged); a valid email and a valid phone both still create successfully. Mirror
  all of it on the asyncio test case.

**Suggested AI model**: Tier 1. Single validation function plus table-driven tests, exact precedent
in the TS implementation.

Acceptance: `create_one_off_notification(email_or_phone="")` raises
`InvalidOneOffNotificationRecipientError` and leaves the backend empty, on both services, while
valid emails and phone numbers create as before.

## 5. Risk & Rollout Notes

- **Phase 1 is technically breaking.** An application unknowingly running duplicate adapters starts
  failing at startup. That is the point — it was double-sending — but it must be called out in
  `RELEASE_NOTES.md` under `### Backwards compatibility` with the remedy ("remove the duplicate
  adapter from `NOTIFICATION_ADAPTERS`"), because the failure appears at deploy time, not at upgrade
  time. This is the same call `vintasend-ts` made in v0.13.0.
- **Phase 2 is breaking only for callers already persisting garbage.** Any existing one-off
  notification with an empty recipient was never deliverable. No migration; existing rows are
  untouched, since validation is on the create path only.
- **No locks, no migrations, no partitions, no backfill.** No database in this repo, and neither
  phase changes a persisted format.
- **Rollback**: revert either phase independently. Neither depends on the other, and neither changes
  a seam, so downstream packages are unaffected in both directions.
- **Downstream**: no seam method added, renamed, or removed, so `vintasend-django`,
  `vintasend-sqlalchemy`, `vintasend-celery` and the renderer/adapter packages need no release. The
  one thing to verify before merging is that none of their test fixtures construct a service with
  duplicate adapter types.
- **Sequencing**: independent of the three architecture ports. Merge whenever. Phase 2 reads better
  after the [shared-helpers plan](2026-07-22-SHARED_SERVICE_HELPERS_IMPLEMENTATION_PLAN.md) has
  created `service_utils.py`, but does not require it.
- **Release**: minor bump with the mandatory `### Backwards compatibility` section. Use
  `Skill(release-package)` for the version choice and release-note shape.

## 6. Open Questions

| Question | Recommended default |
|---|---|
| Should the duplicate check compare `notification_type` only, or `(notification_type, some key)`? | **Type only.** TS validates on type alone, and Python's adapter has no `key` attribute to pair it with. If a future use case genuinely needs two EMAIL adapters (e.g. per-tenant routing), that is an adapter-selection feature, and this guard is exactly the thing that should force the conversation. |
| Should `email_or_phone` validation also run on `update_notification`? | **No.** TS validates in `updateOneOffNotification`, which Python does not have — `update_notification` covers both notification kinds and cannot tell whether `email_or_phone` is meaningful. Add the validation alongside a dedicated `update_one_off_notification` if that method is ever ported. |
| Is the phone regex too permissive (accepts any 10-15 digits, no country-code check)? | **Yes, deliberately.** It matches TS exactly. Real phone validation means an E.164 library and a new runtime dependency, which the `dependency_licenses` policy says to route past a human. Catching empty strings is 90% of the value at 0% of the cost. |

## 7. Touch List

**Phase 1**

- [exceptions.py](../vintasend/exceptions.py) — add `DuplicateNotificationAdapterError`.
- [notification_service.py](../vintasend/services/notification_service.py) — new module-level
  `validate_unique_adapter_notification_types`; called from `NotificationService.__init__`
  ([:99-140](../vintasend/services/notification_service.py#L99-L140)) and
  `AsyncIONotificationService.__init__` (`:767-804`).
- [test_notification_service.py](../vintasend/tests/test_services/test_notification_service.py) —
  constructor guard tests on both service test cases.

**Phase 2**

- [exceptions.py](../vintasend/exceptions.py) — add `InvalidOneOffNotificationRecipientError`.
- [service_utils.py](../vintasend/services/service_utils.py) — add `validate_email_or_phone`
  (created by the shared-helpers plan; falls back to `notification_service.py` module scope if that
  has not merged).
- [notification_service.py](../vintasend/services/notification_service.py) — `create_one_off_notification`
  on both services (`:345-392` and `:997-1044`).
- `@vintasend/tests/test_services/test_service_utils.py` — validator table tests.
- [test_notification_service.py](../vintasend/tests/test_services/test_notification_service.py) —
  create-path integration tests on both service test cases.

**Both phases**

- [RELEASE_NOTES.md](../RELEASE_NOTES.md) — minor entry with a mandatory `### Backwards compatibility`
  section naming the duplicate-adapter rejection and the new recipient validation.
- [README.md](../README.md) — one-off notification section: document that `email_or_phone` is
  validated at creation.
- [pyproject.toml](../pyproject.toml) — version bump.
