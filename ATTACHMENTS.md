# Attachments

VintaSend stores an attachment as two things: bytes somewhere (a file, an S3 object, whatever a
host application uses) and a row describing that blob. The **attachment manager** owns the bytes.
The **notification backend** owns the row. This split means a backend never opens a file, calls
S3, or downloads a URL — it only persists and queries data, and hands an opaque identifier back to
whichever manager the host application configured.

This document covers the attachment manager seam, the data model behind it, and how the two
sides talk to each other. It assumes you have already read the attachment section of
[README.md](README.md).

## The attachment manager seam

`BaseAttachmentManager` (`vintasend/services/attachment_managers/base.py`) and
`AsyncIOBaseAttachmentManager` (`asyncio_base.py`) declare three abstract methods:

```python
class BaseAttachmentManager(ABC):
    @abstractmethod
    def upload_file(
        self,
        file: FileAttachment,
        filename: str,
        content_type: str | None = None,
    ) -> AttachmentFileRecord: ...

    @abstractmethod
    def reconstruct_attachment_file(
        self, storage_identifiers: StorageIdentifiers
    ) -> AttachmentFile: ...

    @abstractmethod
    def delete_file_by_identifiers(self, storage_identifiers: StorageIdentifiers) -> None: ...
```

- **`upload_file`** stores `file` and returns an `AttachmentFileRecord` describing where it
  landed.
- **`reconstruct_attachment_file`** takes the `storage_identifiers` from an `AttachmentFileRecord`
  and builds an `AttachmentFile` handle to read it back later.
- **`delete_file_by_identifiers`** deletes the stored bytes for a given `storage_identifiers`.

**`reconstruct_attachment_file` is synchronous on both `BaseAttachmentManager` and
`AsyncIOBaseAttachmentManager`.** It only builds a handle from identifiers already in hand — no
file is opened, no network call is made — so there is nothing to `await`. `upload_file` and
`delete_file_by_identifiers` are `async def` on the AsyncIO manager; `reconstruct_attachment_file`
is the one method that stays the same shape on both.

The base class also ships three concrete helpers, usable by any manager or by a backend:

- **`detect_content_type(filename)`** guesses a MIME type from a filename, falling back to
  `application/octet-stream`.
- **`calculate_checksum(data)`** returns the sha256 hex digest of `data`. This is what backends use
  for the dedup lookup described below.
- **`file_to_bytes(file)`** reads a `FileAttachment` into memory. It accepts raw `bytes`, a local
  path (`str` or `Path`), a URL `str` (`http://`, `https://`, `s3://`, `gs://`, `azure://` are
  recognized as remote), or any file-like object with `read()`. URL input is downloaded with
  `requests`, VintaSend's only runtime HTTP dependency.

Core does not ship a real manager — only the ABC and `FakeAttachmentManager` /
`FakeAsyncIOAttachmentManager` in `attachment_managers/stubs/fake_attachment_manager.py`. A real
manager (local disk, S3, Django storage, and so on) is expected to live in its own
`vintasend-*` package, the same way real backends and adapters do.

## Injecting a manager

Both services take an `attachment_manager` constructor argument:

```python
NotificationService(
    notification_adapters=[...],
    notification_backend=my_backend,
    attachment_manager=MyAttachmentManager(),   # or a dotted import string, or omit it
)
```

If you omit it, the service falls back to the `NOTIFICATION_ATTACHMENT_MANAGER` setting
(resolved from environment variables or your framework's config, the same way
`NOTIFICATION_BACKEND` and the others are). If neither is set, `attachment_manager` stays `None`
and the backend runs with no attachment support.

Once a manager is resolved, the service calls `backend.inject_attachment_manager(manager)` — but
only if the backend accepts one. This check is duck-typed, not an `isinstance` check:

```python
def supports_attachments(backend: BaseNotificationBackend) -> bool:
    return hasattr(backend, "inject_attachment_manager")
```

`inject_attachment_manager` is a concrete method on `BaseNotificationBackend` and
`AsyncIOBaseNotificationBackend`, not abstract, so a backend written before this seam existed
still constructs and runs. It simply never receives a manager and never accepts attachments.

## Data model

An **`AttachmentFileRecord`** is a checksum-indexed, stored blob:

```python
@dataclass
class AttachmentFileRecord:
    id: str
    filename: str
    content_type: str
    size: int
    checksum: str  # sha256 hex
    created_at: datetime.datetime
    updated_at: datetime.datetime
    storage_identifiers: StorageIdentifiers
```

One `AttachmentFileRecord` can back many notifications. The join between a notification and a
file is a separate row, represented by **`StoredAttachment`**:

