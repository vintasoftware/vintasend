# Implementation Tracking — Background Send via Queue Service

- **Feature name**: background-send-queue-service
- **Plan**: `ai-plans/2026-07-22-BACKGROUND_SEND_QUEUE_SERVICE_IMPLEMENTATION_PLAN.md`
- **Started**: 2026-07-22
- **Last updated**: 2026-07-22
- **Feature flag**: none — the plan states the compatibility boundary is the 2.0 release plus its migration guide, not a runtime flag.

## Run options

| Option | Value |
|---|---|
| `pause_between_phases` | `false` (auto-flow) |
| `generate_inline_comments` | `false` (project default) |
| `full_test_suite` | `true` (full `poetry run pytest` on every outer gate) |
| `commit_strategy_resolved` | `stacked-branches` |
| `use_worktree` | `true` |
| `worktree_path` | `/Users/hugobessa/Workspaces/vintasend/.claude/worktrees/plan-background-send-queue-service` |
| `worktree_branch` | `plan-background-send-queue-service` |
| `worktree_summary` | `.vinta-ai-workflows/worktrees/plan-background-send-queue-service.yaml` |
| `sandbox_tier` | `enforced` (`sandbox-exec` present) |

`WORKROOT` = the worktree path above. `BASE_BRANCH` = `plan-background-send-queue-service`, cut from
`origin/main` @ `e0c93b7`. Every phase branch stacks on the previous one inside that worktree.

### Resolved models

| Role | Source | Tier | Model |
|---|---|---|---|
| Phase 1 implementer | plan | 2 | sonnet (phase touches >3 files) |
| Phase 2 implementer | plan | 4 | opus |
| Phase 3 implementer | plan | 3 | sonnet |
| Phase 4 implementer | plan | 2 | haiku |
| Reviewer | `agent_models.reviewer` | 4 | opus (Phase 2 also names reviewer tier 4) |
| Fixer | `agent_models.fixer` | 3 | sonnet |
| worktree_prep / integrate | `agent_models` | 1 | haiku |

## Environment notes

- The `ai-plans/*.md` plan files are untracked in this repo, so the plan was copied into the worktree
  by hand after provisioning. It is not committed by this run.
- `.claude/worktrees/` was added to `.git/info/exclude` (local-only, uncommitted) so the worktree
  cannot be swept into a commit from the main checkout.
- **Two other implement-plan sessions were active in the main checkout** when this run started,
  ping-ponging between `plan/attachment-manager-seam/phase-1` and
  `plan/notification-filtering-api/phase-1`. This run is isolated in its own worktree and is
  unaffected, but the three plans all target a shared 2.0 wave — see the plan's
  **Risk & Rollout Notes**.

## Completed phases

_None yet._

## Current phase

Phase 1 — Add the queue-service seam.

## Remaining phases

- Phase 2 — Rewire the sync send path to id-only.
- Phase 3 — Background sending on the asyncio service.
- Phase 4 — Documentation and 2.0 migration guide.

## Deferred phases

Cross-repo work, called out by the plan as out of scope and living in its own repo:

- `vintasend-celery` — `CeleryNotificationAdapter` becomes a `CeleryNotificationQueueService`; most of
  `celery_adapter_factory.py` (dict conversion, attachment serialization, `PlaceholderAttachmentFile`)
  becomes dead. Core must release first.
- `vintasend-sqlalchemy` — no required code change; its README should document the
  `NOTIFICATION_SERVICE_FACTORY` pattern as the supported way to give a worker a live session.

No flag-removal phase exists for this plan.
