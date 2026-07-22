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

### Phase 2 — Filter types and the backend seam ✅

- **Status**: complete
- **Model**: claude-opus-4-8 (plan suggested Tier 4)
- **Branch**: `plan/notification-filtering-api/phase-2` (stacked on phase-1)
- **Base**: `plan/notification-filtering-api/phase-1`
- **Commits**:
  - `29c6c9c Add composable filter vocabulary and backend seam`
  - `bbb8805 Implement filter_notifications in fakes and services with tests`
- **Review**: reviewer claude-opus-4-8 (phase override Tier 4). **Clean** — no BLOCKER/SHOULD-FIX;
  one NIT deferred (see below). Verified by execution: every binding semantic (empty-filter-matches-all,
  scalar/list membership, all four string lookups in both case modes, inclusive range boundaries,
  None-under-negation, tiebreaker direction on descending sorts, capabilities merge direction) probed
  directly; the pagination-tiebreaker test proven to fail without the `id` tiebreaker; TypedDicts
  constructed on Python 3.10.13.

Summary:

- New `filters.py`: the full JSON-round-trippable `TypedDict` vocabulary (`DateRange` in functional
  syntax with wire key `from`, `StringFilterLookup`, `StringFieldFilter`, `NotificationOrderBy`,
  `NotificationFilterFields` including `read_at_range`, single-key `AndFilter`/`OrFilter`/`NotFilter`,
  the `NotificationFilter` union), `DEFAULT_BACKEND_FILTER_CAPABILITIES` (22 camelCase dotted keys all
  `True`), the `is_field_filter` / `is_string_filter_lookup` discriminators, and a **shared** recursive
  `matches_filter` + `sort_notifications` both fakes call so the two halves cannot drift.
- `filter_notifications` is `@abstractmethod` on both backend ABCs (the plan-sanctioned break);
  `get_filter_capabilities` (`{}`) and `count_notifications` are concrete. The base
  `count_notifications` deviates from the plan's literal `sum(1 for _ in ...)` — since the only
  primitive is the paginated `filter_notifications`, it pages through to a correct total (verified
  terminating at 0 / <100 / exactly 100 / multiples). Backends override for a SQL `COUNT`.
- Both services expose `filter_notifications`, `count_notifications`, and
  `get_backend_supported_filter_capabilities` (merges the backend report OVER the all-`True` default).
- **Binding semantic decisions** documented at module level: filter fields snake_case / capability
  keys camelCase; `case_sensitive` defaults to `True` and a bare `str` means case-sensitive `exact`;
  ranges inclusive on both ends; a positive filter on `None` never matches, so `None` rows are
  included under `not` (matching the Phase 3b Django `~Q(...) | Q(field__isnull=True)` intent);
  `updated_at`→`modified`, `created_at`→`created`; default order is `created` desc; `id` always
  appended in the primary sort direction.

**NIT deferred to Phase 3b awareness**: the fake compares `str(first.id)` vs `str(second.id)` for the
tiebreaker, so integer ids order lexicographically (`"10"` before `"2"`). Pagination stays correct (a
consistent total order drops/duplicates nothing) and it matches the fake's existing `str()`-based
identity convention, but a SQL backend appending `id` will order numerically. Invisible for the
string ids the tests use; worth knowing when Phase 3b writes the Django translator.

Gates: `ruff check` clean, `ruff format --check` clean, `mypy` clean (42 source files),
`pytest` **260 passed** (211 baseline + 49 new), `tox` **green on py310-py314** (260 each).

### Phase 3 — Counts, retry, and stable ordering ✅

- **Status**: complete
- **Model**: claude-sonnet-5 (plan suggested Tier 3)
- **Branch**: `plan/notification-filtering-api/phase-3` (stacked on phase-2)
- **Base**: `plan/notification-filtering-api/phase-2`
- **Commits**:
  - `c091391 Add resend_notification with typed refusal errors`
  - `dd12165 Test resend_notification and pin pagination tiebreaker contracts`
- **Review**: reviewer claude-opus-4-8 (project default Tier 4). **Clean** — no findings. Verified by
  execution: original-untouched-after-resend, all three context-reuse cases, both refusal paths
  creating no row, tenant + attachments carried onto the clone without tripping the guards, and
  count == exhaustive-sweep length.

Summary:

- `resend_notification(notification_id, use_stored_context_if_available=False)` on both services:
  re-reads the source, refuses one-offs and future-scheduled notifications with the new
  `NotificationResendError`, clones into a brand-new PENDING row via `persist_notification` (leaving
  the original untouched), and sends the clone immediately. `send_after` is deliberately `None` on
  the clone — resend means "send now", matching the plan's copy-field list which omits it.
