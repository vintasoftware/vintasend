# Tracking — Shared Service Helpers

- **Feature name**: SHARED_SERVICE_HELPERS
- **Plan**: [2026-07-22-SHARED_SERVICE_HELPERS_IMPLEMENTATION_PLAN.md](2026-07-22-SHARED_SERVICE_HELPERS_IMPLEMENTATION_PLAN.md)
- **Started**: 2026-07-22
- **Last updated**: 2026-07-22
- **Feature flag**: none — pure refactor, per the plan's **Guiding Decisions**.
- **plan_branch**: `plan/shared-service-helpers`
- **PR**: https://github.com/vintasoftware/vintasend/pull/5

## Run options

| Option | Value |
|---|---|
| `commit_strategy_resolved` | `modular-commits` |
| `pause_between_phases` | `false` |
| `generate_inline_comments` | `true` |
| `full_test_suite` | `true` |
| `use_worktree` | `false` |
| `WORKROOT` | `/Users/hugobessa/Workspaces/vintasend` (main checkout) |
| `BASE_BRANCH` | `main` |
| `sandbox_tier` | `none` |

Models: implementer Tier 2 → `claude-sonnet-5` (stepped up from `claude-haiku-4-5`, phase touches
>3 files); reviewer Tier 4 → `claude-opus-4-8`; fixer Tier 3 → `claude-sonnet-5`. The integrate step
ran inline in the conductor rather than as an `agent_models.integrate` Tier 1 delegate, because the
PR body and inline comments needed review findings only the conductor held.

## Completed phases

### Phase 1 — Extract duplicated service helpers ✅

- **Status**: merged-ready, PR open, review clean (Layers 1–3).
- **Model**: `claude-sonnet-5` (plan suggested Tier 2).
- **Commits**: `002aacf` Add shared service_utils module for attachment/context helpers;
  `eaa1755` Delegate service attachment/context helpers to service_utils.
- **PR-context**: `.vinta-ai-workflows/prs-context/shared-service-helpers/plan.md` — `published`.

Summary:

All six pure helpers that `NotificationService` and `AsyncIONotificationService` each carried their
own copy of now live once in `vintasend/services/service_utils.py` as free functions:
`read_file_data`, `is_url`, `download_from_url`, `validate_attachments`,
`is_asyncio_context_function`, `is_sync_context_function`. The twelve methods became one-line
delegations; no public signature changed. The module-scope `import requests` in
`notification_service.py` is gone — `download_from_url` adopted the asyncio copy's lazy import with
the friendly `ImportError`, which was the only drift between the two copies. `validate_attachments`
keeps its no-op body, an explicit **Non-goal**. New `test_service_utils.py` covers each function
directly; a subprocess regression test asserts `requests` stays out of `sys.modules`.

Gate: ruff clean, mypy clean across 40 source files, 164 tests pass, import check exits 0.

Review findings fixed in-phase (reviewer found no BLOCKERs):

1. `read_file_data` was annotated `FileAttachment`, which includes `bytes` — but the function raises
   `ValueError` for `bytes`. Replaced with a module-private `ReadableFileAttachment` alias and
   dropped "or raw bytes" from all three docstring copies. Mattered because the package ships
   `py.typed`, so the annotation is public contract.
2. The sync side gained the friendly `ImportError` behaviour but had no method-level test for it,
   while the asyncio side did — a parity gap of exactly the kind this plan exists to close. Added
   `test_download_from_url_requests_import_error` to `TestNotificationServiceUrlHandling`.
3. A comment claimed `NotificationAttachment.is_url()` validates URLs; it only checks five scheme
   prefixes. Replaced with a plain true statement.

Two things carried forward for later plans (both in the PR body):

- **`test_notification_attachments.py` could not stay literally unmodified**, as the plan's Tests
  section hoped. Eight tests patched `vintasend.services.notification_service.requests...`, which
  `unittest.mock` resolves by attribute lookup on the module object — unresolvable once the
  module-scope import is dropped. They now patch `requests.get` directly. Every assertion is
  unchanged; the reviewer audited all eight against the pre-change file.
- **Subclass overrides are no longer honoured inside `read_file_data`.** The old body called
  `self._is_url` / `self._download_from_url`; the shared function calls the module-level ones. No
  subclass exists in this repo or the `implementations/` submodules and `_read_file_data` has no
  production caller, so exposure is nil — but the plan's "zero-risk refactor" claim in **Guiding
  Decisions** is slightly overstated. The attachment-manager plan, which relocates these onto
  `BaseAttachmentManager`, should carry this forward.

Also noted, out of scope here: `_is_url` exists verbatim in `FakeFileBackend` and
`FakeAsyncIOFileBackend` too, so it now has four copies rather than two. Collapsing those belongs
with the attachment-manager work.

## Current phase

Phase 2 — Close sync/asyncio parity gaps.

## Remaining phases

- Phase 2 — Close sync/asyncio parity gaps. Also carries the `pyproject.toml` version bump and the
  `RELEASE_NOTES.md` patch entry that the plan's **Touch List** files under "Both phases" — the
  release-notes entry must mention the `requests` import relocation.

## Deferred phases

None. No cross-repo phase, no flag-removal phase — this plan declares no feature flag.
