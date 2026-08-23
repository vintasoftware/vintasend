# Release Notes

## Version 3.1.1 (2026-08-23)

The managed-templates packages gain a capability report and ordering. The `vintasend` package
itself has no code change since 3.1.0 -- the number moves because the whole family releases in
lockstep. Everything below concerns `vintasend-managed-templates`, its Django storage backend, and
the templates management API.

The TypeScript siblings moved first on this one. The vocabulary, the capability keys and the REST
contract are all spelled to match `vintasend-ts-managed-templates`, so a client or dashboard
consuming both ecosystems reads one set of names.

### Features

#### A capability vocabulary for managed templates

* `vintasend_managed_templates.filters` gained `DEFAULT_TEMPLATE_BACKEND_FILTER_CAPABILITIES`, the
  managed-template counterpart of `DEFAULT_BACKEND_FILTER_CAPABILITIES` on the notification side.
  28 keys: `logical.*`, one `fields.*` per field of `ManagedTemplateFilterFields`,
  `stringLookups.*`, and the six new `orderBy.*`.
* Keys are camelCase dotted (`fields.templateManagedBackend`, `orderBy.createdAt`) even though the
  Python field names are snake_case, because a report goes on the wire. `field_capability_key` and
  `order_by_capability_key` translate; both raise `KeyError` for a name outside the vocabulary
  rather than returning a key no backend has declared.
* `BaseTemplateManagerBackend.get_filter_capabilities` is new and **concrete**, returning `{}`.
  `ManagedTemplateService.get_backend_supported_filter_capabilities` merges a backend's report over
  the default and caches it for the life of the service.
* There is deliberately no `pagination.oneIndexed` key. Unlike the notification seam, every
  template read passes through `ManagedTemplateService`'s own `page >= 1` validation, so 1-indexing
  is this seam's convention throughout and there is nothing to negotiate.

#### Ordering on the template-manager seam

* `get_paginated_templates` and `get_paginated_filtered_templates` -- on the backend ABC and on the
  service -- gained an optional `order_by: ManagedTemplateOrderBy | None`.
* `ManagedTemplateFilterOrderByField` widens from `created_at` / `updated_at` to `key`, `name`,
  `version`, `status`, `created_at` and `updated_at`. Each is a scalar a backend already stores per
  row, so a store can answer it from an index. Tags are excluded (a many-to-many has no single
  value to compare) and so is `most_recent_active_version` (a filter, not a field).
* A backend accepting `order_by` MUST apply it to the whole result set **before** paging. Sorting a
  page after it has been chosen orders rows *within* the page while the rows selected *for* it came
  back in the store's own order -- right on page 1, wrong on every page after it.
* `sort_templates(templates, order_by)` is the shared comparator, for a backend that reads a
  complete set anyway. Total and stable: ties break on `(key, version)`, and neither the tiebreak
  nor the placement of absent values flips with the direction -- either would let a page boundary
  move between two requests and drop or repeat a row. Strings compare by code point rather than by
  locale, so two machines serving two pages of one listing cannot disagree.

#### The service acts on the capability report

* `prune_unsupported_filters` is new, and `ManagedTemplateService` applies it before every filtered
  read. Previously nothing in the package read a capability report at all -- including the default
  listing, which asks for `most_recent_active_version` on every unqualified read.
* **Dropping only ever widens.** A pruned filter matches everything the original matched and
  possibly more, so a caller sees extra rows rather than missing ones. The corners follow from that
  rule: an `or` is dropped whole (removing one branch of a disjunction narrows it), a `not` whose
  inside pruned away is dropped (negating "everything" is "nothing"), and a bare string counts as
  `exact` *and* case-sensitive, so a backend lacking either cannot answer it.

#### Filters are dropped; orders are refused

* The one asymmetry in the design. An unsupported filter is pruned and the call succeeds; an
  unsupported order raises the new `ManagedTemplateUnsupportedOrderingError`.
* An ignored filter returns more rows than were asked for, which the caller can see. An ignored
  order returns exactly the rows requested in an arbitrary sequence, and nothing downstream can
  tell -- a UI renders that page under a highlighted "sorted by name" header and shows a sort that
  never happened.
* `get_supported_order_by_fields()` is how a caller asks before it sends.

#### `GET /templates` gains ordering, negotiated through `/capabilities`

* Two query parameters on `vintasend-templates-management-api`: `orderByField` (`key`, `name`,
  `version`, `status`, `createdAt`, `updatedAt`) and `orderByDirection` (`asc`, `desc`, defaulting
  to `asc` when a field is given). `GET /api/v1/capabilities` now carries the matching `orderBy.*`
  keys.
* **Neither parameter has a default.** Every `orderBy.*` key defaults to false, so defaulting to a
  field would make the ordinary listing a 400 against most backends. Omitted asks for the backend's
  own order. This differs from `vintasend-api`, which does default its order -- there, every
  notification backend can sort.
* An order the backend cannot apply is a `400` naming the capability key in `details`, not a silent
  drop. `orderByDirection` without `orderByField` is also a 400: ignoring it looks exactly like a
  backend that cannot sort, which hides the client bug.
* `openapi.yaml` is regenerated. `test_openapi.py` pins the enum against three places at once --
  the spec, the literal the server validates with, and the library's field list -- so a field added
  to one and forgotten in the others fails a test rather than a client.
* `capabilities.py` no longer keeps its own copy of the default map. The library owns the
  vocabulary and the service does the merging, so there is one fewer place for the two to drift.

#### The Django backend declares every field it can order by

* `vintasend-django-templates-manager` implements `get_filter_capabilities`, declaring all six
  `orderBy.*` keys. All six are real indexed columns on `ManagedTemplate`, so each is answered by
  the database and the order is composed into the SQL rather than applied to a page.
* Its order-field map widens from two entries to six, and `get_paginated_templates` accepts an
  order too rather than being hardcoded to newest-first. An unordered read still falls back to
  `-created, -id`, because a key has a row per version and an unordered offset page is free to
  return one row twice and skip another.
* Every entry is pinned by a test that *runs* the sort rather than reading the column definition.
  `version` is the one worth the effort: it is a `PositiveIntegerField`, so v10 sorts after v2 --
  which a store keeping versions as strings gets wrong silently.

### Backwards compatibility

**No seam method was added, removed or renamed, and no existing signature or semantic changed in a
way an implementation has to react to.** `BaseTemplateManagerBackend.__abstractmethods__` is
unchanged, so no custom template-manager backend breaks at instantiation. The notification seams --
`BaseNotificationBackend`, `AsyncIOBaseNotificationBackend`, the adapter ABCs and the template
renderer ABCs -- are untouched entirely.

* **`get_filter_capabilities` is concrete, not abstract.** It returns `{}`, so a backend that says
  nothing keeps working and reads as fully capable of every *filter*. Backends SHOULD override it
  to declare what they cannot do, and MUST override it to offer ordering at all -- see the next
  point.
* **The `orderBy.*` keys default to `False`.** This is the one exception to "a missing key means
  supported", and it is deliberate: ordering is newer vocabulary than the filters, so a `True`
  default would have every backend written before this release claim an order it silently ignores.
  A backend that can sort has to say so, and should verify each claim by *running* the sort rather
  than reading its store's documentation.