```python
@dataclass
class StoredAttachment:
    id: str | uuid.UUID          # the join row's own id
    filename: str
    content_type: str
    size: int
    checksum: str
    created_at: datetime.datetime
    file: AttachmentFile
    description: str | None = None
    is_inline: bool = False
    file_id: str = ""            # the AttachmentFileRecord this row points at
    storage_identifiers: StorageIdentifiers = field(default_factory=dict)
    storage_metadata: StorageIdentifiers = field(default_factory=dict)  # deprecated alias
```

`StoredAttachment.id` is the join row's own id; `StoredAttachment.file_id` is the
`AttachmentFileRecord` it points at. Deleting a `StoredAttachment` drops one reference; the file
record itself survives until nothing references it.

`storage_metadata` is the old name for `storage_identifiers`, kept for backwards compatibility.
The two are reconciled once when the object is built: pass either constructor argument and both
fields hold the same value afterwards, with `storage_identifiers` winning if you pass both.
Mutating one field after construction does not update the other, so read and write
`storage_identifiers` in new code.

**`StorageIdentifiers`** (`dict[str, Any]`) is opaque to the backend. It must carry a non-empty
`"id"` key; every other key is defined by whichever manager wrote it. A backend never parses this
dict — its only legal operation on it is handing it back to `reconstruct_attachment_file` or
`delete_file_by_identifiers` on the injected manager.

## Attaching by reference

Instead of uploading the same file twice, a caller can attach an already-stored file by id:

```python
@dataclass
class NotificationAttachmentReference:
    file_id: str
    description: str | None = None
    is_inline: bool = False
```

Both `NotificationAttachment` (an upload) and `NotificationAttachmentReference` (a reference to an
existing file) are accepted anywhere a caller attaches a file — `create_notification`,
`create_one_off_notification`, and the backend's `persist_notification` /
`persist_one_off_notification`. The type used in those signatures is the union:

```python
AnyNotificationAttachment = NotificationAttachment | NotificationAttachmentReference
```

`is_attachment_reference(attachment)` is a `TypeGuard` that tells the two apart:

```python
def is_attachment_reference(
    attachment: AnyNotificationAttachment,
) -> TypeGuard[NotificationAttachmentReference]:
    return isinstance(attachment, NotificationAttachmentReference)
```

A reference to a `file_id` that does not exist raises `AttachmentFileNotFoundError`
(`vintasend.exceptions`).

`UpdateNotificationKwargs.attachments` is the one exception: it stays typed
`list[StoredAttachment]`, not `AnyNotificationAttachment`. `persist_notification_update` has no
upload path — it just sets fields on an existing row — so a raw upload or reference passed there
would be stored as if it were already a persisted attachment, which is wrong.

## Checksum dedup

When a caller uploads a `NotificationAttachment`, the reference implementation
(`FakeFileBackend._store_attachments`) does this before ever calling `manager.upload_file`:

1. Read the file into memory once, with `manager.file_to_bytes`.
2. Compute its checksum with `manager.calculate_checksum`.
3. Look up an existing record with the same checksum **and size** via
   `backend.find_attachment_file_by_checksum(checksum, size)`.
4. On a hit, reuse the existing `AttachmentFileRecord` and skip the upload entirely.
5. On a miss, call `manager.upload_file` and persist the new record with
   `backend.store_attachment_file_record`.

Either way, a new join row is written for the notification. Two notifications that attach
identical bytes end up sharing one `AttachmentFileRecord` and each get their own join row. Size is
checked alongside the digest so a sha256 collision degrades to a fresh upload instead of silently
serving the wrong file — a cheap safeguard on a dedup path.

An attachment given as a `NotificationAttachmentReference` skips this whole flow: no bytes are
read and no new record is created, only a join row pointing at the existing `file_id`.

## The seven backend methods

`BaseNotificationBackend` and `AsyncIOBaseNotificationBackend` each declare seven `@abstractmethod`
attachment methods, plus the concrete `inject_attachment_manager` shown above:

| Method | Purpose |
|---|---|
| `store_attachment_file_record(record)` | Persist a new `AttachmentFileRecord` row. |
| `get_attachment_file_record(file_id)` | Look up a record by id, or `None`. |
| `find_attachment_file_by_checksum(checksum, size)` | The dedup lookup, or `None` on a miss. |
| `delete_attachment_file(file_id)` | Drop a record row. Deleting the underlying bytes is a separate, manager-driven step. |
| `get_orphaned_attachment_files()` | Records with no join row referencing them. |
| `get_attachments(notification_id)` | The `StoredAttachment`s for a notification, with each `file` handle rebuilt through `manager.reconstruct_attachment_file`. |
| `delete_notification_attachment(attachment_id)` | Delete one join row by its own id. |

On `AsyncIOBaseNotificationBackend` every write-shaped one of these (all but the two read-only
lookups) also accepts an optional `lock: asyncio.Lock | None = None`, matching the rest of that
ABC.

`FakeFileBackend` (and its AsyncIO twin `FakeAsyncIOFileBackend`) in
`notification_backends/stubs/fake_backend.py` is a full, working reference implementation of all
seven, along with the checksum-dedup persist flow above.

