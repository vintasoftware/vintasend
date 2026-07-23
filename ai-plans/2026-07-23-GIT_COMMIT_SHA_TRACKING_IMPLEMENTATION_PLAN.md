# Git Commit SHA Tracking — Implementation Plan

Ports `vintasend-ts`'s git-commit-SHA tracking (its v0.7.0 feature): every notification records which
source-code revision rendered and sent it, resolved at send time through an injectable provider.
Baseline is `vintasend` 2.0.0, in which the queue seam, attachment manager and filtering API already
shipped.

## 1. Goals

1. Add an injectable `BaseGitCommitShaProvider` seam whose single method returns the current commit
   SHA, so a host declares once how its running revision is discovered.
2. Record a normalized 40-character SHA on every notification at **send** time (foreground and
   background), writing it only when it differs from what is already stored.
3. Make the field system-managed: readable everywhere, settable by nobody through the update path.

Non-goals:

- **No history table.** One SHA per notification — the revision that last sent it — not an
  append-only log of every render. TS stores a single `gitCommitSha`; match it.
- **No SHA on creation.** The value is resolved at send time so a scheduled notification records the
  revision that actually delivered it, not the one that enqueued it. Creating a notification never
  sets it.
- **No provider implementations in core.** Core ships the ABC and a fake, exactly as the other
  injected components (queue service, attachment manager) do. A real git-SHA provider is a host
  concern.
- **No `vintasend-sqlalchemy` work.** Downstream scope is core + `vintasend-django`.
- **No validation that the SHA corresponds to a real commit.** Format only.

## 2. Guiding Decisions

| Decision | Resolution |
|---|---|
| **Provider is an injected ABC, not one of the three seams** | It follows the queue-service / attachment-manager pattern already in 2.0: a host-supplied component accepted as an instance or an import string, defaulted from a `NOTIFICATION_*` setting. Making it a fourth *seam* would overstate it — it has one method and no bearing on persistence, delivery or rendering. An ABC (rather than a `Protocol`) keeps it consistent with `BaseNotificationQueueService` and `BaseAttachmentManager`, both ABCs. |
| **Provider absent ⇒ feature off** | No provider configured means no SHA resolved and nothing written — every existing notification flow is byte-for-byte unchanged. This is what keeps the change additive despite touching the send path. |
| **Resolve at send time, in both `send` and `delayed_send`** | TS calls `resolveAndPersistGitCommitShaForExecution` at the top of both `send()` and `delayedSend()`. Python mirrors it so foreground and worker paths agree, and the worker records the *worker's* revision — which, under 2.0's shared-settings constraint, is the deploying revision. |
| **Write only on change** | The provider is called on every send, but `store_git_commit_sha` runs only when the resolved value differs from the stored one. Avoids a needless write per send for the common case where a notification is sent once. |
| **Dedicated backend method, not `persist_notification_update`** | `persist_notification_update(notification_id, update_data: UpdateNotificationKwargs)` takes a typed payload and refuses already-sent rows. The SHA is system-managed and must never appear in `UpdateNotificationKwargs`. A dedicated `store_git_commit_sha(notification_id, sha)` — mirroring the existing `store_context_used` — is the clean seam addition and sidesteps the already-sent guard, since the SHA is written while the row is still pending. |
| **System-managed, enforced at runtime** | TS types the field `gitCommitSha?: never` on inputs so the compiler forbids it. Python has no `never`, so `update_notification` rejects a `git_commit_sha` key at runtime — checking the raw kwargs, since `UpdateNotificationKwargs` is not enforced at runtime. This mirrors exactly how `tenant` reassignment is already blocked. |
| **Normalize to 40-char lowercase hex, reject otherwise** | TS trims, lowercases and matches `^[a-f0-9]{40}$`, throwing on a mismatch. A provider returning a short SHA or a branch name is a configuration error and should fail loudly, not persist garbage. |
| **`store_git_commit_sha` is abstract** | Per the seam-compatibility rule in `AGENTS.md` and the established project choice, a new abstract method on `BaseNotificationBackend` / `AsyncIOBaseNotificationBackend` ships as a minor bump with a mandatory `### Backwards compatibility` note. `vintasend-django` implements it in lockstep; `vintasend-sqlalchemy` is already non-conformant and adopts it with its catch-up plan. |
| **No feature flag** | No flag module in this library, and the provider-absent-⇒-off behaviour is the moral equivalent of a flag defaulting off. The compatibility boundary is the release plus its note. |

