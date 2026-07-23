# vintasend-implementation-template

A starting point for a new `vintasend-*` implementation package: one `TODO` stub per
implementable seam (backend, adapter, template renderer, queue service, attachment manager),
plus a matching scaffold test for each, so a fresh clone installs, type-checks, and passes its
test suite before you write a single line of real logic.

This package is not published and does nothing useful on its own — every stub raises
`NotImplementedError("TODO: ...")`. Replace each `TODO` with a real implementation, delete the
stubs you don't need, and rename the package to match your integration.

For the full clone-and-rename workflow, see the workflow doc added alongside the clone script in
a later phase of `ai-plans/2026-07-23-IMPLEMENTATION_TEMPLATE_PACKAGE_IMPLEMENTATION_PLAN.md`.
Until that lands, treat this directory as source to copy manually:

```bash
cp -r templates/vintasend-implementation-template /path/to/vintasend-your-integration
cd /path/to/vintasend-your-integration
# rename the package dir, update pyproject.toml's [project].name, then:
poetry install
poetry run pytest
poetry run mypy
```

## What's here

| File | Seam | Base class(es) |
|---|---|---|
| `vintasend_implementation_template/backend.py` | Storage | `BaseNotificationBackend`, `AsyncIOBaseNotificationBackend` |
| `vintasend_implementation_template/adapter.py` | Delivery | `BaseNotificationAdapter`, `AsyncIOBaseNotificationAdapter`, `BackgroundNotificationAdapter`, `AsyncIOBackgroundNotificationAdapter` |
| `vintasend_implementation_template/template_renderer.py` | Rendering | `BaseNotificationTemplateRenderer`, `BaseTemplatedEmailRenderer`, `BaseTemplatedSMSRenderer` |
| `vintasend_implementation_template/queue_service.py` | Background send | `BaseNotificationQueueService`, `AsyncIOBaseNotificationQueueService` |
| `vintasend_implementation_template/attachment_manager.py` | Attachment storage | `BaseAttachmentManager`, `AsyncIOBaseAttachmentManager` |

Each module has a matching `tests/test_*.py` asserting the stub is importable, subclasses the
right ABC, and has no leftover abstract methods.