- **Context reuse** is threaded by widening `send(notification, context=None, ...)` with an optional
  trailing `context` param rather than inventing a parallel path. Every existing caller passes no
  context and is byte-identical to before; when `use_stored_context_if_available=True` and the source
  has a stored `context_used`, the clone sends with exactly that object, bypassing
  `get_notification_context`.
- **Tenant** is carried onto the clone via `persist_notification`'s conditional-forward (the Phase-1
  pattern), never through `update_notification`, so the `TenantReassignmentError` guard is not tripped.
  **Attachments** (flat `StoredAttachment` rows — the attachment-manager plan has not merged, so this
  is the plan's sanctioned fallback) are copied via the backend's `persist_notification_update`, also
  bypassing the service-level tenant guard.
- `NotificationResendError(NotificationError)` added to `exceptions.py`.
- `count_notifications` efficiency override was already added to both fakes in Phase 2
  (`sum(1 for n ... if matches_filter(...))`); Phase 3 verified it and added filter→count→exhaustive-
  paginate union tests. The base ABC's paging default is untouched.
- Stable-ordering tiebreaker now asserted in both directions on both fakes (Phase 2 only swept the
  sync fake ascending).

**For Phase 4 README awareness** (non-defect, consistent with existing convention): the object
`resend_notification` returns reflects SENT status only because the fake mutates the same object in
place — identical to the long-standing `create_notification` return convention. A backend returning a
detached instance would report the clone as PENDING even though it was sent. The README should not
over-promise the returned object's freshness.

Gates: `ruff check` clean, `ruff format --check` clean, `mypy` clean (42 source files),
`pytest` **281 passed** (260 baseline + 21 new). tox not run — no version-sensitive typing
constructs introduced.

### Phase 4 — Documentation ✅

- **Status**: complete
- **Model**: claude-sonnet-5 (plan suggested Tier 2)
- **Branch**: `plan/notification-filtering-api/phase-4` (stacked on phase-3)
- **Base**: `plan/notification-filtering-api/phase-3`
- **Commits**:
  - `0721021 Document filtering API and bump version to 1.5.0`
  - `d4a4d81 Stamp created and modified in fake backends` (correctness fix found while verifying the
    README examples, plus its RELEASE_NOTES Bug Fixes note)
- **Review**: reviewer claude-opus-4-8 (project default Tier 4). One borderline SHOULD-FIX (add a
  RELEASE_NOTES Bug Fixes note for the fake stamping) — applied. Everything else verified clean by
  execution: every doc claim checked against the code, the fake-fix invariants probed, JSON
  round-trip (including legacy files missing the keys) confirmed.

Summary:

- README "Filtering and Ordering Notifications" section (after in-app notifications, before the
  Glossary): the filter grammar with worked nested examples including `not` wrapping `or`; ordering
  and the `created_at`→`created` / `updated_at`→`modified` mapping; pagination and why the result is
  an `Iterable`; the capability mechanism and the snake_case-fields / camelCase-keys asymmetry with
  its rationale; the empty-filter-matches-all rule; `resend_notification` with an honest freshness
  caveat; `tenant` is-not-authorization; and a query-governance note. **Every code example was
  executed against `FakeFileBackend` and runs** (independently re-verified by the conductor,
  including resend with byte-for-byte stored-context reuse).
- RELEASE_NOTES 1.5.0 entry with Features, New exceptions, a **Bug Fixes** note for the fake
  stamping, and a mandatory `### Backwards compatibility` section naming `filter_notifications` as
  newly abstract on both backend ABCs. Version bumped 1.4.0 → 1.5.0 — per the plan's Risk & Rollout
  Notes, a new abstract seam method is a minor-with-mandatory-note under AGENTS.md.
- **Correctness fix**: `FakeFileBackend` / `FakeAsyncIOFileBackend` never stamped `created` /
  `modified`, so `created_at_range` matched nothing and the default `created`-desc "newest first"
  ordering degenerated to id-order for service-created notifications. Both fakes now stamp them at
  creation (one shared timestamp), advance `modified` on updates and status transitions (reusing the
  Phase-1 single-`now` bulk timestamp), and round-trip both through JSON (a gap that was also
  silently dropping them). 10 regression tests added.

Gates: `ruff check` clean, `ruff format --check` clean, `mypy` clean (42 source files),
`pytest` **291 passed** (281 baseline + 10 new).

## Deferred phases

- **Phase 3b — `vintasend-django` implementation**: cross-repo. Lives in the separate
  `vintasend-django` package and, per the plan's **Risk & Rollout Notes**, cannot merge until core
  has released. Not executed by this run.
