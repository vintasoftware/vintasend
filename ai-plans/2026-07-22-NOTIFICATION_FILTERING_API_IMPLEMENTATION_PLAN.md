# Notification Filtering API — Implementation Plan

Ports the `vintasend-ts` filter/search API: a composable filter object with logical operators, string
lookups and date ranges; single-field ordering; and backend capability introspection so a monitoring
dashboard can discover what a given backend supports. Adds the `sent_at`, `read_at` and `tenant`
columns the API needs, plus the counts and retry a dashboard requires.

## 1. Goals

1. Add `filter_notifications(filter, page, page_size, order_by=None)` to both backend seams, taking a
   composable filter supporting `and` / `or` / `not`, scalar-or-list membership, string lookups and
   date ranges.
2. Add capability introspection so a client can discover which filter fields, string lookups and
   sort fields a given backend actually supports, and grey out the rest.
3. Add the `sent_at`, `read_at` and `tenant` fields the filter API references, including a ban on
   reassigning `tenant` after creation.
4. Give a dashboard the operations it needs beyond listing: a total count for pagination, a retry
   action, and pagination that does not silently drop or duplicate rows.

Non-goals:

- **No dashboard.** This plan ships the library API a dashboard would consume. The dashboard itself
  is a separate client project.
- **No HTTP layer.** `vintasend` has no web surface; the host application exposes these methods
  however it likes. There is no REST or GraphQL design in this plan.
- **No multi-backend routing.** TS threads `backendIdentifier` through every read, and several of its
  dashboard affordances (`getBackendSyncStats`, `verifyNotificationSync`, the backend picker) depend
  on a primary/replica model Python does not have. Out of scope; the filter API is single-backend and
  takes no `backend_identifier` parameter.
- **No `bulk_persist_notifications`.** A TS method with no dashboard relevance.
- **No `render_email_template_from_content`.** TS's "preview an old notification with the template as
  it was" is genuinely useful for a dashboard, but it needs a new renderer-seam method and belongs
  with a renderer plan.
- **No `vintasend-sqlalchemy` work.** Its filter translator is its own plan, after its catch-up plan.

## 2. Guiding Decisions