* **The optional `order_by` argument is never passed unasked.** `get_paginated_templates` and
  `get_paginated_filtered_templates` gained it on the ABC, but the service passes it as a keyword
  only when the backend's own report says the field is orderable -- which a silent backend's never
  does. A backend whose methods still take three arguments is therefore never handed a fourth.
  Adding the parameter is a signature-only change; honouring it means ordering the whole result set
  before paging.
* **Filtered reads now reach a backend pruned.** A backend that declares a limitation stops being
  handed the part of the filter it said it could not answer, which is the point of the report. Two
  consequences worth checking against your own implementation: a backend that declared a limitation
  and yet answered the filter anyway will see those listings widen, and `get_all_templates()`
  against a backend declining `fields.mostRecentActiveVersion` now returns every version rather
  than the filter being passed through and quietly ignored.
* **`ManagedTemplateFilterOrderByField` widened from two values to six.** Nothing in the library
  consumed it before this release -- it was declared and never wired to the seam -- so this is
  additive in practice. Code matching on it exhaustively gains four cases.
* **New exception**: `ManagedTemplateUnsupportedOrderingError`, subclassing `ManagedTemplateError`.
  It is raised only for an order, never for a filter. Existing `except ManagedTemplateError`
  handlers already catch it.
* **REST clients**: both new query parameters are optional and have no default, so an existing
  request is byte-for-byte unaffected. A client asserting on the exact contents of
  `GET /capabilities` will see six additional keys.
* **Release order.** `vintasend-managed-templates` must reach PyPI before
  `vintasend-django-templates-manager` and `vintasend-templates-management-api`, which pin it
  exactly and cannot resolve a version that does not exist yet. `scripts/lock_subpackages.py` and
  `scripts/tag_subpackages.py` already encode that wave.

## Version 3.1.0 (2026-08-21)

A supported-versions and packaging release. The `vintasend` package itself has no code change
since 3.0.0 -- the number moves because the whole family releases in lockstep. Everything below
concerns which Python and Django versions the packages support, and a Django test matrix that
was not testing what its environment names claimed.

### Features

#### Python 3.10 and 3.11 restored on the Django packages

* `vintasend-django` and `vintasend-django-templates-manager` lower their floor from `>=3.12`
  back to `>=3.10`, matching every other package in the family. The 3.12 floor was collateral
  from an earlier Django bump: Django 6.0 requires Python 3.12, and the constraint was raised
  package-wide instead of being scoped to that one factor. Neither package's source needs 3.12,
  so projects on 3.10 or 3.11 can use them again.

#### Django 6.1 support

* Both Django packages widen their Django constraint to `<6.2` and test against 6.1.
* Both packages' test settings move to the `MAILERS` setting introduced in Django 6.1. The
  deprecated `EMAIL_*` names warn there and are removed in Django 7.0. This is test
  configuration only: `vintasend-django`'s email adapter builds a
  `django.core.mail.EmailMessage`, which is unaffected by the deprecation.

#### Supported Python x Django combinations

* Python 3.10, 3.11 -- Django 4.2, 5.2
* Python 3.12 -- Django 4.2, 5.2, 6.0, 6.1
* Python 3.13, 3.14 -- Django 5.2, 6.0, 6.1

### Bug Fixes

* **The Django version matrix never varied Django.** Both Django packages pinned a Django
  version per tox factor in `deps`, then ran `poetry install` in `commands_pre`, which
  reinstalled Django from `poetry.lock`. Every environment tested the locked version (6.0.8)
  no matter what its name said, so compatibility with Django 4.2 and 5.2 was untested rather
  than under-tested. The factor pin is now installed after `poetry install`, and CI logs show
  four distinct Django versions across the matrix.
* CI ran matrix jobs on Python 3.10 and 3.11 against packages declaring `>=3.12`. Poetry
  refused those interpreters and fell back to the runner's 3.12, so two jobs were duplicates
  running under a misleading name. Matrices now match the declared floor.
* `skip_missing_interpreters` is now set explicitly in every `tox.ini`. Its default changed
  between tox 4.55 (skip) and 4.58 (fail), which is what turned "this matrix job did not
  install that interpreter" into a hard CI failure once a lockfile picked up the newer tox.
* `vintasend-django-templates-manager` uploaded its coverage to Codecov under the
  `vintasend-django` slug.

### Build Improvements

* `tox-gh` is now a dev dependency of all 12 packages that carry a `tox.ini`. Each already had
  a `[gh]` section mapping interpreters to environments, but without the plugin it was inert:
  every CI job ran every environment it could find an interpreter for. The Django packages were
  executing 30 environment runs for 14 declared environments; they now run exactly the 14.

### Dependencies

* `vintasend-django-templates-manager`: `django-stubs` relaxed from `^6.1.0` to `^6.0.5`.
  django-stubs 6.1.0 requires Python `>=3.11` and cannot install on the restored 3.10 floor.
* Both Django packages, dev only: `model-bakery` capped below 1.24, which requires
  `django>=5.2` and therefore cannot run the Django 4.2 environments.

### Backwards compatibility

* **No ABC seam changed.** No method was added, removed or renamed on `BaseNotificationBackend`,
  `AsyncIOBaseNotificationBackend`, the notification adapter ABCs or the template renderer ABCs,
  and no existing signature or semantic changed. Custom backends, adapters and renderers need
  no changes for this release.
* The `vintasend` package contains no code change at all; only its dev dependencies moved.
* **`vintasend-django` and `vintasend-django-templates-manager` drop Django 5.0 and 5.1**, both
  end-of-life upstream (August 2025 and December 2025). This is the only narrowing in the
  release: projects on either must move to Django 5.2, or stay on 3.0.0. The Django 4.2 LTS is
  still supported.
* The Python floor moves down rather than up, so no project loses support for its interpreter.

## Version 3.0.0 (2026-08-21)

### Features

#### Template version pinning and recording
- `Notification` and `OneOffNotification` gained `requested_template_version` (which version to
  render) and `used_template_version` (which version the renderer reported it used). Both default
  to `None`, which is what every notification carries with a renderer whose templates are not
  versioned.
- `create_notification`, `create_one_off_notification` and `update_notification` gained a
  `requested_template_version` (which version to render) and a `pin_template_versions` argument
  (whether to pin to the current version when no version is named). `NotificationService` /
  `AsyncIONotificationService` take `pin_template_versions` too, as the default for every call;
  the per-call argument overrides it in both directions, and `None` means "defer to the service".
- An explicitly passed version always wins over both. Updates re-pin only when they carry a new
  `body_template` -- an update to the title leaves an existing pin where it is.
- `pin_template_versions` is never stored: it decides what `requested_template_version` is set to
  at that moment, and nothing afterwards consults it.
- `update_notification` raises the new `UsedTemplateVersionReassignmentError` if a caller passes
  `used_template_version`, matching the existing `git_commit_sha` guard.
- Pinning exists so that editing a template cannot change what an already-recorded notification
  renders -- which only becomes a question once templates are versioned, i.e. with a store-backed
  renderer such as `vintasend-managed-templates`.

#### Filtering by template version
- `NotificationFilterFields` gained `requested_template_version` and `used_template_version`,
  each a scalar or a list, and `DEFAULT_BACKEND_FILTER_CAPABILITIES` gained the matching
  `fields.requestedTemplateVersion` / `fields.usedTemplateVersion` keys (both `True`).
