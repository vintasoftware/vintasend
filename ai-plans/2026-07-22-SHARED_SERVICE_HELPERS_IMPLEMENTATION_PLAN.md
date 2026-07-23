# Shared Service Helpers — Implementation Plan

Pure refactor. Lands before the three architecture ports
([background send](2026-07-22-BACKGROUND_SEND_QUEUE_SERVICE_IMPLEMENTATION_PLAN.md),
[attachment manager](2026-07-22-ATTACHMENT_MANAGER_SEAM_IMPLEMENTATION_PLAN.md),
[filtering](2026-07-22-NOTIFICATION_FILTERING_API_IMPLEMENTATION_PLAN.md)) so each of those writes shared
logic once instead of twice.

## 1. Goals

1. Extract every pure helper currently duplicated between `NotificationService` and
   `AsyncIONotificationService` into a single shared module, so the two service classes stop carrying
   character-for-character copies of the same code.
2. Reconcile the drift that has already accumulated between the two copies, picking the better
   behaviour in each case and applying it to both.
3. Leave the public API of both service classes byte-identical — no signature changes, no new
   settings, no seam changes.

Non-goals:

- **No behaviour change to `_validate_attachments`.** It is currently a `for` loop whose body is
  `pass` ([notification_service.py:180-195](../vintasend/services/notification_service.py#L180-L195)).
  Making it validate anything is a feature; it belongs to the attachment-manager plan, which moves
  this method onto the manager seam anyway.
- **No deletion of the dead helpers.** `_read_file_data` / `_is_url` / `_download_from_url` are
  called from nowhere but the test suite today. They are deleted (or rather, relocated onto
  `BaseAttachmentManager`) by the attachment plan. Deleting them here would strand ~440 lines of
  existing tests with no replacement in the same PR.
- **No merging of the sync and asyncio service classes.** The parallel-hierarchy design is
  deliberate and load-bearing per `AGENTS.md`. This plan removes duplicated *helpers*, not the
  duplicated *classes*.
- **No touching the three parallel adapter hierarchies** (`base` / `asyncio_base` / `async_base`).

## 2. Guiding Decisions

| Decision | Resolution |
|---|---|
| **Module functions, not a mixin** | The extracted helpers are pure functions of their arguments — none of them read `self`. A module of free functions is directly unit-testable without constructing a service, and avoids adding a class to the MRO of two already-generic service classes (`NotificationService(Generic[B, T])`). A mixin would also make the sync/asyncio split ambiguous for readers. |
| **New module, not `services/helpers.py`** | [helpers.py](../vintasend/services/helpers.py) already exists and imports the backend and adapter bases to build them from import strings. `notification_service.py` imports `helpers`; adding service-internal helpers there is fine directionally, but it mixes two unrelated concerns (dependency construction vs. file/context utilities) in one module. A dedicated `service_utils.py` keeps each module's job legible. |
| **Private-by-convention, not underscore-prefixed module** | Named `vintasend/services/service_utils.py` rather than `_service_utils.py`, matching the existing unprefixed `utils.py` and `helpers.py`. The functions themselves keep their leading underscore where they are re-exposed as methods, so nothing in the public surface changes. |
| **Keep the methods as thin delegating wrappers** | `NotificationService._read_file_data` stays as a one-line call into the shared function rather than being deleted. The existing test suite calls these as methods (~440 lines in `test_notification_attachments.py`), and downstream subclasses may override them. Delegation keeps this a zero-risk refactor. |
| **Lazy `requests` import wins the drift** | The two copies of `_download_from_url` differ: the sync one relies on a module-scope `import requests` ([notification_service.py:10](../vintasend/services/notification_service.py#L10)), the asyncio one imports lazily with a friendly `ImportError`. The lazy form is strictly better — it stops `requests` being imported merely by importing the service module, which matters for a library whose runtime dependency set is deliberately tiny. |
| **No feature flag** | This repo has no feature-flag module, and a library has no runtime to flip a flag in. This plan is a pure refactor with no reachable behaviour change, which is one of the two cases `plan-feature` names as a legitimate flag skip. The compatibility boundary is the release, not a flag. |
| **Patch release, not minor** | No public API changes, no seam changes, no new settings. The one observable difference is that `import vintasend.services.notification_service` no longer transitively imports `requests`. |

## 3. Data Model Changes

None. No dataclass, TypedDict, or seam signature is touched.

### 3.1 Type plumbing

The extracted functions carry the annotations they already have at their current sites. One
tightening: `_read_file_data(self, file)` is currently annotated only on its return type
([notification_service.py:197](../vintasend/services/notification_service.py#L197)). The extracted
function annotates its parameter as `FileAttachment` (from
[dataclasses.py](../vintasend/services/dataclasses.py)), since that is what every caller passes.
`mypy` runs with `python_version = "3.10"`, so the `if TYPE_CHECKING:` guard plus string annotations
convention applies.

## 4. Phased Rollout

### Phase 1 — Extract duplicated service helpers

**Goal**: both service classes call one shared implementation of each helper instead of maintaining
private copies. No caller-visible change. Ship value: none on its own — this is the scaffolding that
keeps the three following plans from writing every helper twice, and it is worth its own PR because
a pure move is reviewable by diff inspection in a way that a move-plus-feature is not.

**Feature flag**: none — pure refactor with no reachable behaviour change, per **Guiding Decisions**.

Changes:

1. New `@vintasend/services/service_utils.py`. Move, verbatim except for dropping the `self`
   parameter:
   - `read_file_data(file) -> bytes` — from
     [notification_service.py:197-222](../vintasend/services/notification_service.py#L197-L222)
     (sync) and `:841-866` (asyncio).
   - `is_url(file_str) -> bool` — from
     [notification_service.py:224-226](../vintasend/services/notification_service.py#L224-L226) and
     `:868-870`.
   - `download_from_url(url) -> bytes` — from
     [notification_service.py:228-232](../vintasend/services/notification_service.py#L228-L232) and
     `:872-882`. Adopt the asyncio copy's lazy `import requests` with the friendly `ImportError`.
   - `validate_attachments(attachments) -> list[NotificationAttachment]` — from
     [notification_service.py:180-195](../vintasend/services/notification_service.py#L180-L195) and
     `:824-839`. Behaviour unchanged, including the `pass` body.
   - `is_asyncio_context_function(func) -> bool` and `is_sync_context_function(func) -> bool` — the
     `inspect.iscoroutinefunction` wrappers duplicated in both classes.
2. [notification_service.py](../vintasend/services/notification_service.py): the six methods on
   `NotificationService` become one-line delegations to the shared functions. Same for the six on
   `AsyncIONotificationService`.
3. [notification_service.py:10](../vintasend/services/notification_service.py#L10): remove the
   module-scope `import requests`. It is now reached only from `service_utils.download_from_url`.
4. Run `Skill(deslop-comments)` over the moved docstrings — several are AI-slop shaped ("In the
   future, this can include validation logic like:") and this is the one PR where rewriting them
   costs nothing.

Spec use-case: no spec — shared scaffolding extracted ahead of the three architecture ports.

Tests:

- **Unit**: `@vintasend/tests/test_services/test_service_utils.py` — new. Direct coverage of each
  extracted function: path / `Path` / file-like / bytes inputs to `read_file_data`, every URL scheme
  in `is_url`, the `ValueError` on unsupported types, and `download_from_url` raising a friendly
  `ImportError` when `requests` is absent (patch `sys.modules`).
- **Integration**: [test_notification_attachments.py](../vintasend/tests/test_services/test_notification_attachments.py)
  lines 909-1345 — the existing ~440 lines that exercise these as service *methods* must keep passing
  unmodified. That is the proof the delegation is faithful; do not rewrite them in this phase.
- **Regression**: assert `requests` is not in `sys.modules` after a fresh
  `import vintasend.services.notification_service` in a subprocess.

**Suggested AI model**: Tier 2 (IDs in [resources/ai-models.yaml](../.claude/skills/plan-feature/resources/ai-models.yaml)).
Mechanical extraction across two classes plus a new test module — more than boilerplate because the
sync and asyncio copies must be confirmed identical before collapsing, but it follows exact precedent.

Acceptance: `vintasend/services/service_utils.py` holds one implementation of each of the six
helpers, both service classes delegate to it, the existing attachment test suite passes unmodified,
and `python -c "import vintasend.services.notification_service, sys; assert 'requests' not in sys.modules"` exits 0.

### Phase 2 — Close sync/asyncio parity gaps

**Goal**: the asyncio service stops silently lacking behaviour its sync twin has, so the three
following plans start from two halves that actually mirror each other.

**Feature flag**: none — the changes are additive on the asyncio side, and `AGENTS.md` already
mandates parity, so this is bringing existing code into line with a stated invariant.

Changes:

1. [notification_service.py](../vintasend/services/notification_service.py) `AsyncIONotificationService.send_pending_notifications`
   (`:1185-1202`): add the sent/failed counters and the logging its sync twin has at
   [:518-549](../vintasend/services/notification_service.py#L518-L549). Currently the asyncio copy
   drops them.
2. Audit the two classes method-by-method and record every remaining asymmetry in the PR body. Fix
   the ones that are omissions; leave the ones that are deliberate (`delayed_send` is absent from the
   asyncio service by design today — the background-send plan adds it, not this one) and say so
   explicitly.
3. Where the audit finds a docstring on one side and not the other, copy it across.

Spec use-case: no spec — enforces the sync/AsyncIO parity invariant named in `AGENTS.md`.

Tests:

- **Unit**: [test_notification_service.py](../vintasend/tests/test_services/test_notification_service.py)
  — add the asyncio counterpart of every existing `send_pending_notifications` counter assertion to
  `AsyncIONotificationServiceTestCase`, mirroring the sync `NotificationServiceTestCase` cases.
- **Integration**: a parity test that reflects over both classes and asserts the public method-name
  sets differ only by an explicit, named allowlist. This is the test that stops the next drift.

**Suggested AI model**: Tier 2. Small diff, but the audit needs care and the parity test needs
judgement about what belongs on the allowlist.

Acceptance: `AsyncIONotificationService.send_pending_notifications` returns the same counter shape as
its sync twin, and the public-method-set parity test passes with an allowlist that names every
intentional difference.

## 5. Risk & Rollout Notes

- **No feature flag, no backfill, no migration.** No database, no persisted state, nothing to roll
  forward or back beyond the release itself.
- **Rollback**: revert the PR. Neither phase changes a seam, a setting, or a stored format, so a
  revert is unconditionally safe at any point.
- **The `requests` import change is the only observable difference.** A host application that relied
  on `vintasend` importing `requests` as a side effect (rather than depending on it directly) would
  see an `ImportError` move from import time to call time. `requests` remains a declared runtime
  dependency in `pyproject.toml`, so this affects nobody who installs the package normally. Mention
  it in the release notes anyway.
- **Sequencing**: merge both phases before starting any of the three architecture ports. If only
  Phase 1 lands, that is still a net win and the ports can proceed; Phase 2 is independently
  valuable and independently revertible.
- **Downstream packages**: none affected. No seam method, constructor signature, or exported name
  changes. `vintasend-django`, `vintasend-sqlalchemy`, `vintasend-celery`, `vintasend-jinja`,
  `vintasend-flask-mail` and `vintasend-fastapi-mail` need no release.

## 6. Open Questions

| Question | Recommended default |
|---|---|
| Should `service_utils.py` be re-exported from `vintasend/__init__.py`? | **No.** `vintasend/__init__.py` is currently empty and the codebase uses deep imports throughout. Introducing a package-root export surface is a separate decision that the background-send plan raises properly (TS has `src/index.ts`; Python has nothing equivalent). Don't smuggle it in here. |
| Should the parity test in Phase 2 also cover the backend and adapter ABC pairs? | **Not in this plan.** The same drift risk exists for `BaseNotificationBackend` / `AsyncIOBaseNotificationBackend`, but those are public seams and a parity test over them will start failing the moment any of the three ports adds a method to one side first. Revisit once the ports have landed. |
| Is `_validate_attachments`' no-op body a bug or a placeholder? | **Placeholder — leave it.** The attachment plan moves it onto the manager seam and gives it a real body there. Changing it here would be an unflagged behaviour change in a refactor PR. |

## 7. Touch List

**Phase 1**

- `@vintasend/services/service_utils.py` — new.
- `@vintasend/tests/test_services/test_service_utils.py` — new.
- [notification_service.py](../vintasend/services/notification_service.py) — line 10 (drop
  `import requests`); lines 180-232 (sync helpers → delegation); lines 824-882 (asyncio helpers →
  delegation); the `_is_asyncio_context_function` / `_is_sync_context_function` pair on both classes.
- [test_notification_attachments.py](../vintasend/tests/test_services/test_notification_attachments.py)
  — unchanged; it is the regression proof for this phase.

**Phase 2**

- [notification_service.py](../vintasend/services/notification_service.py) — lines 1185-1202
  (`AsyncIONotificationService.send_pending_notifications`), plus whatever the parity audit surfaces.
- [test_notification_service.py](../vintasend/tests/test_services/test_notification_service.py) —
  asyncio counter assertions; new public-method-set parity test.

**Both phases**

- [RELEASE_NOTES.md](../RELEASE_NOTES.md) — patch entry. No `### Backwards compatibility` section
  needed; no seam changes. Note the `requests` import relocation.
- [pyproject.toml](../pyproject.toml) — version bump.