| Decision | Resolution |
|---|---|
| **snake_case fields, camelCase capability keys** | Filter fields are an in-process Python API that `mypy` checks and developers type by hand, so `notification_type` and `send_after_range` are correct. Capability keys are different: they are data a client reads, and keeping them byte-identical to TS (`'fields.notificationType'`, `'orderBy.sentAt'`) means one dashboard can consume a capability report from either ecosystem without a translation table. The asymmetry is deliberate and must be documented where it would otherwise read as an oversight. |
| **`TypedDict(total=False)`, not dataclasses** | The filter object round-trips as JSON with no adapter layer, which is what a dashboard POSTing a filter needs. `DateRange` must use functional syntax — `TypedDict('DateRange', {'from': ..., 'to': ...}, total=False)` — because `from` is a Python keyword and the wire key has to stay `from` for cross-language parity. Runtime discriminators `is_field_filter()` and `is_string_filter_lookup()` replace TS's type guards. |
| **`filter_notifications` is abstract; capabilities and counts are concrete** | Per the Step 0 compatibility answer, the core new method lands as `@abstractmethod` — a backend that silently returned nothing would be worse than one that fails at construction. `get_filter_capabilities` and `count_notifications` get concrete defaults (`{}` and `sum(1 for _ in ...)`), following the exact precedent of `count_in_app_notifications` at [base.py:187-205](../vintasend/services/notification_backends/base.py#L187-L205). Backends override for efficiency. |
| **Missing capability keys mean supported** | TS merges a backend's report over an all-`true` default, so a backend only declares what it *cannot* do. This is what makes the mechanism forward-compatible: a new filter field added in a later release does not require every backend to re-declare support. Python mirrors it exactly. |
| **Add `sent_at`, `read_at` and `tenant` rather than reporting them unsupported** | The capability mechanism exists precisely so a backend can decline, and declining would be the smaller plan. But `sent_at` and `read_at` are delivery-lifecycle facts a monitoring dashboard is largely *about* — "what failed and when", "what has been read" — and shipping a filter API that cannot answer them undercuts the goal. `tenant` comes along because it is coupled to filtering and carries a security requirement worth landing deliberately. |
| **`tenant` cannot be reassigned after creation** | TS rejects `tenant` in both update paths with an explicit data-leak rationale: moving a notification between tenants moves it between compartments. Python enforces the same at `update_notification`, raising rather than ignoring. Defence in depth — the host application is the real security boundary, but the library should not be the thing that makes a cross-tenant move easy. |
| **Empty filter matches everything** | `filter_notifications({}, page, page_size)` is the unrestricted listing, so no separate `get_all_notifications` is needed. TS's own tests assert this. It must be stated in the docstring, because "empty filter returns nothing" is an equally plausible reading and getting it wrong in a backend implementation is a silent data-exposure bug. |
| **Stable sort tiebreaker is mandatory** | `created` and `modified` are not unique. Offset pagination over a non-unique sort key silently drops and duplicates rows across pages — a bug that shows up as "the dashboard is missing notifications" and is miserable to diagnose. Every backend appends `id` in the same direction as the primary sort key. TS does not mandate this; Python should. |
| **Return type stays `Iterable`** | Consistent with every other read method on the seam, and it lets Django and SQLAlchemy keep returning generators. The consequence is that a caller cannot `len()` the result, which is exactly why `count_notifications` exists as a separate method. |
| **No feature flag** | No flag module in this library. The compatibility boundary is the release plus its `### Backwards compatibility` note. |

## 3. Data Model Changes

### 3.1 Filter types

New `@vintasend/services/notification_backends/filters.py` — a separate module rather than the ABC
file, to keep the seam file focused and avoid an import cycle with `dataclasses.py`:

```python
DateRange = TypedDict("DateRange", {"from": datetime, "to": datetime}, total=False)

class StringFilterLookup(TypedDict, total=False):
    lookup: Literal["exact", "starts_with", "ends_with", "includes"]  # required
    value: str                                                        # required
    case_sensitive: bool

StringFieldFilter = str | StringFilterLookup

NotificationOrderByField = Literal["send_after", "sent_at", "read_at", "created_at", "updated_at"]
NotificationOrderDirection = Literal["asc", "desc"]

class NotificationOrderBy(TypedDict):
    field: NotificationOrderByField
    direction: NotificationOrderDirection

class NotificationFilterFields(TypedDict, total=False):
    status: NotificationStatus | list[NotificationStatus]
    notification_type: NotificationTypes | list[NotificationTypes]
    adapter_used: str | list[str]
    user_id: int | str | uuid.UUID
    body_template: StringFieldFilter
    subject_template: StringFieldFilter
    context_name: StringFieldFilter
    tenant: str | list[str]
    send_after_range: DateRange
    created_at_range: DateRange
    sent_at_range: DateRange

NotificationFilter = NotificationFilterFields | AndFilter | OrFilter | NotFilter
```

`AndFilter` / `OrFilter` / `NotFilter` are single-key TypedDicts (`{"and": [...]}` etc.). Plus
`DEFAULT_BACKEND_FILTER_CAPABILITIES` (all `True`, camelCase dotted keys) and the two discriminators.

Note the one intentional divergence inside `StringFilterLookup`: the *lookup values* are snake_case
(`starts_with`) to match the field-naming decision, while the *capability keys* stay camelCase
(`stringLookups.startsWith`). Document it.

### 3.2 New fields on the core dataclasses

`sent_at: datetime | None`, `read_at: datetime | None` and `tenant: str | None` on `Notification` and
`OneOffNotification` in [dataclasses.py](../vintasend/services/dataclasses.py) (`:158-199`), appended
with `= None` defaults so existing positional construction keeps working. `tenant` is added to
`persist_notification` / `persist_one_off_notification` as an optional keyword; it is **not** added to
`UpdateNotificationKwargs`.

### 3.3 Django schema

`sent_at` and `read_at` (nullable, indexed — both are range-filtered and sortable) and `tenant`
(nullable, indexed — filtered by equality and membership) on the `Notification` model, in one
additive migration. `mark_pending_as_sent` and `mark_sent_as_read` populate the timestamps.

## 4. Phased Rollout

Bundled per Step 0. The columns land before the filter that references them, so no phase ships a
filter field with nowhere to read from.

### Phase 1 — Delivery-lifecycle and tenant fields

**Goal**: notifications record when they were sent and read, and which tenant they belong to, with
tenant reassignment rejected. Independently useful even if no filtering ever ships.

**Feature flag**: none — additive fields with `None` defaults; existing callers see no change.

Changes:

1. [dataclasses.py](../vintasend/services/dataclasses.py) `:158-199`: add the three fields to both
   dataclasses.
2. [notification_backends/base.py](../vintasend/services/notification_backends/base.py) `:58-90` and
   the asyncio twin `:55-89`: add `tenant: str | None = None` to both `persist_*` signatures. An
   optional keyword is a safe widening under `AGENTS.md`; renaming or reordering would not be.
3. `mark_pending_as_sent` (`:104-107`) and `mark_sent_as_read` (`:114-117`) contracts: set `sent_at`
   and `read_at`. `mark_sent_as_read_bulk` (`:119-142`) sets `read_at` on every row it touches.
4. [notification_service.py](../vintasend/services/notification_service.py): `create_notification`
   (`:289-343`) and `create_one_off_notification` (`:345-392`) accept and forward `tenant`, plus
   asyncio twins. `update_notification` (`:394-420`) raises if `tenant` appears in kwargs — check the
   raw dict, not just the TypedDict, since `UpdateNotificationKwargs` is not enforced at runtime.
5. [exceptions.py](../vintasend/exceptions.py): `TenantReassignmentError`.
6. [fake_backend.py](../vintasend/services/notification_backends/stubs/fake_backend.py): populate all
   three fields in both the sync and asyncio classes.

Spec use-case: no spec — ports TS's `tenant` field and its reassignment ban, plus the timestamps the
filter API needs.

Tests:

- **Unit**: `sent_at` is `None` until sent then set; `read_at` likewise; `mark_sent_as_read_bulk` sets
  it on all affected rows and none of the skipped ones; `tenant` persists and round-trips.
- **Integration**: `update_notification(id, tenant="other")` raises `TenantReassignmentError` and
  changes nothing; passing `tenant` via `**kwargs` bypassing the TypedDict also raises. Both service
  classes.
- **Backwards compatibility**: a notification created without `tenant` has `tenant is None` and every
  existing test passes untouched.

**Suggested AI model**: Tier 3. Touches both dataclasses, both backend ABCs, both services and both
fakes, and the reassignment guard has a security rationale worth getting exactly right.

Acceptance: a notification records `sent_at` on send and `read_at` on read, `tenant` persists, and
`update_notification` with a `tenant` key raises `TenantReassignmentError` on both services.

### Phase 2 — Filter types and the backend seam

**Goal**: the filter vocabulary exists and `FakeFileBackend` implements it fully, so the semantics
have a working reference before any ORM translates them.

**Feature flag**: none — new abstract method, gated by the release.

Changes:

1. New `@vintasend/services/notification_backends/filters.py` per **Data Model Changes**, re-exported
   from `base.py` for import convenience.
2. [notification_backends/base.py](../vintasend/services/notification_backends/base.py): abstract
   `filter_notifications(filter, page, page_size, order_by=None)` after `:160`, alongside the other
   `filter_*` methods. Concrete `get_filter_capabilities() -> dict[str, bool]` returning `{}` and
   concrete `count_notifications(filter) -> int` defaulting to `sum(1 for _ in ...)`. Docstring must
   state that an empty filter matches everything and that ordering is stable.
3. [notification_backends/asyncio_base.py](../vintasend/services/notification_backends/asyncio_base.py):
   async mirrors after `:165`. Reads take no `lock` parameter, matching the existing read methods.
4. [fake_backend.py](../vintasend/services/notification_backends/stubs/fake_backend.py): a recursive
   in-memory predicate evaluator, in both classes, reusing the existing `__paginate_notifications`
   (`:512-519`, `:1014-1020`). This is the reference implementation downstream authors will read —
   it must handle every operator, lookup and range, and report all capabilities `True`.
5. [notification_service.py](../vintasend/services/notification_service.py): `filter_notifications`,
   `count_notifications` and `get_backend_supported_filter_capabilities` on both services. The last
   merges the backend's report over `DEFAULT_BACKEND_FILTER_CAPABILITIES`.

Spec use-case: no spec — ports TS's `NotificationFilter` type family and `filterNotifications`.

Tests:

- **Unit**: `@vintasend/tests/test_services/test_notification_filters.py` — new. Mirror the TS suite:
  each field alone; scalar vs list membership; every string lookup in both case modes; ranges with
  one bound and with both; multiple keys as implicit AND; nested `and` / `or` / `not`, including
  `not` wrapping `or`; empty filter matches all; each `order_by` field in both directions.
- **Integration**: pagination correctness — a full sweep across pages over records sharing a
  `created` value returns every row exactly once. This is the test that catches a missing tiebreaker.
- **Capabilities**: a backend declaring `{'fields.tenant': False}` reports every other key `True`
  after the merge.

**Suggested AI model**: Tier 4. The recursive evaluator plus the TypedDict union under `mypy` on
Python 3.10 is genuinely fiddly, and the fake is the semantics other implementations copy.

**Review models**: reviewer Tier 4 — this phase defines a public query contract two ORMs will
translate. A wrong NULL or negation semantic here propagates into every backend and surfaces as
missing rows in a dashboard.

Acceptance: `FakeFileBackend.filter_notifications` satisfies every case in the ported TS test suite,
`get_backend_supported_filter_capabilities()` returns all-`True` for it, and a page sweep over
equal-`created` records yields each row exactly once.

### Phase 3 — Counts, retry, and stable ordering

**Goal**: the operations a monitoring dashboard needs on top of listing — a total for pagination, a
retry action, and ordering that does not lose rows.

**Feature flag**: none.

Changes:

1. `resend_notification(notification_id, use_stored_context_if_available=False)` on both services,
   porting TS: re-read the notification, refuse one-offs, refuse future-scheduled ones, clone into a
   new row, and optionally reuse `context_used` verbatim instead of regenerating. Refusals raise
   typed errors, not silent no-ops.
2. [exceptions.py](../vintasend/exceptions.py): `NotificationResendError`.
3. Stable-ordering contract: document on `filter_notifications` that implementations must append `id`
   in the primary direction, and assert it in the fake.
4. `count_notifications` overrides where a naive count would be wasteful.

Spec use-case: no spec — ports TS's `resendNotification` and adds the Python-side count.

Tests:

- **Unit**: resending a sent notification creates a new row and leaves the original untouched;
  `use_stored_context_if_available=True` reuses `context_used` byte-for-byte; resending a one-off
  raises; resending a future-scheduled notification raises; `count_notifications` agrees with the
  length of an exhaustive `filter_notifications` sweep.
- **Integration**: filter → count → paginate through every page, asserting the union equals the
  filtered set exactly, with no duplicates.

**Suggested AI model**: Tier 3. Multi-step logic with several refusal branches, on established
patterns.

Acceptance: `resend_notification` clones a sent notification into a new pending row, refuses one-offs
and future-scheduled notifications with typed errors, and `count_notifications(f)` equals the length
of an exhaustive sweep of `filter_notifications(f, ...)`.

### Phase 3b — `vintasend-django` implementation (parallel track, separate repo)

**Goal**: the Django backend implements the filter seam natively in SQL. Runs alongside Phases 2-3;
**cannot merge until core has released**.

**Feature flag**: none.

Changes:

1. `vintasend_django/models.py:13-46`: add `sent_at`, `read_at`, `tenant`, all nullable and indexed.
   One additive migration (`0005_*`).
2. New `_filter_to_q(filter) -> Q` near `_paginate_queryset` (`:69-72`): recursive translation —
   `and` → `reduce(operator.and_, ...)`, `or` → `reduce(operator.or_, ...)`, `not` → `~`, `{}` →
   `Q()`. String lookups map to `__exact` / `__iexact` / `__startswith` / `__istartswith` /
   `__endswith` / `__iendswith` / `__contains` / `__icontains`. Ranges map to `__gte` / `__lte`
   (inclusive, matching TS).
3. **NULL semantics on negation**: `~Q(field__in=[...])` excludes NULL rows in SQL, which is almost
   never what "does not match" means to a dashboard user. Emit `~Q(...) | Q(field__isnull=True)` for
   nullable columns — `adapter_used`, `tenant`, `sent_at`, `read_at`. Decide once, document, and test
   it; TS leaves this undefined.
4. `_order_by_to_field(order_by)`: map to Django names, noting `updated_at` → `modified` and
   `created_at` → `created`. Always append `-id` / `id`. `Meta.ordering = ("-created",)` at
   `models.py:43-44` means an unordered queryset is already sorted; an explicit `.order_by()`
   overrides it, so document the default rather than relying on it.
5. `filter_notifications`, `get_filter_capabilities` (returning `{}` — everything supported once the
   columns exist) and a `.count()`-backed `count_notifications` after `:437`.
6. Populate `sent_at` / `read_at` in the mark methods.

Spec use-case: no spec — downstream adoption.

Tests: the full core filter suite re-run against the Django backend, plus SQL-specific cases — NULL
handling under negation, `iexact` on a case-sensitive collation, and pagination stability over
duplicate `created` values.

**Suggested AI model**: Tier 4. Recursive `Q` translation with NULL semantics and a migration.

**Review models**: reviewer Tier 4 — a translation bug returns the wrong rows rather than failing,
and NULL-handling errors are exactly the kind that pass a happy-path suite.

Acceptance: the core filter test suite passes unmodified against `DjangoDbNotificationBackend`,
`get_filter_capabilities()` returns `{}`, and negation over a nullable column includes NULL rows.

### Phase 4 — Documentation

**Goal**: a dashboard author can build against this without reading the source.

**Feature flag**: none.

Changes:

1. [README.md](../README.md): a "Filtering and Ordering Notifications" section — the filter grammar,
   worked nested examples, the capability mechanism and how to consume it, the snake_case-fields /
   camelCase-capability-keys asymmetry and why, and the empty-filter-matches-all rule.
2. [RELEASE_NOTES.md](../RELEASE_NOTES.md): minor entry with `### Backwards compatibility` naming
   `filter_notifications` as newly abstract on both backend classes, and the `tenant` /
   `sent_at` / `read_at` additions.

Spec use-case: no spec — release documentation.

Tests: none beyond a green suite. Every README example must run.

**Suggested AI model**: Tier 2.

**Reusable skills**: `Skill(release-package)`; `Skill(deslop-comments)`.

Acceptance: the README documents every filter field, lookup and capability key, and every example in
it executes against `FakeFileBackend`.

## 5. Risk & Rollout Notes

- **`filter_notifications` as an abstract method breaks every downstream backend at instantiation.**
  Step 0's compatibility choice, allowed by `AGENTS.md` as a minor with a mandatory note. Core
  releases first, then `vintasend-django`. `vintasend-sqlalchemy` cannot adopt until its catch-up
  plan lands — it is already missing four methods from 1.2.0.
- **Migrations are additive**: three nullable indexed columns. No table rewrite; index creation on a
  notification table is the main cost and `CONCURRENTLY` is worth considering for large deployments.
  Existing rows get `NULL` for all three, which is correct — `sent_at` is genuinely unknown for
  notifications sent before the column existed.
- **Backfill**: none, deliberately. `sent_at` could be approximated from `modified` for rows in `SENT`
  status, but `modified` changes on any update, so the approximation would be wrong in a way that is
  invisible. A `NULL` that means "unknown" beats a plausible-looking wrong timestamp in an audit view.
- **Query-plan risk**: a composable filter API lets a client build an arbitrarily nested predicate.
  Deeply nested `or` groups over unindexed columns will produce slow scans. This library cannot
  prevent that; the host application owns query governance. Note it in the README rather than
  inventing a depth limit that would be arbitrary.
- **The pagination tiebreaker is a correctness fix, not a nicety.** Without it, a dashboard paging
  through notifications sharing a `created` timestamp silently sees duplicates and misses rows.
- **`tenant` is not a security boundary here.** Consistent with the existing `user_id` treatment, it
  is an opaque host-application concept. The reassignment ban is defence in depth. The README must
  say plainly that filtering by tenant is not authorization.
- **Rollback**: Phase 1 is revertible before any backend persists a tenant. Phase 2 is not revertible
  once downstream ships against the abstract method. Phase 3b's migration is reversible (drop three
  nullable columns) with data loss confined to values written after it landed.