- Candidates must be real `int`s. The new public guard `is_template_version_value` is what
  decides that, and it is stricter than `is_membership_value` on purpose: these are integer
  columns, so `"3"` is a malformed filter rather than a request for version 3, and a SQL
  backend forwarding it would raise on the cast instead of returning no rows. One bad
  candidate rejects the whole leaf, matching how a bad status candidate already behaved.
- The usual NULL semantics apply unchanged: a notification with no version never matches a
  positive filter on either field, and is included under negation.
- `vintasend-django` and `vintasend-sqlalchemy` translate both fields to SQL, sharing the
  guard so they accept and reject exactly what the reference evaluator does.

#### Logical composition and range negation reported through the capability report
- `DEFAULT_BACKEND_FILTER_CAPABILITIES` gained two namespaces, all defaulting to `True`:
  `logical.and` / `logical.or` / `logical.not` / `logical.notNested`, and
  `negation.sendAfterRange` / `negation.createdAtRange` / `negation.sentAtRange` /
  `negation.readAtRange`.
- The filter vocabulary has supported `and` / `or` / `not` groups since 2.0, but a backend had
  no way to say it could not assemble them — so a dashboard could only find out by issuing a
  filter the backend then failed or silently mis-evaluated.
- `logical.not` and `logical.notNested` are deliberately separate: a query builder able to negate
  a single predicate cannot always negate a whole subtree, so a backend may support
  `{"not": {"tenant": "acme"}}` and decline `{"not": {"or": [...]}}`.
- `negation.*` is scoped to date ranges because that is where backends struggle. A negated
  membership or string lookup is a plain `NOT IN` / `NOT LIKE`; a negated range must include
  `None` rows to satisfy this library's negation semantics, which not every query builder
  expresses. `fields.sentAtRange: True` alongside `negation.sentAtRange: False` is expected.
- These keys and their spellings come from the `vintasend-ts` sibling library, which already had
  `logical.*` and the three `negation.*` range keys. `negation.readAtRange` is new to both, and
  exists here because this library also has `fields.readAtRange`.

#### Case-insensitive string matching reported through the capability report
- `DEFAULT_BACKEND_FILTER_CAPABILITIES` gained `stringLookups.caseInsensitive`, defaulting to
  `True`. It reports whether a backend can match a string filter ignoring case, i.e. whether it
  honours `case_sensitive: False` on a `StringFilterLookup`.