### Reclaiming orphaned files

Nothing in this seam deletes a file automatically. `cancel_notification` and
`delete_notification_attachment` have no attachment side effect — cascading a join row when a
notification is deleted is the backend schema's job, and deciding when to actually free unused
bytes is the host application's retention policy, not a rule this library should impose.

The pattern is: call `get_orphaned_attachment_files()` to find `AttachmentFileRecord`s no join
row references anymore, then reclaim each one in two steps —

```python
manager.delete_file_by_identifiers(record.storage_identifiers)  # delete the bytes
backend.delete_attachment_file(record.id)                        # drop the row
```

Add this to a periodic task in your own application if you want automatic cleanup. VintaSend
ships the query, not the sweep.

## `is_inline`

`is_inline` lives on `NotificationAttachment`, `NotificationAttachmentReference`, and
`StoredAttachment` — never on `AttachmentFileRecord`. It describes how *this* notification uses
the file, not a property of the file itself: the same stored image could be an inline logo on one
notification and a plain attachment on another.

## A runnable example

This uses only the fakes shipped in core (`FakeAttachmentManager`, `FakeFileBackend`), so it runs
with no other package installed:

```python
import os
import tempfile

from vintasend.constants import NotificationTypes
from vintasend.services.attachment_managers.stubs.fake_attachment_manager import (
    FakeAttachmentManager,
)
from vintasend.services.dataclasses import (
    NotificationAttachment,
    NotificationAttachmentReference,
    NotificationContextDict,
)
from vintasend.services.notification_adapters.stubs.fake_adapter import FakeEmailAdapter
from vintasend.services.notification_backends.stubs.fake_backend import FakeFileBackend
from vintasend.services.notification_service import NotificationService, register_context
from vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer import (
    FakeTemplateRenderer,
)

db_path = os.path.join(tempfile.mkdtemp(), "notifications.json")
backend = FakeFileBackend(database_file_name=db_path)
adapter = FakeEmailAdapter(backend=backend, template_renderer=FakeTemplateRenderer())

service = NotificationService(
    notification_adapters=[adapter],
    notification_backend=backend,
    attachment_manager=FakeAttachmentManager(),
)


@register_context("invoice_context")
def invoice_context():
    return NotificationContextDict({})


notification = service.create_notification(
    user_id="user-1",
    notification_type=NotificationTypes.EMAIL.value,
    title="Your invoice",
    body_template="Hello",
    context_name="invoice_context",
    context_kwargs=NotificationContextDict({}),
    subject_template="Invoice",
    preheader_template="Invoice",
    attachments=[
        NotificationAttachment(filename="invoice.pdf", file=b"%PDF-1.4 fake invoice bytes"),
    ],
)

stored = notification.attachments[0]
stored.get_file_data() == b"%PDF-1.4 fake invoice bytes"  # True

# Attach the same already-uploaded file to a second notification, by reference,
# instead of uploading it again.
second = service.create_notification(
    user_id="user-2",
    notification_type=NotificationTypes.EMAIL.value,
    title="Your invoice copy",
    body_template="Hello",
    context_name="invoice_context",
    context_kwargs=NotificationContextDict({}),
    subject_template="Invoice",
    preheader_template="Invoice",
    attachments=[
        NotificationAttachmentReference(file_id=stored.file_id, description="same invoice"),
    ],
)
second.attachments[0].file_id == stored.file_id  # True: same AttachmentFileRecord, new join row
```

## AsyncIO

Everything above has an AsyncIO twin: `AsyncIOBaseAttachmentManager` mirrors
`BaseAttachmentManager`, `AsyncIONotificationService` takes the same `attachment_manager`
argument, and `AsyncIOBaseNotificationBackend` declares the same seven attachment methods. Three
differences to keep in mind:

- On the manager, `upload_file` and `delete_file_by_identifiers` are `async def`.
  `reconstruct_attachment_file` stays a plain `def` on both — see above.
- On the backend, every write-shaped attachment method (`store_attachment_file_record`,
  `delete_attachment_file`, `delete_notification_attachment`) takes the same optional
  `lock: asyncio.Lock | None = None` the rest of `AsyncIOBaseNotificationBackend` takes. The
  read-only lookups (`get_attachment_file_record`, `find_attachment_file_by_checksum`,
  `get_orphaned_attachment_files`, `get_attachments`) do not.
- `file_to_bytes` on `AsyncIOBaseAttachmentManager` is a plain `def`, not a coroutine. Every read
  it performs — local disk, an in-memory buffer, or `requests` — is already blocking, and there is
  no async HTTP dependency in this library that would make an `await` here meaningful.

`FakeAsyncIOAttachmentManager` and `FakeAsyncIOFileBackend` are the reference implementations for
the AsyncIO half, in the same `stubs/` modules as their sync counterparts.
