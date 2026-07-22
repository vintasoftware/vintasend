# Implementation Tracking — Notification Filtering API

- **Plan**: [2026-07-22-NOTIFICATION_FILTERING_API_IMPLEMENTATION_PLAN.md](2026-07-22-NOTIFICATION_FILTERING_API_IMPLEMENTATION_PLAN.md)
- **Feature name**: notification-filtering-api
- **Started**: 2026-07-22
- **Last updated**: 2026-07-22
- **Feature flag**: none (the plan declares no flag; the compatibility boundary is the release plus its `### Backwards compatibility` note)

## Run options

| Option | Value |
|---|---|
| `commit_strategy_resolved` | stacked-branches |
| `pause_between_phases` | false |
| `generate_inline_comments` | false |
| `full_test_suite` | true |
| `use_worktree` | true (switched on mid-run — see note below) |
| `worktree_path` | `.claude/worktrees/plan-notification-filtering-api` |
| `worktree_branch` | `plan/notification-filtering-api/phase-1` (phase branches stack inside the worktree) |
| `sandbox_tier` | none |

**Worktree note.** The run started in the main checkout with `use_worktree = false`. Partway through
Phase 1 the implementer found a second, unrelated plan run (`attachment-manager-seam`) driving the
same working tree — it switched the checked-out branch mid-phase. The Phase 1 commit was rebuilt via
git plumbing directly on `e0c93b7` and verified clean, then this plan was moved into its own worktree
so the remaining phases cannot collide. The main checkout was left untouched.

## Completed phases

### Phase 1 — Delivery-lifecycle and tenant fields ✅

- **Status**: complete
- **Model**: claude-sonnet-5 (plan suggested Tier 3)
- **Branch**: `plan/notification-filtering-api/phase-1`
- **Base**: `main` (`e0c93b7`)
- **Commits**:
  - `da4c325 Add sent_at, read_at and tenant to notifications`
  - `ed78505 Forward tenant only when set and harden lifecycle tests` (review fixes)
- **Review**: reviewer claude-opus-4-8 (phase override Tier 4), fixer claude-sonnet-5 (Tier 3).
  One BLOCKER + four SHOULD-FIX + two NIT raised; all fixed or consciously deferred.

Summary:

- Added `sent_at`, `read_at` and `tenant` (all `datetime | None` / `str | None`, all defaulting to
  `None`) to both `Notification` and `OneOffNotification` in `dataclasses.py`. Appended last so
  existing positional construction is unaffected.
- Added `tenant: str | None = None` to `persist_notification` and `persist_one_off_notification` on
  both `BaseNotificationBackend` and `AsyncIOBaseNotificationBackend`. In the AsyncIO ABC `tenant`
  sits immediately before the pre-existing `lock` parameter so the sync and async signatures read
  identically up to that point; no in-repo caller passes `lock` positionally.
- Documented the timestamp contract on `mark_pending_as_sent` (sets `sent_at`), `mark_sent_as_read`
  (sets `read_at`) and `mark_sent_as_read_bulk` (sets `read_at` only on rows that actually
  transitioned SENT → READ; already-read rows keep their original `read_at`).
- `create_notification` and `create_one_off_notification` accept and forward `tenant` on both
  `NotificationService` and `AsyncIONotificationService`.
- `update_notification` on both services raises `TenantReassignmentError` when `tenant` appears in
  the **raw kwargs dict**, checked before the TypedDict is consulted, so a `**{"tenant": ...}` splat
  cannot bypass it. `tenant` is deliberately absent from `UpdateNotificationKwargs`.
- New `TenantReassignmentError(NotificationError)` in `exceptions.py`.
- Both fakes populate all three fields and round-trip them through JSON, reading missing keys with
  `.get()` so notification files written before this change still load.
- 14 new tests (7 sync + 7 AsyncIO).

Review findings and their resolution:

- **BLOCKER — `tenant` forward made the keyword de-facto required downstream.** All four create
  call sites forwarded `tenant=tenant` unconditionally, so any backend built against ≤1.4.0 would
  raise `TypeError` on *every* create, including for callers who never use tenants — breaking this
  phase's own "existing callers see no change" contract, and failing at call time in production
  rather than loudly at construction. Fixed by forwarding `tenant` only when it is not `None`.
  Two regression tests pin it, each driving a backend subclass whose `persist_notification` carries
  the frozen pre-1.5 signature. Verified by reverting the service file and confirming both tests
  fail with the exact downstream `TypeError`. A caller who *does* pass a tenant to an old backend
  still gets the loud `TypeError`, which is intended.
- **SHOULD-FIX — two tautological assertions.** The AsyncIO "already-read rows are not restamped"
  assertion compared an object against itself (the fake returns rows by identity), and passed
  against a deliberately sabotaged implementation. `test_create_notification_persists_tenant`
  likewise never exercised the JSON round-trip. Both now bind expected values first and reload
  through a second backend instance over the same file.
- **SHOULD-FIX — `create_one_off_notification` tenant was entirely untested** on both services.
  Covered now, including the round-trip.
- **SHOULD-FIX — bulk read marking stamped N distinct timestamps.** `datetime.now()` was called
  once per row inside the loop; every real SQL backend issues one `UPDATE ... SET read_at = <one
  timestamp>`. Since the fakes are the reference implementation downstream authors copy, the value
  is now hoisted above the loop. This matters for Phase 2's `read_at_range` filter: a range
  boundary landing inside the microseconds spanned by one bulk call would otherwise split a single
  batch across pages.
- **NIT — abstract-method body style** realigned between `base.py` and `asyncio_base.py`.

Consciously deferred (recorded so later phases pick them up):

- `NotificationDict` / `OneOffNotificationDict` in `notification_adapters/async_base.py` do not
  carry `tenant`, so a task-queue adapter cannot see it. No correctness impact today.
- The fakes' `persist_notification_update` does a blind `setattr`, so the backend layer itself does
  not refuse tenant reassignment. The plan deliberately specified a service-level guard only.
- `README.md` documents `create_notification`'s signature and `update_notification`'s exception
  list, both of which changed here. **Phase 4 must update them.**

Gates: `ruff check` clean, `ruff format --check` clean, `mypy` clean (40 source files),
`pytest` **211 passed** (191 baseline + 20 new).

## Current phase

Phase 2 — Filter types and the backend seam (not started).

## Remaining phases

- **Phase 2** — Filter types and the backend seam (Tier 4; reviewer Tier 4).
- **Phase 3** — Counts, retry, and stable ordering (Tier 3).
- **Phase 4** — Documentation (Tier 2).

## Deferred phases

- **Phase 3b — `vintasend-django` implementation**: cross-repo. Lives in the separate
  `vintasend-django` package and, per the plan's **Risk & Rollout Notes**, cannot merge until core
  has released. Not executed by this run.