- This sits alongside the existing `stringLookups.caseSensitive`, and the two are **independent
  capabilities rather than one flag and its negation**. A backend on a case-insensitive collation
  (MySQL's `*_ci`) cannot match case-sensitively and reports `caseSensitive: False`; a backend
  with no way to fold case cannot match case-insensitively and reports `caseInsensitive: False`.
  Most backends do both, hence both default to `True`.
- Previously only `caseSensitive` existed, so a caller wanting to know whether it could ask for a
  case-insensitive match had to infer it — and inferring one from the other inverts the answer for
  exactly the backends that have a constraint worth reporting.
- `stringLookups.caseInsensitive` is also the key the `vintasend-ts` sibling library uses, so a
  client consuming both ecosystems reads one key with one spelling. `vintasend-ts` has since added
  `stringLookups.caseSensitive` too, so both ecosystems now report the pair.

#### Pagination convention reported through the capability report
- `DEFAULT_BACKEND_FILTER_CAPABILITIES` gained `pagination.oneIndexed`, defaulting to `True`.
  It reports whether a backend's `page` argument is 1-indexed -- whether `page=1` is the first
  page -- and it covers every paginated backend method, not just `filter_notifications`.
- The key exists because the convention is silent when a caller gets it wrong: nothing raises,
  the caller simply serves the wrong page, skips the first record, or gets an empty first page.
  Anything translating between its own page numbering and a backend's should read this key
  rather than hardcode an assumption. It matters most across ecosystems: `vintasend-ts` defines
  the same key but defaults it to `false`, because its backends page from 0 while every backend
  here pages from 1. The key is the same; the correct value is not, so it has to be read.
- A 0-indexed backend declares `{"pagination.oneIndexed": False}` from `get_filter_capabilities`,
  the same way it would decline any other capability.

#### `UnconfirmedNotificationUpdateError` for writes a backend cannot confirm
- New exception in `vintasend/exceptions.py`, subclassing `NotificationUpdateError`, for the case
  where a backend issued a write but could not read back how many rows it affected -- so it cannot
  say whether the write applied.
- Distinct from a plain `NotificationUpdateError`, which means no row matched and the write
  definitively did not apply. A caller that needs certainty should re-read the notification.
- Documented on the `update_notification`, `mark_read`, and `cancel_notification` docstrings of both
  `NotificationService` and `AsyncIONotificationService`.
- First raised by `vintasend-sqlalchemy`, whose async backend reads `rowcount` off the result of an
  `UPDATE`: `AsyncSession.execute` is typed as returning a plain `Result`, which has no `rowcount`,
  and the backend now checks for the `CursorResult` it expects instead of assuming it.

### Backwards compatibility

Everything below is additive. A host that upgrades and changes nothing behaves exactly as it did:
`pin_template_versions` defaults to `False`, and every new seam member has a default.

- **`BaseNotificationTemplateRenderer.get_latest_template_version(template_key)`** is new,
  concrete, and returns `None`. A renderer whose templates are not versioned needs no changes.
  One that versions them overrides it, honours `notification.requested_template_version` in
  `render()`, and sets `template_version` on the `NotificationSendInput` it returns.
- **`BaseNotificationAdapter.send()` / `AsyncIOBaseNotificationAdapter.send()`** now return
  `NotificationSendInput | None` instead of `None`. Existing adapters return `None` implicitly and
  keep working -- the service records nothing for them. Return the send input to have the template
  version recorded.
- **`BaseNotificationBackend.store_template_version()` / its AsyncIO twin** are new, concrete, and
  no-ops. Unlike `store_git_commit_sha` they are deliberately not abstract, so an existing backend
  inherits them and keeps working; the only cost of not overriding one is that
  `used_template_version` stays `None` on the records that backend holds.
- **`persist_notification()` / `persist_one_off_notification()`** gained an optional
  `requested_template_version` keyword on the backend ABCs. The service passes it only when a
  version was actually pinned, so a backend that has not added the parameter is never handed it at
  runtime. Adding it to an implementation is a signature-only change; storing it needs a column.
- **Downstream packages updated in this release**:
  - `vintasend-django` -- both columns, migration `0007`, and both filter fields translated to `Q`.
  - `vintasend-sqlalchemy` -- both columns, migration `3c1a2b4d5e6f` (plus the
    `upgrade_notification_table_to_2_1()` op helper for host applications), `store_template_version`,
    and both filter fields translated to SQL on the sync and AsyncIO backends alike.
  - `vintasend-managed-templates` -- honours the pin, reports the version used, and accepts
    `version=2` inside composition tags.
  - `vintasend-django-templates-manager` -- the Django-ORM manager backend for the above.
  - `vintasend-templates-management-api` (`tools/`) -- exposes composition and `isAbstract` over HTTP.

  Each of these releases on its own cycle, from its own repository. This file covers the core
  package only; a package's own README is the reference for what it added.

- **`UnconfirmedNotificationUpdateError` needs no handling changes.** It subclasses
  `NotificationUpdateError`, so every existing `except NotificationUpdateError` -- including the
  send path's own `raise_on_failed_send` handling -- already catches it. Catch the new type
  directly only if you need to tell an indeterminate write apart from a failed one.
- **No action required for existing backends.** This adds a key to a data contract, not an
  abstract method. The new key defaults to `True`, which is what every backend in this library
  and every known downstream implementation already does, so a backend that says nothing keeps
  reporting the correct value. `get_filter_capabilities` is unchanged in signature and still has
  a working default.
- A downstream backend that is genuinely 0-indexed was already mismatched with the documented
  convention before this release; it should now declare `{"pagination.oneIndexed": False}` so
  callers can compensate.
- Callers asserting on the exact contents of `get_backend_supported_filter_capabilities()` will
  see ten additional keys. The in-repo test that does this (`test_capabilities_all_true_for_full_backend`)
  compares against `DEFAULT_BACKEND_FILTER_CAPABILITIES` rather than a literal, so it needed no
  change; a downstream test hardcoding the full dict will.
- A backend that cannot assemble `and` / `or` / `not` groups, or cannot negate a date range, was
  already unable to do so before this release and had no way to report it. It should now declare
  the relevant `logical.*` / `negation.*` key as `False`.
- A caller that had been treating `stringLookups.caseSensitive` as a proxy for "can this backend
  match case-insensitively" should switch to reading `stringLookups.caseInsensitive` directly. The
  two are not inverses, and the old inference is wrong for any backend that reports either.

## Version 2.0.0 (2026-07-23)

2.0 is a major release that bundles several feature sets: background notification sending through a
queue service, a composable filtering / ordering API, a dedicated attachment manager seam, git commit
SHA tracking, rendering a notification from historical template content, and multi-backend
replication. The breaking changes come from the background-sending rework and from new abstract
methods that every downstream backend and email renderer must implement; multi-backend replication
is additive and non-breaking on its own. See `MIGRATION_TO_2.0.0.md` for step-by-step upgrade
guidance.

### Features

#### Git commit SHA tracking
- New injected component: `BaseGitCommitShaProvider` (`vintasend.services.git_commit_sha_providers`)
  and its AsyncIO twin `AsyncIOBaseGitCommitShaProvider` expose a single method,
  `get_current_git_commit_sha() -> str | None`, that a host implements to report the git commit
  SHA of the revision currently running. It follows the same injection pattern as the queue
  service and attachment manager: an instance, a dotted import string, or the new
  `NOTIFICATION_GIT_COMMIT_SHA_PROVIDER` setting. Core ships the ABCs plus a reference fake,
  `FakeGitCommitShaProvider` / `FakeAsyncIOGitCommitShaProvider`; no default provider ships in
  core, and with none configured the feature is entirely off -- no SHA is ever resolved or
  written, and existing send/delayed_send flows are byte-for-byte unchanged.
- `Notification` and `OneOffNotification` gained a system-managed `git_commit_sha: str | None`
  field. Both `NotificationService` and `AsyncIONotificationService` resolve it at **send** time
  (not creation time) at the top of both `send()` and `delayed_send()`, so a scheduled
  notification records the revision that actually delivered it -- foreground or from a
  background worker. The provider is called on every send, but the resolved, normalized SHA
  (trimmed, lowercased, 40 hex characters) is only persisted when it differs from what is
  already stored, through a new dedicated backend method, `store_git_commit_sha`.
- A provider that raises is caught and logged, then treated exactly like a `None` return --
  audit metadata is never allowed to block a delivery. A provider returning a non-`None`,
  malformed value (not 40 hex characters once trimmed) raises `InvalidGitCommitShaError`.
- `git_commit_sha` is system-managed: it cannot be set through `create_notification`, and
  `update_notification` raises the new `GitCommitShaReassignmentError` if a caller passes it,
  mirroring the existing `tenant` reassignment guard. It is only ever written by the service
  itself, through `store_git_commit_sha`, while the row is still pending.
- See the README's "Git Commit SHA Tracking" section.

#### Background sending via a queue service
- Adapters opt in to background delivery by subclassing `BackgroundNotificationAdapter` (sync) or
  `AsyncIOBackgroundNotificationAdapter` (AsyncIO). When a background-capable adapter is used,
  `send()` enqueues the notification id to the configured queue service and returns immediately; a
  worker calls the service's `delayed_send(notification_id)` to deliver it. This decouples web
  request latency from notification delivery.
- The queue now carries only the notification id, not serialized notification data. The worker
  reloads the notification from the backend, so context is generated at delivery time (not at
  enqueue time) and attachments work on the background path for the first time.
- `NOTIFICATION_SERVICE_FACTORY` points to a callable that returns a ready `NotificationService`
  or `AsyncIONotificationService` for the worker. The factory runs once per process and the result
  is cached, enabling ORM sessions scoped to the worker rather than rebuilt per task.
- `AsyncIONotificationService` supports background sending via `AsyncIOBackgroundNotificationAdapter`
  and `AsyncIOBaseNotificationQueueService`.

#### Filtering, ordering, and resend
- `filter_notifications(filter, page, page_size, order_by=None)`, `count_notifications(filter)` and
  `get_backend_supported_filter_capabilities()` on both services. The composable filter vocabulary
  lives in `vintasend.services.notification_backends.filters`: field filters (scalar equality or
  list membership), string lookups (`exact` / `starts_with` / `ends_with` / `includes`, case
  sensitivity configurable), inclusive date ranges, and `and` / `or` / `not` groups that nest
  arbitrarily. An empty filter matches every notification. `get_backend_supported_filter_capabilities`
  reports which fields, lookups and sort fields the configured backend supports, so a client can
  grey out what it can't use. See the README's "Filtering and Ordering Notifications" section.
- `resend_notification(notification_id, use_stored_context_if_available=False)` on both services:
  clones a sent notification into a brand-new pending row and sends it immediately, leaving the
  original untouched. Refuses one-off notifications and notifications still scheduled in the future
  by raising `NotificationResendError`. `use_stored_context_if_available=True` reuses the source's
  stored context verbatim instead of regenerating it through the context registry.
- `Notification` and `OneOffNotification` gained `sent_at`, `read_at` and `tenant` fields
  (`datetime | None` / `datetime | None` / `str | None`, all defaulting to `None`).
  `mark_pending_as_sent` sets `sent_at`; `mark_sent_as_read` and `mark_sent_as_read_bulk` set
  `read_at`. `persist_notification` and `persist_one_off_notification` gained an optional `tenant`
  keyword. The filter vocabulary includes `sent_at_range`, `read_at_range` and `tenant` (equality
  or membership).

#### Render a notification from historical template content
- `render_email_template_from_content(notification, template_content, context)` on both services:
  given a notification, an `EmailTemplateContent` (`subject_template`, `body_template`, optional
  `preheader_template`), and a context -- typically a notification's stored `context_used` -- renders
  and returns the resulting `TemplatedEmail` without sending or persisting anything. This is a
  read-shaped preview/audit operation: no context is generated (the caller supplies it verbatim) and
  the notification's own stored templates are never consulted. Raises the new
  `NotificationRenderError` when the notification's type has no email adapter configured, or its
  configured renderer is not a `BaseTemplatedEmailRenderer`. See the README's "Rendering a
  notification from historical template content" section, including its injection-safety caveat.
- New abstract method `render_from_template_content` on `BaseTemplatedEmailRenderer`, mirroring
  `render`'s signature with an `EmailTemplateContent` replacing the stored template reference. The
  reference implementation, `FakeTemplateRenderer`, renders the supplied content directly. See
  "Breaking Changes" and "Backwards Compatibility" below.
- `TemplatedEmail` gained an optional `preheader: str | None = None` field, additive with a default,
  so it reproduces a historical preheader when one was supplied.

#### Attachment manager seam
- New attachment manager seam: `BaseAttachmentManager` and `AsyncIOBaseAttachmentManager`
  (`vintasend.services.attachment_managers`) own every byte of attachment storage —
  `upload_file`, `reconstruct_attachment_file`, and `delete_file_by_identifiers` — so a
  notification backend never reads, writes, or downloads a file itself. Core ships the ABCs plus a
  working reference, `FakeAttachmentManager` / `FakeAsyncIOAttachmentManager`; real managers (local
  disk, S3, Django storage, and so on) live in their own `vintasend-*` package. See
  [ATTACHMENTS.md](ATTACHMENTS.md).
- Both services take a new `attachment_manager` constructor argument (instance, dotted import
  string, or the new `NOTIFICATION_ATTACHMENT_MANAGER` setting), injected into the backend through
  the duck-typed `inject_attachment_manager` hook. A backend that predates this seam and has no
  such method is left untouched and keeps working with no attachment support.
- The attachment model is now checksum-indexed: `AttachmentFileRecord` describes one stored blob,
  and `StoredAttachment` is the join row linking a notification to it. `storage_metadata` is kept
  as a deprecated alias for the new `storage_identifiers` field.
- `NotificationAttachmentReference(file_id=...)` attaches an already-uploaded file by id instead of
  re-uploading it. `NotificationAttachment` (an upload) and `NotificationAttachmentReference` (a
  reference) are both accepted through the new `AnyNotificationAttachment` union, distinguished with
  the new `is_attachment_reference` type guard. Identical uploads are deduplicated on checksum and
  size; a reference to an unknown `file_id` raises the new `AttachmentFileNotFoundError`.
- `get_orphaned_attachment_files` returns file records no longer referenced by any notification, for
  a caller-driven, two-step reclamation. Nothing is deleted automatically.

#### Multi-backend replication
- Both services now accept `additional_backends`: a service can hold one primary backend plus
  zero or more extra backends kept in sync by replication, each addressable by a stable
  identifier. A backend declares its own identifier by overriding `get_backend_identifier()`;
  if it doesn't, the service assigns `backend-{n}` (the primary is always `backend-0`). Two
  configured backends resolving to the same identifier raise the new
  `DuplicateBackendIdentifierError` at construction. A service with no `additional_backends`
  is byte-for-byte identical to a single-backend 2.0 deployment.
- New identifier and routing surface on both services: `get_primary_backend_identifier`,
  `get_all_backend_identifiers`, `get_additional_backend_identifiers`, `has_backend`. Every read
  method (`get_notification`, `filter_notifications`, `get_future_notifications`, and the rest
  of the getters) gained an optional trailing `backend_identifier` keyword — omitted, it reads
  the primary exactly as before; an unknown identifier raises the new `BackendNotFoundError`.
- Every write (create, update, mark sent/failed/read, cancel, store context, and the one-off
  variants) now fans out to the additional backends after the primary write succeeds. The
  primary write is the source of truth: its result and any exception are the caller's, exactly
  as on a single-backend service. A replica that rejects a write is logged, not raised, and is
  reconciled by a later write or by `process_replication`. A replication conflict that looks
  like the replica already has the row being created, or lacks the row being updated, flips the
  operation once (create ↔ update) before giving up, so retries under partial failure
  self-heal instead of piling up errors.
- New constructor keyword `replication_mode: Literal["inline", "queued"]` (also settable via the
  new `NOTIFICATION_REPLICATION_MODE` setting, defaulting to `"inline"`). `"inline"` replicates
  on the request path, right after the primary write. `"queued"` enqueues one replication task
  per destination backend through a new `replication_queue_service` argument (also settable via
  the new `NOTIFICATION_REPLICATION_QUEUE_SERVICE` setting) and reuses the same host-factory
  worker model background sending already established. A missing replication queue service, an
  unresolvable notification id, or a failed enqueue for one backend all fall back to inline
  replication rather than silently dropping it — a broken queue is never a data-loss risk.
  `register_replication_queue_service` injects the queue service after construction, mirroring
  `register_queue_service`.
- New sibling seam: `BaseNotificationReplicationQueueService` and its AsyncIO twin
  `AsyncIOBaseNotificationReplicationQueueService`
  (`vintasend.services.notification_queue_services`) — a single method,
  `enqueue_replication(notification_id, backend_identifier)`. Core ships the ABCs plus a working
  reference, `FakeReplicationQueueService` / `FakeAsyncIOReplicationQueueService`.
- New management and monitoring surface on both services, for a replication dashboard:
  `verify_notification_sync(notification_id)` reads the record from every registered backend and
  reports, field by field, which backends hold it and whether they agree;
  `get_backend_sync_stats()` reports each backend's notification count and reachability, never
  letting one broken backend fail the others; `process_replication(notification_id,
  target_backend_identifier=None)` (and its no-target alias `replicate_notification`) converges
  one or every additional backend to the primary's current snapshot, returning
  `{"successes": [...], "failures": [...]}`; `migrate_to_backend(destination_backend_identifier,
  batch_size, source_backend_identifier=None)` pages through a source backend (the primary by
  default) copying every notification into a destination, idempotent on re-run and reporting
  per-record failures without aborting the rest of the batch. Copy-only — it never deletes from
  the source. The new `BackendMigrationError` covers migration-level misuse (migrating a backend
  onto itself, a non-positive `batch_size`).
- New optional backend methods, on both `BaseNotificationBackend` and
  `AsyncIOBaseNotificationBackend`, that make all of the above possible:
  `get_backend_identifier() -> str | None`, `apply_replication_snapshot_if_newer(snapshot) ->
  ApplyResult`, and `get_all_notifications()`. **These ship as concrete methods with working
  defaults, not `@abstractmethod`** — see "Backwards Compatibility" below for why, and for what
  that means for downstream backends.
- See the README's "Multi-Backend Configuration" section, including its "Failure semantics"
  subsection: replication is best-effort, not synchronous multi-master, offers no
  read-your-writes guarantee across backends, and this library never fails a primary over to a
  replica automatically.

### Bug Fixes
- `FakeFileBackend` and `FakeAsyncIOFileBackend` now stamp `created` and `modified` when a
  notification is persisted, and advance `modified` on updates and status transitions — matching a
  real ORM's `auto_now_add` / `auto_now`. Previously both fields stayed `None` for any notification
  created through the service, so `created_at_range` filters matched nothing and the default
  `created`-descending ordering fell through to the `id` tiebreaker.

_The following were first documented under 1.4.0 and 1.3.0. Those version numbers were bumped
in `pyproject.toml` but never published to PyPI, and the work shipped in 2.0.0 instead, so
their notes are folded in here._

- `NotificationService` and `AsyncIONotificationService` now reject two or more adapters that
  declare the same `notification_type`, raising the new
  `vintasend.exceptions.DuplicateNotificationAdapterError` at construction. Previously both
  adapters were kept, and because the send loop has no `break`, every notification of that type
  was sent twice: the second `mark_pending_as_sent` then failed because the row was no longer
  `PENDING_SEND`, and if the first adapter failed while the second succeeded the notification was
  marked FAILED and then overwritten as SENT. The error message names the offending notification
  type and the `adapter_import_str` of every adapter declaring it.
- `create_one_off_notification` now validates `email_or_phone` before anything is persisted, on
  both services, raising the new `vintasend.exceptions.InvalidOneOffNotificationRecipientError`.
  An empty string, a whitespace-only string, or a value that is neither an email address nor a
  10-to-15-digit phone number (optionally `+`-prefixed) previously persisted a notification that
  could never be delivered. Validation is on format only; it does not check deliverability. Both
  new exceptions derive from `NotificationError`, which derives from `ValueError`, so existing
  `except ValueError` handlers keep working.
- `AsyncIONotificationService` now accepts the same `(import_str, kwargs)` adapter tuple form
  that `NotificationService` already accepted, for example
  `notification_adapters=[(("pkg.Adapter", {"k": 1}), "pkg.Renderer")]`. The async construction
  helper, `get_asyncio_notification_adapters`, already handled this shape; the service's own
  validation guard did not, and rejected it with `NotificationError("Invalid notification
  adapters")` before the helper ever ran.

### Internal Improvements
_The following were first documented under 1.4.0 and 1.3.0. Those version numbers were bumped
in `pyproject.toml` but never published to PyPI, and the work shipped in 2.0.0 instead, so
their notes are folded in here._
- Extracted the file-attachment and context-function helpers duplicated between `NotificationService`
  and `AsyncIONotificationService` into `vintasend.services.service_utils`; both classes now delegate
  to one shared implementation of each. No public signature changed.
- Closed sync/AsyncIO parity gaps between the two services: `AsyncIONotificationService.send_pending_notifications`
  now tracks sent/failed counters and logs the same summary lines its sync twin,
  `NotificationService.send_pending_notifications`, always has; `AsyncIONotificationService.__init__`
  now initializes the `NotificationSettings` singleton up front, matching `NotificationService.__init__`.
- Importing `vintasend.services.notification_service` no longer imports `requests` as a side effect --
  `download_from_url` now imports it lazily, at call time, with a friendly `ImportError` if it's
  missing.

### Breaking Changes

1. **`raise_on_failed_send` defaults to `False`.** In 1.x, send failures raised
   `NotificationSendError` and similar exceptions. In 2.0 they are logged but not raised by default.
   Applications that catch these exceptions should pass `raise_on_failed_send=True` to
   `NotificationService` / `AsyncIONotificationService` to restore 1.x behavior.
2. **Background adapter `delayed_send` signature changed.** The adapter marker method now takes only
   `notification_id`, not `(notification_dict, context_dict)`. Core never calls this method;
   delivery happens via the adapter's `send()` after the worker loads the notification. Adapter
   authors must move background delivery logic from `delayed_send` to `send()`.
3. **Deleted serialization hooks and types.** The eight abstract serialize/restore methods on
   `AsyncNotificationProtocol`, plus the types `NotificationDict` and `OneOffNotificationDict`, are
   deleted. No serialization is needed with id-only payloads.
4. **`NOTIFICATION_SERVICE_FACTORY` is required for background sending.** The worker needs a factory
   callable to rebuild the service in its own process. Web and worker must also share the same
   `NOTIFICATION_BACKEND` and `NOTIFICATION_QUEUE_SERVICE` settings, or the worker silently fails to
   find notifications.
5. **Adapter rename.** `AsyncBaseNotificationAdapter` is renamed to `BackgroundNotificationAdapter`;
   the old name is kept as a silent alias for compatibility. New AsyncIO background adapters
   subclass `AsyncIOBackgroundNotificationAdapter`.
6. **New abstract methods on both backend base classes — every custom backend subclass MUST
   implement them before it can be instantiated against 2.0:**
   - From the filtering API: `filter_notifications`. (`get_filter_capabilities` and
     `count_notifications` are concrete defaults, so they need no changes but SHOULD be overridden
     for efficiency.)
   - From the attachment seam: `store_attachment_file_record`, `get_attachment_file_record`,
     `find_attachment_file_by_checksum`, `delete_attachment_file`, `get_orphaned_attachment_files`,
     `get_attachments`, and `delete_notification_attachment`. (`inject_attachment_manager` is added
     too, but concrete with a default, so it needs no change.)
7. **New abstract method on `BaseTemplatedEmailRenderer` — every custom email renderer subclass MUST
   implement `render_from_template_content` before it can be instantiated against 2.0.** See
   "Backwards Compatibility" below.

### New exceptions
- `TenantReassignmentError`: raised by `update_notification` when `tenant` appears in the update
  kwargs. `tenant` cannot be changed after a notification is created.
- `NotificationResendError`: raised by `resend_notification` for a one-off notification or one still
  scheduled in the future.
- `AttachmentFileNotFoundError`, `AttachmentUploadError`, and `UnsupportedAttachmentFileTypeError`
  for the attachment paths.
- `NotificationRenderError`: raised by `render_email_template_from_content` when the notification's
  type has no email adapter configured, or its configured renderer is not a
  `BaseTemplatedEmailRenderer`. Distinct from the existing `NotificationTemplateRenderingError`
  family, which covers a renderer failing while actually rendering a template it was handed.
- `BackendNotFoundError`: raised when a `backend_identifier` argument (on a read, or on
  `process_replication` / `migrate_to_backend`) names a backend that isn't registered on the
  service.
- `DuplicateBackendIdentifierError`: raised at construction when two configured backends resolve
  to the same identifier.
- `ReplicationError`: raised when a replication worker is asked to replicate a notification id
  that doesn't resolve on the primary backend.
- `BackendMigrationError`: raised by `migrate_to_backend` for migration-level misuse -- the same
  backend named as both source and destination, or a non-positive `batch_size`.
- All derive from `NotificationError`, which derives from `ValueError`, so existing
  `except ValueError` handlers keep working.

### Backwards Compatibility
- **`AsyncBaseNotificationAdapter` is now an alias** for `BackgroundNotificationAdapter`. Existing
  imports keep working; `BackgroundNotificationAdapter` is the recommended name for new code.
- **`raise_on_failed_send` silent behavior change.** Code that does not catch send exceptions sees
  the same behavior (failures logged). Code that catches `NotificationSendError` must pass
  `raise_on_failed_send=True` to restore 1.x semantics.
- The `sent_at`, `read_at` and `tenant` fields are additive with `None` defaults, appended after the
  existing ones, so existing positional construction and existing callers are unaffected. The
  optional trailing `tenant` keyword on `persist_notification` / `persist_one_off_notification` is
  forwarded only when a caller passes it, so a backend built against a pre-2.0 signature keeps
  working for tenant-less callers.
- `update_notification` now raises `TenantReassignmentError` if `tenant` is present in the update
  kwargs (checked on the raw dict). `send()` gained an optional trailing `context` keyword used
  internally by `resend_notification`; every existing caller passes no `context` and is unaffected.
- `UpdateNotificationKwargs.attachments` intentionally stays typed `list[StoredAttachment]`, not
  widened to `AnyNotificationAttachment`, because `persist_notification_update` has no upload path.
- **Downstream packages.** `vintasend-celery` is significantly affected and requires a 2.0 release
  of its own. `vintasend-django` must implement the new filter and attachment seams (plus a schema
  migration for the new attachment table) before it can pin `vintasend>=2.0.0`. `vintasend-sqlalchemy`
  cannot adopt this release until its own catch-up plan lands, since it is already missing methods
  from 1.2.0.
- **New abstract method**: `store_git_commit_sha(notification_id, git_commit_sha)` was added to
  `BaseNotificationBackend` and `AsyncIOBaseNotificationBackend`. Every downstream backend
  implementation MUST implement it before it can be instantiated -- a subclass missing it raises
  `TypeError` at construction. This repo releases first; `vintasend-django` follows with a
  matching release that widens its `vintasend` pin. `vintasend-sqlalchemy` adopts it as part of
  its ongoing catch-up plan.
- **New abstract method**: `render_from_template_content(notification, template_content, context)`
  was added to `BaseTemplatedEmailRenderer`. Every downstream email renderer implementation MUST
  implement it before it can be instantiated -- a subclass missing it raises `TypeError` at
  construction. `vintasend-django` and `vintasend-jinja` are the two affected packages; both must
  implement it and widen their `vintasend` pin before they can be released against `vintasend>=2.0.0`.
  This repo releases first. Backends, adapters, and non-email renderers are untouched.
- With no `git_commit_sha_provider` configured (the default), `send()`, `delayed_send()`,
  `create_notification()`, and `update_notification()` behave exactly as before this release -- that
  feature is additive for every caller who does not opt in. Likewise,
  `render_email_template_from_content` is a wholly new, opt-in entrypoint: no existing method's
  signature or semantics changed to support it, and no notification is sent or persisted by calling
  it.
- **Multi-backend replication is non-breaking, despite touching the backend seam.** The three new
  backend methods -- `get_backend_identifier`, `apply_replication_snapshot_if_newer`, and
  `get_all_notifications` -- ship as **concrete methods with working defaults**, not
  `@abstractmethod`. No downstream backend breaks at instantiation: a single-backend deployment
  that passes no `additional_backends` is byte-for-byte identical to its pre-multi-backend
  behavior, and every existing custom backend subclass keeps working unmodified against
  `vintasend>=2.0.0`. The three new methods are optional overrides a backend picks up at its own
  pace; `vintasend-django` and `vintasend-sqlalchemy` need no release to remain compatible, and
  MAY later override the hooks for efficiency (a real identifier, an `update_or_create`-style
  newer-wins upsert, an unpaginated query).
- **This is a deliberate, documented exception to the project's abstract-by-default seam rule**
  (see `AGENTS.md`), which every other seam addition in this 2.0 release follows. Multi-backend
  replication is opt-in -- most deployments never configure `additional_backends` -- so forcing
  every downstream backend to implement replication hooks for a feature they never use would be
  the wrong trade. The concrete defaults are functionally complete on their own:
  `get_backend_identifier` falls back to a service-assigned `backend-{n}`, and
  `apply_replication_snapshot_if_newer`'s default decline routes the service through a
  read-then-write fallback (`_converge_replica_to_snapshot`) that works against any backend's
  existing primitives.
- **Best-effort consistency, not synchronous multi-master.** A replica write failure is logged,
  not raised -- the primary write has already committed and is never rolled back. There is no
  read-your-writes guarantee across backends: a read against a named replica immediately after a
  write may not yet reflect it, especially in `"queued"` mode. `verify_notification_sync` and
  `process_replication` are the reconciliation tools, not a substitute for that guarantee.
- **The read-then-write fallback has a real fidelity limit.** On a backend that does not
  implement `apply_replication_snapshot_if_newer`, inline and queued replication fall back to
  `_converge_replica_to_snapshot`, which can only update a row the replica already holds -- it
  cannot create a row with the primary's id, since no base backend primitive lets a caller choose
  an id on create. It also does not converge attachments or intermediate audit fields (e.g.
  `sent_at`) that aren't part of `UpdateNotificationKwargs`. A backend that needs full-fidelity
  replication -- including creating rows on a fresh replica, and converging attachments -- should
  implement `apply_replication_snapshot_if_newer` rather than relying on this fallback.

_The following were first documented under 1.4.0 and 1.3.0. Those version numbers were bumped
in `pyproject.toml` but never published to PyPI, and the work shipped in 2.0.0 instead, so
their notes are folded in here._

- No seam method was added, renamed, or removed, and no existing method signature or semantic
  changed. Custom backends, adapters and template renderers need no code changes, and the
  `vintasend-django`, `vintasend-sqlalchemy`, `vintasend-celery`, and renderer/adapter packages
  need no release.
- **An application that configures two adapters for the same notification type now fails at
  service construction instead of starting.** This is deliberate -- that configuration was
  double-sending every notification of that type and corrupting its status -- but the failure
  appears at deploy time rather than at upgrade time. The remedy is to remove the duplicate
  adapter from `NOTIFICATION_ADAPTERS` (or from the `notification_adapters` argument). The error
  message names the type and every adapter declaring it, so it tells you exactly what to delete.
- **Custom adapters must set `notification_type` to a `NotificationTypes` member.** It has always
  been declared that way on `BaseNotificationAdapter` and `AsyncIOBaseNotificationAdapter`, but it
  is not an `@abstractmethod`, so an adapter that omitted it or declared it as a plain `str`
  previously failed only at send time. Service construction now reads it, so such an adapter fails
  earlier and with an `AttributeError` rather than a `NotificationError`.
- **Callers passing an empty or malformed `email_or_phone` to `create_one_off_notification` now
  get an exception where they previously got a persisted notification.** Those notifications were
  never deliverable. Existing rows are untouched -- validation is on the create path only, and
  `update_notification` is unchanged.

- `AsyncIONotificationService.__init__` now constructs the `NotificationSettings` singleton
  immediately, matching `NotificationService.__init__`. `NotificationSettings` is a singleton:
  the first construction wins, and every later `config` argument is ignored. An application
  that builds `AsyncIONotificationService(...)` with `config=None` before anything else
  constructs `NotificationSettings` with a real config will now get the `None`-derived
  settings everywhere, where previously the in-request construction with the real config
  would have won. Build the service after your config is available, or pass `config` to the
  service, rather than relying on some other later construction of `NotificationSettings` to
  supply it.
- A host application that relied on the transitive `requests` import (see the `requests` bullet
  above) instead of depending on `requests` itself will see that `ImportError` move from import
  time to call time. `requests` remains a declared runtime dependency in `pyproject.toml`, so
  this affects nobody who installs the package normally.

### Operational Requirements
- **Drain or dual-register the Celery queue before deploying.** Tasks queued under 1.x carry a
  different payload format and will fail against 2.0. Either drain the queue before deploying the
  2.0 worker or register the new entrypoint under a new task name and run both workers until the old
  queue empties. See `MIGRATION_TO_2.0.0.md`.

### Upgrade Path
1. Read `MIGRATION_TO_2.0.0.md` for the breaking changes and the deploy procedure.
2. If you use background sending, set up `NOTIFICATION_SERVICE_FACTORY`.
3. If you maintain an adapter, move to `BackgroundNotificationAdapter` /
   `AsyncIOBackgroundNotificationAdapter` and move `delayed_send` logic to `send()`.
4. If you maintain a backend, implement the new filter and attachment abstract methods.
5. If you maintain an email template renderer, implement `render_from_template_content`.
6. Test end-to-end, including attachments in background sends (now supported), then drain the queue
   and deploy the 2.0 worker.

## Version 1.2.0 (2026-06-14)

### Features

* List ALL in-app notifications (read + unread) on the backend ABCs and services:
  `filter_all_in_app_notifications` (unpaginated) and `filter_in_app_notifications(page, page_size)`
  (paginated). "All" = `notification_type == IN_APP` and `status in (SENT, READ)`; internal
  pipeline states (PENDING_SEND, FAILED, CANCELLED) are never exposed to end users.
* Count helpers `count_in_app_notifications` and `count_in_app_unread_notifications` on the
  backend ABCs (concrete defaults derived from the existing iterables; backends SHOULD override
  for efficiency), and `get_in_app_notifications_count` / `get_in_app_unread_count` on the
  services. Combined with the paginated list methods these let callers build
  count / next / previous envelopes for both the unread and the all lists.
* Service method `get_in_app_notifications(user_id, page=1, page_size=10)` mirroring
  `get_in_app_unread` (including the "No in-app notification adapter found" guard).
* Bulk mark-as-read: `mark_sent_as_read_bulk(notification_ids, user_id=None)` on the backend
  ABCs and `mark_read_bulk(notification_ids, user_id=None)` on the services. Idempotent
  (already-READ / missing / not-owned / non-SENT ids are skipped, never an error), optionally
  scoped to `user_id` (rows owned by others are never touched), and returns the final READ state
  for the requested ids.
* `Notification` and `OneOffNotification` dataclasses gained optional `created` and `modified`
  timestamp fields (`context_used` already existed), defaulting to `None` so existing
  constructors and non-Django backends keep working.

### Backwards compatibility

* Three new abstract methods were added to `BaseNotificationBackend` and
  `AsyncIOBaseNotificationBackend`: `filter_all_in_app_notifications`,
  `filter_in_app_notifications`, and `mark_sent_as_read_bulk`. Custom backend subclasses MUST
  implement them. The two `count_*` methods are concrete defaults, so they require no changes
  but SHOULD be overridden for efficiency.
* No existing method signature or semantic changed; this is an additive minor release.

## Version 1.1.3 (2026-06-03)
- Bumped version to follow the officially-supported implementations

## Version 1.1.2 (2026-06-03)
- Fixed bug in periodic_send_pending_notifications. We were only sending notifications if the first adapter configured was async, now we're searching through the list.

## Version 1.1.1 (2026-06-03)

### Bug Fixes
- Replaced deprecated `asyncio.iscoroutinefunction` with `inspect.iscoroutinefunction` (removal slated for Python 3.16)

### Build Improvements
- Widened Python constraint to `<3.15` and added `py314` to the tox envlist for full Python 3.14 support

## Version 1.1.0 (2026-06-03)

### Build Improvements
- Added Python 3.14 to the CI and tox test matrix
- Bumped publish workflow to Python 3.13 for stable releases
- Pinned local Python version via `.python-version`

### Dependencies
- Updated project dependencies (`pyproject.toml` / `poetry.lock`)

## Version 1.0.1 (2025-09-16)

### Bug Fixes
- **Fixes bug on async adapters**: The instanciation of the service with strings wasn't enabling using adapters with kwargs

### Build Improvements
- Simplified publish workflow
- Fix duplicate runs on every push


## Version 1.0.0 (2025-09-16)

### 🚀 Major Features

#### File Attachments Support
- **NEW**: Added comprehensive file attachment support for notifications
- **Multiple Input Types**: Support for file paths, URLs, bytes data, file-like objects, and Path objects  
- **URL Downloads**: Automatic download of remote files from HTTP/HTTPS, S3, Google Cloud Storage, and Azure Blob Storage URLs
- **Content Type Detection**: Automatic MIME type detection based on file extensions
- **Inline Attachments**: Support for inline images in HTML emails with `is_inline` flag
- **Backend Integration**: New storage interfaces for backends to implement attachment persistence
- **Adapter Integration**: Updated adapter interfaces to handle attachments in email sending

#### One-Off Notifications
- **NEW**: Send notifications directly to email addresses or phone numbers without requiring user IDs
- **Direct Targeting**: Use email addresses or phone numbers as direct targets
- **Use Cases**: Perfect for welcome emails, marketing campaigns, and external party notifications
- **Full Feature Support**: One-off notifications support all standard features including attachments, scheduling, and templating

### 🔧 API Enhancements

#### Notification Service
- Added `attachments` parameter to `create_notification()` method
- Added `attachments` parameter to `create_one_off_notification()` method  
- Added `attachments` parameter to `update_notification()` method
- New `create_one_off_notification()` method for direct email/phone targeting
- Enhanced AsyncIO support for all new features

#### Data Classes
- **NEW**: `NotificationAttachment` class for defining file attachments
- **NEW**: `StoredAttachment` class for backend-stored attachment metadata
- **NEW**: `OneOffNotification` class for non-user-targeted notifications
- **NEW**: `FileAttachment` type alias supporting multiple input formats
- **NEW**: `AttachmentFile` abstract base class for stored file access

#### Backend Interfaces
- Added attachment storage methods to `BaseNotificationBackend`
- Added one-off notification persistence to backend interfaces
- Enhanced AsyncIO backend interfaces with attachment support
- New abstract methods for attachment lifecycle management

#### Adapter Interfaces  
- Enhanced adapter interfaces to handle attachments in notification sending
- Updated template renderer interfaces for attachment-aware rendering
- Backward compatible changes with optional attachment parameters

### 🔄 Backward Compatibility
- All existing APIs remain fully functional
- Optional attachment parameters maintain backward compatibility
- Existing notifications continue to work without modification
- No breaking changes to core interfaces

### 🧪 Testing & Quality
- Comprehensive test suite for attachment functionality (1300+ test lines)
- Tests for all file input types and edge cases
- AsyncIO and sync testing coverage
- Validation and error handling test cases
- End-to-end attachment workflow testing

### 📚 Documentation
- Updated README with attachment examples and usage patterns
- New glossary entries for attachments and one-off notifications
- AsyncIO examples for all new features
- Import statements updated for new classes

### 🔧 Dependencies & Infrastructure
- Updated setuptools dependency for security improvements
- Enhanced type hints and type safety
- Improved error handling and validation
- Added comprehensive docstrings for new features

### 📋 Migration Guide
For backend and adapter package maintainers:
- See `MIGRATION_TO_1.0.0.md` for detailed implementation guidance
- New abstract methods need implementation in external packages
- Stub implementations provided as reference
- Backward compatibility maintained for gradual migration

---

## Version 0.1.4 (Initial Release)

Initial version of VintaSend with core notification functionality.
