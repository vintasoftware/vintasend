# vintasend-implementation-template

A starting point for a new `vintasend-*` implementation package: one `TODO` stub per
implementable seam (backend, adapter, template renderer, queue service, attachment manager),
plus a matching scaffold test for each, so a fresh clone installs, type-checks, and passes its
test suite before you write a single line of real logic.

This package is not published and does nothing useful on its own — every stub raises
`NotImplementedError("TODO: ...")`. Clone it, rename it, then replace each `TODO` with a real
implementation.

## What's here

| File | Seam | Base class(es) |
|---|---|---|
| `vintasend_implementation_template/backend.py` | Storage | `BaseNotificationBackend`, `AsyncIOBaseNotificationBackend` |
| `vintasend_implementation_template/adapter.py` | Delivery | `BaseNotificationAdapter`, `AsyncIOBaseNotificationAdapter`, `BackgroundNotificationAdapter`, `AsyncIOBackgroundNotificationAdapter` |
| `vintasend_implementation_template/template_renderer.py` | Rendering | `BaseNotificationTemplateRenderer`, `BaseTemplatedEmailRenderer`, `BaseTemplatedSMSRenderer` |
| `vintasend_implementation_template/queue_service.py` | Background send | `BaseNotificationQueueService`, `AsyncIOBaseNotificationQueueService` |
| `vintasend_implementation_template/attachment_manager.py` | Attachment storage | `BaseAttachmentManager`, `AsyncIOBaseAttachmentManager` |
| `vintasend_implementation_template/replication_queue_service.py` | Queued multi-backend replication (optional) | `BaseNotificationReplicationQueueService`, `AsyncIOBaseNotificationReplicationQueueService` |

Each module has a matching `tests/test_*.py` asserting the stub is importable, subclasses the
right ABC, and has no leftover abstract methods.

There is no `logger.py` stub here. `vintasend` does not have a logger seam yet — if one ships,
add a `logger.py` stub and matching test alongside that work.

## Workflow

1. **Clone.** Run the clone script against a target directory:

   ```bash
   python templates/vintasend-implementation-template/scripts/clone.py /path/to/vintasend-your-integration --package-name vintasend-your-integration
   ```

   This copies the skeleton, renames the distribution (`vintasend-implementation-template` →
   your kebab-case name) and the import package (`vintasend_implementation_template` → your
   snake_case name) everywhere they appear, and prints the next commands to run. See
   `scripts/clone.py`'s module docstring for exactly what it does and does not touch.

2. **Rename the classes.** The clone keeps every class named `ImplementationTemplate*` (for
   example `ImplementationTemplateBackend`). Rename each one to match your integration —
   `DjangoBackend`, `CeleryQueueService`, whatever fits. The clone script leaves this to you on
   purpose: you will usually pick a name more specific than a blind find-and-replace could
   guess.

3. **Implement each component**, in this order:

   1. Backend — every other seam reads and writes through it, so get it working first.
   2. Template renderer — needs only the backend's data shapes, no delivery mechanism yet.
   3. Adapter — delivery. Needs a working renderer to produce something to send.
   4. Attachment manager — only if your integration supports attachments.
   5. Queue service — only if delivery should happen in a background worker rather than the
      calling process.
   6. Replication queue service — only if a host using your backend wants queued multi-backend
      replication (`replication_mode="queued"`) rather than the default inline replication.

   Work through the checklist below one seam at a time. Each entry names the exact abstract
   methods and points at the fake in `vintasend`'s own `stubs/` package as a working reference.

4. **Test against the fakes.** Your new package's tests should exercise your real
   implementation the way `vintasend`'s own suite exercises its fakes — no mocking the seam
   itself. Run `poetry run pytest` after every component; the scaffold tests already in
   `tests/` keep passing throughout, since they only assert the shape of the stub, not its
   behavior.

5. **Publish.** Once every seam you need is implemented and tested, this is an ordinary Poetry
   package: `poetry build`, then publish it the way you publish any Python package. It depends
   on `vintasend` by version range already — you do not need to touch that pin.

## Per-component checklist

Each checklist below is parsed by `vintasend/tests/test_template_checklist.py` in the main
`vintasend` repo, which confirms every named method still exists on the current ABC. If a seam
changes, that test fails before this doc goes stale.

### Backend (storage seam)

Implement `vintasend_implementation_template/backend.py`. Reference:
`vintasend/services/notification_backends/stubs/fake_backend.py` (`FakeFileBackend` for the
sync class, `FakeAsyncIOFileBackend` for the AsyncIO class).

