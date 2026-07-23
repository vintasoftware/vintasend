# Attachment Manager Seam — Implementation Plan

Ports the `vintasend-ts` v0.5.0 attachment architecture: file storage becomes a fourth seam owned by
an attachment manager, backends own only database rows, and the two communicate through an opaque
`StorageIdentifiers` blob. Adds the checksum-indexed file record, the join table, and
reference-by-id inputs.

## 1. Goals

1. Introduce `BaseAttachmentManager` / `AsyncIOBaseAttachmentManager` as a fourth seam owning all
   file I/O, so a backend never reads, writes, or downloads a byte.
2. Split the flat attachment model into a checksum-indexed `AttachmentFileRecord` plus a join row, so
   one stored blob can serve many notifications and identical uploads deduplicate.
3. Add `NotificationAttachmentReference` so a caller can attach an already-uploaded file by id
   instead of re-uploading it.
4. Give storage-less backends — SQLAlchemy above all — a working attachment story that does not
   require them to implement file storage themselves.
5. Fix `vintasend-django`'s `_store_attachments`, which cannot accept a real `NotificationAttachment`.

Non-goals:

- **No `LocalFileAttachmentManager` in core.** Per the Step 0 decision, core ships the ABC and a
  fake; real managers live in `vintasend-*` packages, exactly as the other three seams work.
- **No data migration.** See **Guiding Decisions** — the Django attachment path has never accepted a
  real attachment, so there is no production data to migrate.
- **No `vintasend-sqlalchemy` work.** It has no attachment support and is already non-conformant with
  the 1.2.0 ABC. Bringing it up to date is its own plan in its own repo.
- **No adapter-side attachment rendering changes** beyond removing Django's reach-through into the
  concrete file class. Inline/CID handling stays as it is.
- **No orphan-sweep scheduling.** The plan adds `get_orphaned_attachment_files`; wiring a periodic
  job to call it is the host application's business.

## 2. Guiding Decisions

