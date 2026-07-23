# Tracking — Attachment Manager Seam

- **Plan**: `ai-plans/2026-07-22-ATTACHMENT_MANAGER_SEAM_IMPLEMENTATION_PLAN.md`
- **Started**: 2026-07-22
- **Last updated**: 2026-07-22

## Run options

- `pause_between_phases`: false
- `generate_inline_comments`: false
- `full_test_suite`: false
- `use_worktree`: true (added mid-run — see note)
- `worktree_path`: `.claude/worktrees/plan-attachment-manager-seam`
- `worktree_branch`: `plan/attachment-manager-seam/phase-2` (holds the stacked phase work)
- `sandbox_tier`: none
- `commit_strategy_resolved`: stacked-branches

### Note on worktree

The run began in the main checkout (`use_worktree: false` in config). During Phase 1 it emerged that
another agent session is actively working in the main checkout with uncommitted `tenant` /
`sent_at` / `read_at` changes on the same files Phases 2–4 touch. The user confirmed that work is
another session's and must be left alone, and chose to move to an isolated worktree. Phase 1's
commit was carried into the worktree; Phases 2–4 run there. The main checkout is never written to.

## Completed phases

### Phase 1 — Attachment manager seam and types ✅

- **Status**: DONE — reviewed (3 layers) + integrated.
- **Implementer model**: claude-sonnet-5 (Tier 3).
- **Reviewer model**: claude-opus-4-8 (Tier 4). **Fixer model**: claude-sonnet-5 (Tier 3).
- **Branch**: `plan/attachment-manager-seam/phase-1` (remote) at `11644c5`. **Base**: `main`.
- **PR**: https://github.com/vintasoftware/vintasend/pull/10
- **Summary**: Added the fourth seam. `BaseAttachmentManager` / `AsyncIOBaseAttachmentManager`
  ABCs with three abstract methods (`upload_file`, `reconstruct_attachment_file`,
  `delete_file_by_identifiers`) and three concrete helpers (`detect_content_type`,
  `calculate_checksum`, `file_to_bytes`). `reconstruct_attachment_file` is synchronous on both ABCs.
  `read_file_data` / `is_url` / `download_from_url` moved off `service_utils.py` into `base.py`;
  `asyncio_base.py` imports them (no duplication). New dataclasses: `StorageIdentifiers`,
  `AttachmentFileRecord`, `NotificationAttachmentReference`, `AnyNotificationAttachment`,
  `is_attachment_reference`. `StoredAttachment` gains `file_id` + `storage_identifiers`, with
  `storage_metadata` kept as a real backwards-compatible field (reconciled in `__post_init__`, not a
  property — a review fix). New `NOTIFICATION_ATTACHMENT_MANAGER` setting and `get_attachment_manager`
  / `get_asyncio_attachment_manager` helpers that return `None` when unset (including bare-env, where
  the setting resolves to `{}`). New `UnsupportedAttachmentFileTypeError`. `FakeAttachmentManager` /
  `FakeAsyncIOAttachmentManager` stubs.
- **Review findings fixed**: 1 BLOCKER (`storage_metadata` property broke the documented constructor
  + `asdict` contract — made a real reconciled field), 2 SHOULD-FIX (`get_attachment_manager`
  crashed under bare-env `{}` → guard on falsy; file-IO duplicated across both base modules →
  consolidated into `base.py`), 3 NITs (annotation narrowed, Unknown-framework + async fake tests
  added).
- **Gate**: ruff clean, mypy clean (47 files), pytest 222 passed / 2 skipped.

## Current phase

Phase 2 — Wire the manager into the service and backend seam (starting).

## Remaining phases

- Phase 2 — Wire the manager into the service and backend seam.
- Phase 3 — File records, checksum dedup, and references.
- Phase 4 — Documentation.

## Deferred phases

- **Phase 3b — vintasend-django implementation**: cross-repo (separate `vintasend-django` repo).
  Cannot merge until core releases and it can pin the new version. Not executed by this run; open a
  follow-up in that repo after release.