```checklist
BaseNotificationBackend.get_all_pending_notifications
BaseNotificationBackend.get_pending_notifications
BaseNotificationBackend.get_all_future_notifications
BaseNotificationBackend.get_future_notifications
BaseNotificationBackend.get_all_future_notifications_from_user
BaseNotificationBackend.get_future_notifications_from_user
BaseNotificationBackend.persist_notification
BaseNotificationBackend.persist_one_off_notification
BaseNotificationBackend.persist_notification_update
BaseNotificationBackend.mark_pending_as_sent
BaseNotificationBackend.mark_pending_as_failed
BaseNotificationBackend.mark_sent_as_read
BaseNotificationBackend.mark_sent_as_read_bulk
BaseNotificationBackend.cancel_notification
BaseNotificationBackend.get_notification
BaseNotificationBackend.filter_all_in_app_unread_notifications
BaseNotificationBackend.filter_in_app_unread_notifications
BaseNotificationBackend.filter_all_in_app_notifications
BaseNotificationBackend.filter_in_app_notifications
BaseNotificationBackend.filter_notifications
BaseNotificationBackend.get_user_email_from_notification
BaseNotificationBackend.store_context_used
BaseNotificationBackend.store_git_commit_sha
BaseNotificationBackend.store_attachment_file_record
BaseNotificationBackend.get_attachment_file_record
BaseNotificationBackend.find_attachment_file_by_checksum
BaseNotificationBackend.delete_attachment_file
BaseNotificationBackend.get_orphaned_attachment_files
BaseNotificationBackend.get_attachments
BaseNotificationBackend.delete_notification_attachment
AsyncIOBaseNotificationBackend.get_all_pending_notifications
AsyncIOBaseNotificationBackend.get_pending_notifications
AsyncIOBaseNotificationBackend.get_all_future_notifications
AsyncIOBaseNotificationBackend.get_future_notifications
AsyncIOBaseNotificationBackend.get_all_future_notifications_from_user
AsyncIOBaseNotificationBackend.get_future_notifications_from_user
AsyncIOBaseNotificationBackend.persist_notification
AsyncIOBaseNotificationBackend.persist_one_off_notification
AsyncIOBaseNotificationBackend.persist_notification_update
AsyncIOBaseNotificationBackend.mark_pending_as_sent
AsyncIOBaseNotificationBackend.mark_pending_as_failed
AsyncIOBaseNotificationBackend.mark_sent_as_read
AsyncIOBaseNotificationBackend.mark_sent_as_read_bulk
AsyncIOBaseNotificationBackend.cancel_notification
AsyncIOBaseNotificationBackend.get_notification
AsyncIOBaseNotificationBackend.filter_all_in_app_unread_notifications
AsyncIOBaseNotificationBackend.filter_in_app_unread_notifications
AsyncIOBaseNotificationBackend.filter_all_in_app_notifications
AsyncIOBaseNotificationBackend.filter_in_app_notifications
AsyncIOBaseNotificationBackend.filter_notifications
AsyncIOBaseNotificationBackend.get_user_email_from_notification
AsyncIOBaseNotificationBackend.store_context_used
AsyncIOBaseNotificationBackend.store_git_commit_sha
AsyncIOBaseNotificationBackend.store_attachment_file_record
AsyncIOBaseNotificationBackend.get_attachment_file_record
AsyncIOBaseNotificationBackend.find_attachment_file_by_checksum
AsyncIOBaseNotificationBackend.delete_attachment_file
AsyncIOBaseNotificationBackend.get_orphaned_attachment_files
AsyncIOBaseNotificationBackend.get_attachments
AsyncIOBaseNotificationBackend.delete_notification_attachment
```

Connected through the `NOTIFICATION_BACKEND` setting (a dotted import path to your class), read
by `vintasend.app_settings.NotificationSettings` and passed into `NotificationService` /
`AsyncIONotificationService`.

### Template renderer (rendering seam)

Implement `vintasend_implementation_template/template_renderer.py`. There is no AsyncIO twin
for this seam — `render` stays synchronous everywhere, and async adapters call it directly.
Reference: `vintasend/services/notification_template_renderers/stubs/fake_templated_email_renderer.py`
(`FakeTemplateRenderer`). There is no reference fake for SMS yet; follow the same shape as the
email renderer, or look at `vintasend-jinja`'s implementation.

```checklist
BaseNotificationTemplateRenderer.render
BaseTemplatedEmailRenderer.render
BaseTemplatedEmailRenderer.render_from_template_content
BaseTemplatedSMSRenderer.render
```

Connected by passing it into your adapter — a renderer has no setting of its own;
`vintasend`'s adapters accept one as a constructor argument (a live instance or a dotted import
string).

### Adapter (delivery seam)