## 3. Data Model Changes

### 3.1 New provider seam

```python
# vintasend/services/git_commit_sha_providers/base.py
class BaseGitCommitShaProvider(ABC):
    @abstractmethod
    def get_current_git_commit_sha(self) -> str | None: ...


# vintasend/services/git_commit_sha_providers/asyncio_base.py
class AsyncIOBaseGitCommitShaProvider(ABC):
    @abstractmethod
    async def get_current_git_commit_sha(self) -> str | None: ...
```

A `None` return means "unknown this call" — skip the write, do not raise. Only a *non-null,
malformed* SHA raises.

### 3.2 New dataclass field

`git_commit_sha: str | None = None` on `Notification` and `OneOffNotification` in
[dataclasses.py](../vintasend/services/dataclasses.py) (`:217-238`, `:241-264`), appended after
`tenant` with a `None` default so existing positional construction is unaffected. **Not** added to
`UpdateNotificationKwargs` — the comment already at
[dataclasses.py:276-280](../vintasend/services/dataclasses.py#L276-L280) declining to widen that
TypedDict is the precedent.

### 3.3 New backend method

Abstract `store_git_commit_sha(notification_id, git_commit_sha: str)` on both backend ABCs, placed
next to `store_context_used` ([base.py:341](../vintasend/services/notification_backends/base.py#L341)).

### 3.4 New setting

`NOTIFICATION_GIT_COMMIT_SHA_PROVIDER`, a dotted import string, threaded through all six layers
`Skill(add-env-var)` names.

### 3.5 Django schema

`git_commit_sha` — `CharField(max_length=40, null=True, blank=True)` — on the `Notification` model,
in one additive migration. Not indexed by default (it is displayed and audited, rarely filtered); add
an index only if a later filtering need appears.

## 4. Phased Rollout

Two phases, bundled per the standing granularity choice: the seam and its wiring are one concern, the
Django implementation is a separate repo.

### Phase 1 — Provider seam, field, and send-path wiring

**Goal**: with a provider configured, sending a notification records the normalized current SHA on
its row; with none configured, nothing changes. Independently useful the moment a host writes a
provider.

**Feature flag**: none — additive; provider-absent means the send path is unchanged.

Changes:

1. New `@vintasend/services/git_commit_sha_providers/__init__.py`, `base.py`, `asyncio_base.py` per
   **Data Model Changes**. Import only ABC machinery — no import of `notification_service`, to keep
   the graph flat.
2. New `@vintasend/services/git_commit_sha_providers/stubs/fake_git_commit_sha_provider.py`:
   `FakeGitCommitShaProvider` returning a fixed valid SHA, and a variant returning `None`. Complete,
   never raising, per the stubs-are-a-deliverable rule.
3. [dataclasses.py](../vintasend/services/dataclasses.py): add `git_commit_sha` to both dataclasses.
4. [notification_backends/base.py](../vintasend/services/notification_backends/base.py) and
   [asyncio_base.py](../vintasend/services/notification_backends/asyncio_base.py): abstract
   `store_git_commit_sha`.
5. [fake_backend.py](../vintasend/services/notification_backends/stubs/fake_backend.py): implement it
   in both the sync and asyncio fakes.
6. New module-level `normalize_git_commit_sha(sha) -> str` in
   [service_utils.py](../vintasend/services/service_utils.py) — trim, lowercase, assert
   `^[a-f0-9]{40}$`, raise `InvalidGitCommitShaError` otherwise. One implementation, called by both
   services.
7. [notification_service.py](../vintasend/services/notification_service.py):
   `NotificationService.__init__` (`:168`) accepts
   `git_commit_sha_provider: BaseGitCommitShaProvider | str | None = None`, defaulting from the
   setting; `AsyncIONotificationService.__init__` (`:1157`) the same. New private
   `_resolve_and_persist_git_commit_sha(notification)` called at the top of `send` (`:316`) and
   `delayed_send` (`:1032`), and their asyncio twins (`:1313`, `:2055`): call the provider, skip on
   `None`, normalize, and `store_git_commit_sha` only when it differs from `notification.git_commit_sha`.
8. `update_notification` (`:575`, `:1580`): reject a `git_commit_sha` key in the raw kwargs, raising
   `GitCommitShaReassignmentError` — same shape as the existing `tenant` guard.
9. [exceptions.py](../vintasend/exceptions.py): `InvalidGitCommitShaError`,
   `GitCommitShaReassignmentError`.
10. [app_settings.py](../vintasend/app_settings.py): the new setting across six layers.
11. [helpers.py](../vintasend/services/helpers.py): `get_git_commit_sha_provider` and its asyncio twin.

Spec use-case: no spec — ports `BaseGitCommitShaProvider` and `resolveAndPersistGitCommitShaForExecution`
from `vintasend-ts` v0.7.0.

Tests:

- **Unit**: `@vintasend/tests/test_services/test_git_commit_sha.py` — new. `normalize_git_commit_sha`
  over a clean SHA, an uppercase one, a padded one, a short one (raises), a branch name (raises);
  provider returning `None` skips the write; a valid SHA is stored once; a second send with the same
  SHA does not re-store; a changed SHA re-stores.
- **Integration**: sending with a configured provider records the SHA and the notification round-trips
  with it; sending with **no** provider leaves `git_commit_sha` `None` and touches no store method
  (assert `store_git_commit_sha` is never called). Both service classes, and the `delayed_send`
  worker path. Use `freeze_time` where scheduling is involved.
- **Guard**: `update_notification(id, git_commit_sha="…")` raises `GitCommitShaReassignmentError` and
  changes nothing, including via `**kwargs` past the TypedDict.

**Suggested AI model**: Tier 3 (IDs in [resources/ai-models.yaml](../.claude/skills/plan-feature/resources/ai-models.yaml)).
Touches both service classes, both backend ABCs and both fakes, with a normalize/resolve/persist flow
that must be identical on the foreground and worker paths.

**Reusable skills**: `Skill(add-env-var)` for `NOTIFICATION_GIT_COMMIT_SHA_PROVIDER`.

Acceptance: with `FakeGitCommitShaProvider` configured, `send()` stores a normalized 40-char SHA
exactly once; a malformed provider return raises `InvalidGitCommitShaError`; with no provider the
field stays `None` and `store_git_commit_sha` is never called; `update_notification` rejects the key.

### Phase 1b — `vintasend-django` implementation (parallel track, separate repo)

**Goal**: the Django backend persists and returns `git_commit_sha`. Runs alongside Phase 1;
**cannot merge until core has released**, since it pins the new version.

**Feature flag**: none.

Changes:

1. `vintasend_django/models.py`: add the `git_commit_sha` column; one additive migration.
2. `django_db_notification_backend.py`: implement `store_git_commit_sha`; include `git_commit_sha`
   in `serialize_user_notification` and `serialize_one_off_notification` so it round-trips.
3. Tests: `store_git_commit_sha` persists and re-reads; a fresh notification serializes with
   `git_commit_sha is None`.

Spec use-case: no spec — downstream adoption.

Tests:

- **Unit**: the new method round-trips a SHA.
- **Integration**: full send against the Django backend records the SHA; the serializers expose it.

**Suggested AI model**: Tier 2. Single-column migration plus one method mirroring `store_context_used`.

Acceptance: a notification sent against `DjangoDbNotificationBackend` with a provider configured has
its `git_commit_sha` persisted and returned by the read serializers.

## 5. Risk & Rollout Notes

- **One new abstract method breaks every downstream backend at instantiation** until it implements
  `store_git_commit_sha`. Minor bump with the mandatory `### Backwards compatibility` note; core
  releases first, then `vintasend-django`. `vintasend-sqlalchemy` is already non-conformant and takes
  this with its catch-up plan.
- **Migration is additive**: one nullable column, no rewrite, no lock of consequence. Existing rows
  get `NULL`, correctly meaning "sent before tracking existed".
- **No backfill.** A SHA cannot be reconstructed for a past send. `NULL` is the honest value.
- **Shared-settings interaction**: because the worker resolves the SHA at delivery, the recorded
  value is the worker's deploying revision. Under 2.0's worker/web shared-settings constraint that is
  the intended semantics; note it so a reader does not expect the enqueueing revision.
- **Rollback**: Phase 1 is revertible before any backend stores a SHA. Phase 1b's migration reverses
  by dropping one nullable column.
- **Provider errors**: a provider that raises (rather than returning `None`) will surface at send
  time. Decide in review whether the service swallows provider exceptions to `None` (safer — a broken
  provider never blocks a send) or lets them propagate under `raise_on_failed_send`. Recommended:
  swallow to `None` and log, since audit metadata should never stop a notification going out.

## 6. Open Questions

| Question | Recommended default |
|---|---|
| Should a provider exception block the send? | **No — swallow to `None` and log.** Audit metadata is not worth failing a delivery over. A provider that cannot determine the SHA is equivalent to one returning `None`. |
| Index `git_commit_sha` in Django? | **Not initially.** It is displayed and audited, not filtered. Add an index when a concrete "show me everything sent by revision X" query appears; an unused index is write-cost for nothing. |
| Should `filter_notifications` gain a `git_commit_sha` field? | **Not in this plan.** The filtering API is already shipped; adding a field to it is a filtering-plan follow-up, and doing it here couples two releases. Flag it as a natural next step. |
| Accept a 7-char short SHA? | **No.** TS requires the full 40-char form and so should Python — a short SHA is ambiguous across a large history and defeats the point of exact provenance. |

## 7. Touch List

**Phase 1**

- `@vintasend/services/git_commit_sha_providers/__init__.py`, `base.py`, `asyncio_base.py` — new.
- `@vintasend/services/git_commit_sha_providers/stubs/__init__.py`, `fake_git_commit_sha_provider.py` — new.
- `@vintasend/tests/test_services/test_git_commit_sha.py` — new.
- [dataclasses.py](../vintasend/services/dataclasses.py) — `:217-264`.
- [notification_backends/base.py](../vintasend/services/notification_backends/base.py) — near `:341`.
- [notification_backends/asyncio_base.py](../vintasend/services/notification_backends/asyncio_base.py) — matching position.
- [fake_backend.py](../vintasend/services/notification_backends/stubs/fake_backend.py) — both classes.
- [service_utils.py](../vintasend/services/service_utils.py) — `normalize_git_commit_sha`.
- [notification_service.py](../vintasend/services/notification_service.py) — `:168`, `:316`, `:575`, `:1032`, `:1157`, `:1313`, `:1580`, `:2055`.
- [exceptions.py](../vintasend/exceptions.py) — two new exceptions.
- [app_settings.py](../vintasend/app_settings.py), [helpers.py](../vintasend/services/helpers.py).
- [RELEASE_NOTES.md](../RELEASE_NOTES.md) — minor entry with `### Backwards compatibility`.
- [README.md](../README.md) — a "Git Commit SHA Tracking" section.
- [pyproject.toml](../pyproject.toml) — version bump.

**Phase 1b (cross-repo — `vintasend-django`, own PR, after core releases)**

- `vintasend_django/models.py`; new migration.
- `vintasend_django/services/notification_backends/django_db_notification_backend.py` — `store_git_commit_sha` and the two serializers.
- `vintasend_django/services/tests/test_django_db_notification_backend.py`.
- `pyproject.toml` — widen the `vintasend` pin.