- **Coordinate the major.** Minor on its own, but the
  [background-send plan](2026-07-22-BACKGROUND_SEND_QUEUE_SERVICE_IMPLEMENTATION_PLAN.md) forces a
  2.0. Landing all three ports in one 2.0 spares downstream three separate breaking waves.

## 6. Open Questions

| Question | Recommended default |
|---|---|
| Should there be a `read_at_range` filter? | **Yes, add it.** TS omits it — `readAt` is orderable but not filterable, which reads like an oversight rather than a decision. "Read in the last 24h" is an obvious dashboard query and the column exists once Phase 1 lands. Adds one field and one capability key. |
| Are date-range bounds inclusive? | **Inclusive on both ends**, matching TS's `from`/`to` with no exclusive variant, mapped to `__gte` / `__lte`. Must be in the docstring — a client computing "today" from midnight to midnight will double-count boundary rows otherwise. |
| Should `filter_notifications` cover one-off notifications too? | **Yes, both.** The seam's other reads return `Notification | OneOffNotification` and a dashboard wants one list. If callers need to separate them, that argues for a `notification_kind` filter field — worth adding if anyone asks, not pre-emptively. |
| Should `count_notifications` be part of the capability report? | **No.** It has a working concrete default on every backend, so there is nothing to declare. Capabilities describe what a backend cannot do; a slow count is still a correct count. |
| Max page size? | **No enforced cap in the library.** `page_size` is the caller's decision and the host owns its own limits. Note the memory implication in the README rather than picking an arbitrary ceiling. |
| Should `resend_notification` copy attachments? | **Yes, by reference.** Once the [attachment plan](2026-07-22-ATTACHMENT_MANAGER_SEAM_IMPLEMENTATION_PLAN.md) lands, the clone's join rows point at the same `AttachmentFileRecord`s — no re-upload, no duplicate blobs. If that plan has not merged, resend copies the flat attachment rows and this becomes a follow-up. |