Implement `vintasend_implementation_template/adapter.py`. Use the plain classes
(`BaseNotificationAdapter` / `AsyncIOBaseNotificationAdapter`) when delivery happens in the
calling process, and the `Background*` classes when delivery should happen in a worker instead
— see `vintasend/services/notification_adapters/async_base.py`'s docstring for the
`send`/`delayed_send` split. Reference:
`vintasend/services/notification_adapters/stubs/fake_adapter.py` (`FakeEmailAdapter`,
`FakeAsyncIOEmailAdapter`, `FakeAsyncEmailAdapter`, `FakeAsyncIOBackgroundEmailAdapter`) and
`vintasend/services/notification_adapters/stubs/fake_in_app_adapter.py` (`FakeInAppAdapter`,
`FakeAsyncIOInAppAdapter`) for an in-app delivery example.

```checklist
BaseNotificationAdapter.send
AsyncIOBaseNotificationAdapter.send
BackgroundNotificationAdapter.send
BackgroundNotificationAdapter.delayed_send
AsyncIOBackgroundNotificationAdapter.send
AsyncIOBackgroundNotificationAdapter.delayed_send
```

`BackgroundNotificationAdapter.send` and `AsyncIOBackgroundNotificationAdapter.send` are the
same `send` you already implement on the plain adapter above — a background adapter inherits
it, and only adds `delayed_send`.

Connected through the `NOTIFICATION_ADAPTERS` setting: a list of `(dotted_class_path,
notification_type)` pairs, read the same way as the backend.

### Queue service (background send)

Implement `vintasend_implementation_template/queue_service.py`. Only needed if you ship a
`Background*` adapter — the queue service is how a notification id gets from
`NotificationService.send()` to your worker; the worker then calls
`NotificationService.delayed_send(notification_id)`, which reloads the notification and calls
the adapter's `send`. Reference:
`vintasend/services/notification_queue_services/stubs/fake_queue_service.py`
(`FakeQueueService`, `FakeAsyncIOQueueService`).

```checklist
BaseNotificationQueueService.enqueue_notification
AsyncIOBaseNotificationQueueService.enqueue_notification
```

Connected through the `NOTIFICATION_QUEUE_SERVICE` setting (a dotted import path). Unset means
background sending is unsupported and `NotificationService` calls adapters directly instead.

### Replication queue service (optional multi-backend seam)

Implement `vintasend_implementation_template/replication_queue_service.py`. Only needed for
**queued** multi-backend replication (`replication_mode="queued"`) -- this is a separate,
optional seam from the backend and adapter above, and a host can use multiple backends with
plain inline replication (the default) without ever touching it. When configured, this queue
service is how a `(notification_id, backend_identifier)` pair gets from
`NotificationService`'s write path to your worker; the worker then calls
`NotificationService.process_replication(notification_id, backend_identifier)`, which converges
that replica to the primary's current snapshot. Reference:
`vintasend/services/notification_queue_services/stubs/fake_replication_queue_service.py`
(`FakeReplicationQueueService`, `FakeAsyncIOReplicationQueueService`).

```checklist
BaseNotificationReplicationQueueService.enqueue_replication
AsyncIOBaseNotificationReplicationQueueService.enqueue_replication
```

Connected through the `NOTIFICATION_REPLICATION_QUEUE_SERVICE` setting (a dotted import path).
Unset with `replication_mode="queued"` falls back to inline replication (with a warning logged)
rather than dropping replication silently.

### Attachment manager (attachment storage)

Implement `vintasend_implementation_template/attachment_manager.py`. Only needed if your
integration supports notification attachments. A backend never reads, writes, or downloads a
byte itself — it persists rows and hands `StorageIdentifiers` back to whichever manager was
injected. `reconstruct_attachment_file` stays a plain (non-`async`) method even on the AsyncIO
class, since it only builds a lazy handle and performs no I/O itself. Reference:
`vintasend/services/attachment_managers/stubs/fake_attachment_manager.py`
(`FakeAttachmentManager`, `FakeAsyncIOAttachmentManager`).

```checklist
BaseAttachmentManager.upload_file
BaseAttachmentManager.reconstruct_attachment_file
BaseAttachmentManager.delete_file_by_identifiers
AsyncIOBaseAttachmentManager.upload_file
AsyncIOBaseAttachmentManager.reconstruct_attachment_file
AsyncIOBaseAttachmentManager.delete_file_by_identifiers
```

Connected through the `NOTIFICATION_ATTACHMENT_MANAGER` setting (a dotted import path). Unset
means attachments are unsupported.

## Manual copy, without the clone script

If you would rather not run the script:

```bash
cp -r templates/vintasend-implementation-template /path/to/vintasend-your-integration
cd /path/to/vintasend-your-integration
# rename vintasend_implementation_template/ to your package's import name, update every
# import that references it, update pyproject.toml's [project].name and [tool.poetry].packages,
# then:
poetry install
poetry run pytest
poetry run mypy
```

The script does exactly this, minus the manual find-and-replace.