| Decision | Resolution |
|---|---|
| **Manager owns bytes, backend owns rows** | The split TS landed in v0.5.0. It is the whole point: a backend that can persist a row should not also need to know about S3, and today `vintasend-sqlalchemy` supports neither, while `vintasend-django` gets storage only because `FileField` happens to provide it. Separating them means any backend works with any manager. |
| **`StorageIdentifiers` is opaque to the backend** | A `dict[str, Any]` with a required `id`, persisted as JSON and never parsed by the backend. It is the mechanism that makes "any backend, any manager" true — the backend's only legal operation on it is handing it back to whichever manager was injected. |
| **Duck-typed injection, not an abstract method** | TS injects via `injectAttachmentManager` guarded by an `in` check, and does not declare it on the backend interface. Python mirrors this with `hasattr(backend, "inject_attachment_manager")`, so a backend that does not do attachments needs no changes at all. |
| **Backend attachment methods are abstract** | Per the Step 0 compatibility answer, new seam methods land as `@abstractmethod` with a minor bump and a mandatory `### Backwards compatibility` note. This diverges from TS, where all eight are optional. It is the more honest contract but it breaks every downstream backend at instantiation until updated — which is why `vintasend-django` is in scope and `vintasend-sqlalchemy` needs its catch-up plan before it can adopt this release. |
| **`reconstruct_attachment_file` stays synchronous** | TS deliberately kept this one non-`Promise`: it builds a handle from identifiers, it does not perform I/O. Keeping it sync in Python means the asyncio manager ABC differs from the sync one by fewer methods, and a handle can be constructed inside a serializer without an await. |
| **`storage_metadata` keeps working via a deprecated alias** | TS renamed it to `storageIdentifiers` and called it breaking. In Python the field is currently written only by [fake_backend.py:305](../vintasend/services/notification_backends/stubs/fake_backend.py#L305), so a `@property` alias costs nothing and keeps this a minor rather than forcing a major on a field nobody populates. |
| **`NotificationAttachment` stays the upload dataclass** | Making it a union would break every downstream `isinstance(x, NotificationAttachment)`. Instead: keep it as-is, add `NotificationAttachmentReference` as a sibling, and introduce `AnyNotificationAttachment` as the union used in signatures. TS could use a structural union for free; Python cannot. |
| **URL download survives, relocated onto the manager** | It is documented public behaviour ([README.md:95-98](../README.md)) and tested. TS rejects URLs, but removing a documented feature to match a sibling library is a bad trade. It moves from the service — where it is currently dead code — onto `BaseAttachmentManager` as a concrete helper, which removes the layering inversion without removing the feature. |
| **`is_inline` is preserved** | Python-only, with no TS counterpart. Documented in README and RELEASE_NOTES and asserted in the fakes. A port must not regress it. |
| **No feature flag** | No flag module in this library. The compatibility boundary is the release plus its `### Backwards compatibility` note. |

## 3. Data Model Changes

### 3.1 New core types

In [dataclasses.py](../vintasend/services/dataclasses.py):

```python
StorageIdentifiers = dict[str, Any]  # must carry a non-empty "id"; all other keys manager-defined


@dataclass
class AttachmentFileRecord:
    id: str
    filename: str
    content_type: str
    size: int
    checksum: str            # sha256 hex
    created_at: datetime.datetime
    updated_at: datetime.datetime
    storage_identifiers: StorageIdentifiers


@dataclass
class NotificationAttachmentReference:
    file_id: str
    description: str | None = None
    is_inline: bool = False


AnyNotificationAttachment = NotificationAttachment | NotificationAttachmentReference
```

Plus `is_attachment_reference(attachment) -> bool`, mirroring TS's type guard.

### 3.2 `StoredAttachment` changes

Add `file_id: str` (the `AttachmentFileRecord` it points at; `id` remains the join-row id) and
`storage_identifiers`, with `storage_metadata` retained as a deprecated `@property` alias.

### 3.3 The attachment manager seam

```python
# vintasend/services/attachment_managers/base.py
class BaseAttachmentManager(ABC):
    @abstractmethod
    def upload_file(self, file: FileAttachment, filename: str,
                    content_type: str | None = None) -> AttachmentFileRecord: ...

    @abstractmethod
    def reconstruct_attachment_file(self, storage_identifiers: StorageIdentifiers) -> AttachmentFile: ...

    @abstractmethod
    def delete_file_by_identifiers(self, storage_identifiers: StorageIdentifiers) -> None: ...

    # concrete, public so backends may call them
    def detect_content_type(self, filename: str) -> str: ...
    def calculate_checksum(self, data: bytes) -> str: ...
    def file_to_bytes(self, file: FileAttachment) -> bytes: ...   # absorbs read_file_data / is_url / download_from_url
```

`AsyncIOBaseAttachmentManager` mirrors it with `async def` on the three abstract methods;
`reconstruct_attachment_file` stays sync in both.

### 3.4 New backend methods

Seven, abstract on both `BaseNotificationBackend` and `AsyncIOBaseNotificationBackend`:
`store_attachment_file_record`, `get_attachment_file_record`, `find_attachment_file_by_checksum`,
`delete_attachment_file`, `get_orphaned_attachment_files`, `get_attachments`,
`delete_notification_attachment`. Plus non-abstract `inject_attachment_manager` and a module-level
`supports_attachments(backend)` guard.

### 3.5 Django schema

`vintasend_django/models.py` gains `AttachmentFile` — `filename`, `content_type`, `size`,
`checksum` (indexed), `storage_identifiers` (`JSONField`), `created`, `modified` — and `Attachment`
becomes a join table: FK to notification (`CASCADE`), FK to `AttachmentFile` (`PROTECT`, so a file
referenced by a live notification cannot vanish), plus `description` and `is_inline`. One schema
migration; **no data migration** (see **Risk & Rollout Notes**).

## 4. Phased Rollout

Bundled per Step 0, but split so the seam, the wiring, the dedup layer and the Django work are each
independently reviewable.

### Phase 1 — Attachment manager seam and types

**Goal**: the manager ABCs, the new dataclasses, and a working fake exist. Ship value: none on its
own — nothing consumes them yet. Foundation phase because Phases 2-4 all build on these types, and
landing them separately gives downstream implementers a published interface to start against.

**Feature flag**: none — purely additive new surface.

Changes:

1. New `@vintasend/services/attachment_managers/__init__.py`, `base.py`, `asyncio_base.py` per
   **Data Model Changes**.
2. `file_to_bytes` absorbs the logic the
   [shared-helpers plan](2026-07-22-SHARED_SERVICE_HELPERS_IMPLEMENTATION_PLAN.md) extracted into
   `service_utils.read_file_data` / `is_url` / `download_from_url`. Those functions move here and
   `service_utils` stops re-exporting them.
3. New `@vintasend/services/attachment_managers/stubs/fake_attachment_manager.py`:
   `FakeAttachmentManager` and `FakeAsyncIOAttachmentManager`, storing bytes in an in-memory dict
   keyed by generated id. Per `AGENTS.md`, complete and working, never raising.
4. [dataclasses.py](../vintasend/services/dataclasses.py): add `StorageIdentifiers`,
   `AttachmentFileRecord`, `NotificationAttachmentReference`, `AnyNotificationAttachment`,
   `is_attachment_reference`; add `file_id` and `storage_identifiers` to `StoredAttachment` with the
   `storage_metadata` alias.
5. [helpers.py](../vintasend/services/helpers.py): `get_attachment_manager` and its asyncio twin.
6. [app_settings.py](../vintasend/app_settings.py): `NOTIFICATION_ATTACHMENT_MANAGER` across all six
   layers.

Spec use-case: no spec — ports `BaseAttachmentManager` from `vintasend-ts` v0.5.0.

Tests:

- **Unit**: `@vintasend/tests/test_services/test_attachment_managers.py` — new. Round trip through
  `FakeAttachmentManager`; `calculate_checksum` is stable sha256; `detect_content_type` falls back to
  `application/octet-stream`; `file_to_bytes` over path, `Path`, file-like, bytes and URL inputs;
  `is_attachment_reference` discriminates correctly.
- **Integration**: `@vintasend/tests/test_app_settings.py` — the new setting resolves across env and
  all three frameworks.

**Suggested AI model**: Tier 3. Several new types plus two ABCs plus stubs, with a subtlety: the
sync/asyncio split must not accidentally make `reconstruct_attachment_file` async.

**Reusable skills**: `Skill(add-env-var)` for `NOTIFICATION_ATTACHMENT_MANAGER`.

Acceptance: `FakeAttachmentManager().upload_file(b"x", "a.txt")` returns an `AttachmentFileRecord`
with a sha256 checksum and `storage_identifiers["id"]` set, and
`reconstruct_attachment_file(record.storage_identifiers).read()` returns `b"x"`.

### Phase 2 — Wire the manager into the service and backend seam

**Goal**: the service injects a manager into any backend that accepts one, the backend seam declares
the attachment methods, and file I/O leaves the service entirely.

**Feature flag**: none — the abstract-method additions are gated by the release.

Changes:

1. [notification_backends/base.py](../vintasend/services/notification_backends/base.py): add the
   seven abstract methods after `:216`, plus non-abstract `inject_attachment_manager` and
   module-level `supports_attachments`. Widen `persist_notification` (`:58-72`) and
   `persist_one_off_notification` (`:74-90`) to accept `AnyNotificationAttachment`.
2. [notification_backends/asyncio_base.py](../vintasend/services/notification_backends/asyncio_base.py):
   the same, `async`, with the `lock` parameter on the write-shaped methods.
3. [notification_service.py](../vintasend/services/notification_service.py) `__init__` on both
   services (`:99-140`, `:767-804`): accept `attachment_manager: BaseAttachmentManager | str | None`;
   inject into the backend when it exposes `inject_attachment_manager`.
4. Delete `_read_file_data`, `_is_url`, `_download_from_url` from both services (`:197-232`,
   `:841-882`) — they moved to the manager in Phase 1. Give `_validate_attachments` (`:180-195`,
   `:824-839`) a real body: reject an attachment that is neither an upload nor a reference, and
   reject a reference with an empty `file_id`.
5. `create_notification` (`:289-343`) and `create_one_off_notification` (`:345-392`), plus asyncio
   twins: accept `AnyNotificationAttachment`. **Stop passing `attachments=` when the list is empty**
   — this is what currently makes `create_notification` raise `TypeError` against
   `SQLAlchemyNotificationBackend`.
6. [fake_backend.py](../vintasend/services/notification_backends/stubs/fake_backend.py): rewrite
   `_store_attachments` (`:281-349`) and the asyncio copy (`:591-660`) to delegate every byte
   operation to the injected manager. Delete its private `_read_attachment_data` / `_is_url` /
   `_download_from_url` — the third copy of that logic in the repo.
7. [notification_adapters/base.py](../vintasend/services/notification_adapters/base.py) and the
   asyncio twin: add `supports_attachments` property (default `False`) and a concrete
   `prepare_attachments(attachments)` that warns when `supports_attachments` is `True` but the method
   was not overridden. Concrete and defaulted, so no adapter package breaks.

Spec use-case: no spec — ports TS's injection model and optional-backend-method surface.

Tests:

- **Unit**: [test_notification_attachments.py](../vintasend/tests/test_services/test_notification_attachments.py)
  — the ~440 lines at `:909-1345` testing `_read_file_data` / `_is_url` / `_download_from_url` as
  *service* methods move to the manager test module. Do not delete the assertions; relocate them.
- **Integration**: same file `:154-298`, `:430-563`, `:759-884` — rewritten so create-with-attachment
  goes service → backend → injected manager, asserting the backend performs no file I/O itself.
- **Regression**: a backend with **no** `inject_attachment_manager` still constructs and persists a
  notification with no attachments — proof the duck-typed injection is genuinely optional.

**Suggested AI model**: Tier 4. Touches both service classes, both backend ABCs, both adapter bases
and the fakes, while relocating a large test suite. High blast radius on a public seam.

**Review models**: reviewer Tier 4 — this phase changes the backend ABC, which every downstream
package implements. A missed method or a wrong default silently breaks third-party backends at
instantiation.

Acceptance: `FakeFileBackend` performs no file I/O of its own; a notification created with a
file-path attachment round-trips through the injected manager; a backend without
`inject_attachment_manager` still works; `grep -rn "_download_from_url" vintasend/services/notification_service.py`
returns nothing.

### Phase 3 — File records, checksum dedup, and references

**Goal**: identical uploads store one blob, and a caller can attach an already-uploaded file by id.

**Feature flag**: none — new capability on a seam introduced in the same release.

Changes:

1. Persist flow in [fake_backend.py](../vintasend/services/notification_backends/stubs/fake_backend.py)
   (reference implementation for downstream authors): for an upload, `manager.file_to_bytes` →
   `manager.calculate_checksum` → `find_attachment_file_by_checksum` → on hit reuse the existing
   `AttachmentFileRecord` and skip the upload entirely; on miss `manager.upload_file` then
   `store_attachment_file_record`. Either way, write the join row. For a reference, resolve
   `file_id` via `get_attachment_file_record`, raise a typed error if absent, and write only the join
   row.
2. `get_attachments(notification_id)` returns `StoredAttachment`s whose `file` handle comes from
   `manager.reconstruct_attachment_file(record.storage_identifiers)`.
3. `get_orphaned_attachment_files()` returns records with no join rows. Deletion stays caller-driven,
   two-step: `manager.delete_file_by_identifiers` then `backend.delete_attachment_file`.
4. [exceptions.py](../vintasend/exceptions.py): `AttachmentFileNotFoundError`,
   `AttachmentUploadError`.
5. Cancel and delete get **no** attachment hook, matching TS — cascade is the schema's job and
   reclamation is the orphan sweep's. Say so explicitly in the docstrings so it reads as a decision
   rather than an omission.

Spec use-case: no spec — ports TS's dedup and reference-by-id capability.

Tests:

- **Unit**: uploading identical bytes twice creates one `AttachmentFileRecord` and two join rows;
  differing bytes create two records; a reference to a missing `file_id` raises.
- **Integration**: two notifications sharing one file, then deleting one — the file record survives
  and does not appear in `get_orphaned_attachment_files`; deleting the second makes it appear.
- **Edge cases**: an empty file (0 bytes) checksums and dedups correctly; a reference and an upload of
  the same bytes on one notification.

**Suggested AI model**: Tier 3. Multi-step persist flow with branching, but confined to the fake
backend plus tests once Phase 2's seam exists.

Acceptance: attaching the same bytes to two notifications produces one `AttachmentFileRecord` and two
join rows, and `get_orphaned_attachment_files` returns it only after both notifications are gone.

### Phase 3b — `vintasend-django` implementation (parallel track, separate repo)

**Goal**: `vintasend-django` implements the new seam, gets a working attachment path for the first
time, and stops reaching into concrete file classes from its adapter. Runs alongside Phases 2-3;
**cannot merge until core has released**, since it must pin the new version.

**Feature flag**: none.

Changes:

1. `vintasend_django/models.py:50-66`: add `AttachmentFile`; convert `Attachment` to a join table per
   **Data Model Changes**. One schema migration (`0004_*`).
2. New `DjangoStorageAttachmentManager` over `django.core.files.storage.storages["default"]`, keyed
   by `storage_identifiers["name"]`. Preserve `upload_to="notifications/attachments/"` so existing
   `FieldFile.name` values stay resolvable. Honour `expires_in` in `url()` where the storage backend
   supports signed URLs; document local storage as non-expiring.
3. `django_db_notification_backend.py`: rewrite `_store_attachments` (`:165-212`) against the real
   dataclass — **it currently dispatches on `file_path` / `file_bytes` / `file_obj`, attributes
   `NotificationAttachment` does not have, so any genuine attachment raises `ValueError`**. Rewrite
   `_serialize_attachment` (`:136-163`) to read the stored checksum instead of recomputing sha256 on
   every read. Fix `serialize_user_notification` (`:89-110`), which omits `attachments` entirely, so
   attachments are invisible on regular notifications. Implement the seven new methods.
4. `django_email.py:86-109`: drop the `hasattr(django_file, "attachment")` reach-through into the
   backend's concrete file class; use `prepare_attachments` with `StoredAttachment.filename`,
   `.content_type` and `.file.read()`. Stop swallowing every exception into a warning.
5. `test_django_db_notification_backend.py:702-790`: replace the `Mock(spec=NotificationAttachment)`
   fixtures with real dataclasses. **These mocks are why the bug survived** — they hand-set
   `.file_path`, certifying a code path real input cannot reach.

Spec use-case: no spec — downstream adoption plus a live defect fix.

Tests:

- **Unit**: `DjangoStorageAttachmentManager` round trip against `InMemoryStorage`; `url()` behaviour
  on a storage that signs and one that does not.
- **Integration**: create a notification with a real `NotificationAttachment` — the test that fails
  today; dedup across two notifications; attachments present on `serialize_user_notification`.
- **Regression**: the Django email adapter attaches a file without touching any backend-specific
  class.

**Suggested AI model**: Tier 4. Schema change, a new manager, a broken method rewritten, and a test
suite whose mocks were hiding the bug.

**Review models**: reviewer Tier 4 — a schema migration plus a defect whose existing tests passed
while the code was broken. The reviewer's job includes confirming the new tests would fail against
the old implementation.

Acceptance: `create_notification(..., attachments=[NotificationAttachment(file="/tmp/x.pdf", filename="x.pdf")])`
succeeds against the Django backend, the file is retrievable through the manager, and the new tests
fail if `_store_attachments` is reverted.

### Phase 4 — Documentation

**Goal**: implementers can write an attachment manager without reading the source.

**Feature flag**: none.

Changes:

1. New `@ATTACHMENTS.md`, modelled on the TS one but **written from the Python code** — the TS
   `ATTACHMENTS.md` is stale against its own implementation (it documents `deleteFile`, an async
   `reconstructAttachmentFile`, and an async `calculateChecksum`, none of which match the shipped
   v0.5.0 interface). Do not port it verbatim.
2. [README.md](../README.md) `:71-145`, `:213`, `:260-310`: the manager seam, injection, references,
   dedup, and the AsyncIO note.
3. [RELEASE_NOTES.md](../RELEASE_NOTES.md): minor entry with `### Backwards compatibility` naming all
   seven new abstract methods and both classes they land on.

Spec use-case: no spec — release documentation.

Tests: none beyond a green suite. Verify every code sample runs.

**Suggested AI model**: Tier 2.

**Reusable skills**: `Skill(release-package)`; `Skill(deslop-comments)` over the new prose.

Acceptance: `ATTACHMENTS.md` documents the three abstract methods with signatures matching the
shipped code, and `RELEASE_NOTES.md` names every added abstract method.

## 5. Risk & Rollout Notes

- **Seven new abstract methods on both backend ABCs breaks every downstream backend at
  instantiation.** This is the Step 0 compatibility choice, and `AGENTS.md` allows it as a minor with
  a mandatory note — but the sequencing is strict: core releases first, then `vintasend-django`
  widens its pin and implements. `vintasend-sqlalchemy` **cannot adopt this release** until its
  separate catch-up plan lands, since it is already missing `persist_one_off_notification`,
  `mark_sent_as_read_bulk` and the in-app filter methods from 1.2.0.
- **No data migration, deliberately.** `_store_attachments` has never accepted a real
  `NotificationAttachment` — it dispatches on attributes the dataclass does not have. Anyone who
  believes they have Django attachment data almost certainly does not. Say this plainly in the
  release notes: *"attachments on Django notifications never worked with the documented
  `NotificationAttachment` API"*. If a deployment does turn out to hold rows, the remedy is a
  one-off script in that application, not a migration shipped to everyone.
- **The schema migration is additive**: a new table plus new columns and FKs on an existing one. No
  rewrite of a hot table, no lock of consequence at the sizes this table reaches. `PROTECT` on the
  file FK is the deliberate choice — deleting a file still referenced by a notification should fail
  loudly.
- **`requests` stays a runtime dependency**, now reached from the manager base rather than the
  service module. The dependency count does not change.
- **Rollback**: Phase 1 is freely revertible. Phase 2 is not revertible after downstream packages
  ship against the new ABC. Phase 3b's migration is reversible (drop the table, restore the column)
  only before any real attachment is written.
- **Coordinate the major.** This plan is a minor on its own, but the
  [background-send plan](2026-07-22-BACKGROUND_SEND_QUEUE_SERVICE_IMPLEMENTATION_PLAN.md) forces a
  2.0. Landing all three ports in one 2.0 means downstream absorbs one breaking wave instead of
  three. If they ship separately, this one goes first — it has the smallest downstream surface.
- **Interaction with background sending**: once the queue payload is id-only, the worker reads
  attachments from the backend through the manager, which is what makes background sends with
  attachments possible at all. `vintasend-celery`'s `PlaceholderAttachmentFile` becomes dead code.

## 6. Open Questions

| Question | Recommended default |
|---|---|
| Should `find_attachment_file_by_checksum` also compare file size? | **Yes, add size to the lookup.** sha256 collisions are not a practical concern, but a size check is free, and it turns a silent wrong-file-served into a miss. Cheap insurance on a dedup path. |
| Should dedup be opt-out per manager? | **No, not in v1.** Dedup happens in the backend's persist flow, not the manager, so a manager cannot meaningfully opt out. If a host needs per-notification isolated copies (retention or compliance), that is a real requirement — capture it when someone asks, rather than designing a knob for a hypothetical. |
| Does `UpdateNotificationKwargs.attachments` accept inputs or stored attachments? | **Inputs** (`AnyNotificationAttachment`). It currently types as `StoredAttachment`, which is incoherent — a caller updating a notification has files to upload, not already-stored records. Fix it in Phase 2 and note it. |
| Should the orphan sweep ship as a periodic task? | **No.** `vintasend/tasks/periodic_tasks.py` exists, so it is tempting, but retention policy is the host's decision and an automatic sweep deleting user files by default is the wrong default. Ship the query, document the pattern. |
| Is `is_inline` meaningful on a reference? | **Yes, keep it on both.** Inline-ness is a property of how *this notification* uses the file, not of the file itself — the same blob could be an inline logo in one email and a normal attachment in another. It belongs on the join row. |

## 7. Touch List

**Phase 1**

- `@vintasend/services/attachment_managers/__init__.py`, `base.py`, `asyncio_base.py` — new.
- `@vintasend/services/attachment_managers/stubs/__init__.py`, `fake_attachment_manager.py` — new.
- `@vintasend/tests/test_services/test_attachment_managers.py` — new.
- [dataclasses.py](../vintasend/services/dataclasses.py) — `:11-19`, `:46-72`, `:75-105`, plus new types.
- [helpers.py](../vintasend/services/helpers.py), [app_settings.py](../vintasend/app_settings.py).

**Phase 2**

- [notification_backends/base.py](../vintasend/services/notification_backends/base.py) — `:58-90`, after `:216`.
- [notification_backends/asyncio_base.py](../vintasend/services/notification_backends/asyncio_base.py) — `:55-89`, end of class.
- [notification_service.py](../vintasend/services/notification_service.py) — `:99-140`, `:180-232`, `:289-392`, `:767-804`, `:824-882`, `:941-1044`.
- [notification_adapters/base.py](../vintasend/services/notification_adapters/base.py) + asyncio/async twins.
- [fake_backend.py](../vintasend/services/notification_backends/stubs/fake_backend.py) — `:281-349`, `:591-660`.
- [test_notification_attachments.py](../vintasend/tests/test_services/test_notification_attachments.py) — `:154-298`, `:430-563`, `:759-884`, `:909-1345`.

**Phase 3**

- [fake_backend.py](../vintasend/services/notification_backends/stubs/fake_backend.py) — persist flow, both classes.
- [exceptions.py](../vintasend/exceptions.py) — two new exceptions.
- [test_notification_attachments.py](../vintasend/tests/test_services/test_notification_attachments.py) — dedup and reference tests.

**Phase 3b (cross-repo — `vintasend-django`, own PR, after core releases)**

- `vintasend_django/models.py:50-66`; new `migrations/0004_*.py`.
- New `vintasend_django/services/attachment_managers/django_storage.py`.
- `vintasend_django/services/attachment_file.py:1-48`.
- `vintasend_django/services/notification_backends/django_db_notification_backend.py` — `:89-110`, `:136-163`, `:165-212`, `:214-291`.
- `vintasend_django/services/notification_adapters/django_email.py:86-109`.
- `vintasend_django/services/tests/test_django_db_notification_backend.py:702-790`.
- `pyproject.toml` — widen the `vintasend` pin.

**Phase 4**

- `@ATTACHMENTS.md` — new.
- [README.md](../README.md), [RELEASE_NOTES.md](../RELEASE_NOTES.md), [pyproject.toml](../pyproject.toml).