## 7. Touch List

**Phase 1**

- [dataclasses.py](../vintasend/services/dataclasses.py) — `:158-199`, `:202-212`.
- [notification_backends/base.py](../vintasend/services/notification_backends/base.py) — `:58-90`, `:104-142`.
- [notification_backends/asyncio_base.py](../vintasend/services/notification_backends/asyncio_base.py) — `:55-89` and mark methods.
- [notification_service.py](../vintasend/services/notification_service.py) — `:289-392`, `:394-420`, `:941-1072`.
- [exceptions.py](../vintasend/exceptions.py) — `TenantReassignmentError`.
- [fake_backend.py](../vintasend/services/notification_backends/stubs/fake_backend.py) — both classes.

**Phase 2**

- `@vintasend/services/notification_backends/filters.py` — new.
- `@vintasend/tests/test_services/test_notification_filters.py` — new.
- [notification_backends/base.py](../vintasend/services/notification_backends/base.py) — after `:160`.
- [notification_backends/asyncio_base.py](../vintasend/services/notification_backends/asyncio_base.py) — after `:165`.
- [fake_backend.py](../vintasend/services/notification_backends/stubs/fake_backend.py) — evaluator in both classes, reusing `:512-519` and `:1014-1020`.
- [notification_service.py](../vintasend/services/notification_service.py) — after `:578` and after `:1231`.

**Phase 3**

- [notification_service.py](../vintasend/services/notification_service.py) — `resend_notification` on both services.
- [exceptions.py](../vintasend/exceptions.py) — `NotificationResendError`.
- [test_notification_filters.py](../vintasend/tests/test_services/test_notification_filters.py) — count and pagination-stability tests.

**Phase 3b (cross-repo — `vintasend-django`, own PR, after core releases)**

- `vintasend_django/models.py:13-46`; new `migrations/0005_*.py`.
- `vintasend_django/services/notification_backends/django_db_notification_backend.py` — `_filter_to_q`, `_order_by_to_field` near `:69-72`; new methods after `:437`; mark methods.
- `vintasend_django/services/tests/test_django_db_notification_backend.py` — full filter suite.
- `pyproject.toml` — widen the `vintasend` pin.

**Phase 4**

- [README.md](../README.md), [RELEASE_NOTES.md](../RELEASE_NOTES.md), [pyproject.toml](../pyproject.toml).
