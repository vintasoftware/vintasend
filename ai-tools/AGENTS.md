# VintaSend

VintaSend is a Python library for transactional notifications. It records every notification in a
data store, renders it from a template at send time, and dispatches it through a pluggable adapter
(email, SMS, push, in-app). The library itself is deliberately incomplete: it defines three abstract
seams and ships fakes for them, but every real backend, adapter, and template renderer lives in a
separate `vintasend-*` package. This repository owns the interfaces, the orchestration, and the
contract — not the integrations.

It is published to PyPI as `vintasend` and consumed by external Python applications, so the public
API and its type signatures are the product.

## Project Overview

Single package, no monorepo tooling.

- **`vintasend/services/notification_service.py`** — the entry point. `NotificationService` and
  `AsyncIONotificationService`, plus the `@register_context` decorator and the `Contexts` registry.
- **`vintasend/services/notification_backends/`** — the storage seam. Where notifications are
  persisted and queried.
- **`vintasend/services/notification_adapters/`** — the delivery seam. How a notification actually
  gets sent.
- **`vintasend/services/notification_template_renderers/`** — the rendering seam. How a notification
  body is produced from a template plus context.
- **`vintasend/tasks/`** — `background_tasks.py` and `periodic_tasks.py`, the hooks a host app wires
  into Celery, cron, or similar to drain pending notifications.
- **`vintasend/app_settings.py`** — settings resolution across Django, Flask, FastAPI, and bare env
  vars.
- **`vintasend/tests/`** — the whole suite, run against the in-repo fakes.

**There is no database, no web server, and no frontend in this repo.** Persistence is entirely the
backend implementation's problem. The test suite runs against `FakeFileBackend`, which serializes to
a JSON file. Nothing here opens a socket except `requests`, used to fetch URL-sourced attachments.

## Tech Stack

- **Python 3.10–3.14** — the floor is 3.10 and it is load-bearing. See **Supporting the minimum
  Python** below.
- **Poetry** — dependency management and publishing.
- **Runtime dependencies are intentionally tiny**: `typing-extensions`, `packaging`, `requests`.
  Adding a fourth is a real decision, not a routine one.
- **pytest** (`^9`) with `pytest-xdist` (`--dist=loadscope`), `pytest-asyncio`, `pytest-cov`, and
  `freezegun` for time control.
- **tox** — runs the suite against all five supported interpreters.
- **ruff** — lint and format, single source of truth for style.
- **mypy** — type checking. The package ships `py.typed`, so its annotations are part of the public
  contract.
- **pre-commit** — ruff, mypy, and hygiene hooks.
- **GitHub Actions** — `ci.yml` (lint + type-check job, then the 3.10–3.14 test matrix),
  `publish.yml` (tag-triggered PyPI release).

## Common Commands

```bash
poetry install --with dev          # install, including dev tooling

poetry run pytest                  # full suite (fast — no I/O, no DB)
poetry run pytest vintasend/tests/test_services/test_notification_service.py   # scoped
poetry run tox                     # the suite across Python 3.10–3.14

poetry run ruff check .            # lint
poetry run ruff format .           # format
poetry run mypy                    # type-check (config selects the package)

poetry run pre-commit run --all-files

poetry build                       # build sdist + wheel
```

## Code Style

Enforced by ruff — do not hand-format, run `poetry run ruff format .`:

- Line length 100, 4-space indent, double quotes.
- Two blank lines after the import block (`lines-after-imports = 2`).
- isort ordering with a dedicated `django` section between stdlib and third-party.

Conventions ruff does not enforce:

- **Type everything.** The package ships `py.typed`. A new public method without annotations is
  incomplete.
- **Guard type-only imports.** Anything imported purely for annotations goes inside
  `if TYPE_CHECKING:` with string annotations at the use site. This keeps the import graph flat and
  avoids cycles between the service and the seams.
- **Import `NotificationContextDict` and the other dataclasses from
  `vintasend.services.dataclasses`**, which is where they are defined — not from
  `notification_service`, which merely re-exports them. `no_implicit_reexport` is on and will reject
  the indirect path.
- **Exceptions derive from `NotificationError`** in `vintasend/exceptions.py`, which itself derives
  from `ValueError`. Raise the most specific existing subclass; add a new one rather than raising a
  bare `ValueError`.
- **Singletons use `SingletonMeta`** from `vintasend/utils/singleton_utils.py`. `NotificationSettings`
  and `Contexts` are the two instances. Do not invent a second singleton mechanism.

### Supporting the minimum Python

The floor is Python 3.10 and several settings encode it. Do not raise any of them to match whatever
interpreter you happen to be running:

- `[tool.ruff] target-version = "py310"` — set higher, pyupgrade rewrites code into 3.11/3.12-only
  syntax that breaks the tox matrix.
- `[tool.mypy] python_version = "3.10"` — set higher, errors that only appear on the oldest
  interpreter get hidden.
- For symbols added to `typing` after 3.10, branch on `sys.version_info` rather than
  `try/except ImportError`. mypy evaluates the former statically; with the latter, the annotations
  silently degrade. `notification_service.py` does this for `Unpack`.

## Architecture

### The three seams

Everything in this library is organized around three abstract base classes. A host application
supplies one concrete implementation of each.

| Seam | Base class | Responsibility |
|---|---|---|
| Backend | `notification_backends/base.py` `BaseNotificationBackend` | Persist, query, and update notifications |
| Adapter | `notification_adapters/base.py` `BaseNotificationAdapter` | Deliver a rendered notification |
| Renderer | `notification_template_renderers/base.py` `BaseNotificationTemplateRenderer` | Turn a template plus context into a sendable body |

Adapters are generic over the other two — `BaseNotificationAdapter(Generic[B, T], ABC)`, where `B` is
the backend type and `T` the renderer type — and accept either live instances or dotted import
strings, which is what the `@overload`-ed `__init__` is for.

**Changing a seam is a breaking change.** Every `vintasend-*` package implements these classes, and
`@abstractmethod` is enforced at instantiation: a downstream class missing a method raises
`TypeError` the moment it is constructed. So adding an abstract method breaks every existing
implementation at import time in their test suites.

Rules for touching the seams:

- Adding an `@abstractmethod` ships as a **minor** release accompanied by a mandatory
  `### Backwards compatibility` section in `RELEASE_NOTES.md` naming every added method and both
  classes it landed on. That is this project's established practice — version 1.2.0 added three
  abstract methods to `BaseNotificationBackend` and `AsyncIOBaseNotificationBackend` exactly this
  way. Strict semver would call it major; the project has chosen otherwise, so follow the precedent
  and make the release note carry the warning. Flag it explicitly in the PR body — never slip one in
  as part of a feature. Renaming, reordering, or removing a seam method *is* a major bump.
- Prefer adding a method with a working default implementation (not abstract), so existing
  implementations keep functioning. Reserve abstract for things no sensible default exists for.
- Abstract method bodies are `...`, never `raise NotImplementedError`. The decorator already enforces
  implementation at construction time; the raise is dead code reachable only via an explicit
  `super()` call. Both `base.py` and `asyncio_base.py` follow this.
- Widening a signature (new optional keyword argument) is safe. Renaming or reordering parameters is
  not — implementations override by name.

### Sync and AsyncIO parity

Almost everything exists twice: `NotificationService` / `AsyncIONotificationService`,
`BaseNotificationBackend` / `AsyncIOBaseNotificationBackend`, `BaseNotificationAdapter` /
`AsyncIOBaseNotificationAdapter`, and `FakeFileBackend` / `FakeAsyncIOFileBackend`.

**Every behavior change lands in both halves, in the same commit, with tests for both.** A fix
applied only to the sync path is an incomplete change. The test suite mirrors this:
`NotificationServiceTestCase` (`unittest.TestCase`) and `AsyncIONotificationServiceTestCase`
(`IsolatedAsyncioTestCase`) in `vintasend/tests/test_services/test_notification_service.py`.

`AsyncBaseNotificationAdapter` in `notification_adapters/async_base.py` is a third, distinct thing,
and the naming is a trap. It is **not** `async`/`await` — it is a *sync* adapter that hands delivery
to a task queue (see `vintasend-celery`). It composes `AsyncNotificationProtocol`, whose entire job
is serializing the adapter's parts (backend kwargs, config, adapter kwargs, renderer kwargs) into a
task payload and restoring them inside the worker, plus a `delayed_send` that takes a plain
`NotificationDict`. Genuine `async`/`await` is `AsyncIOBaseNotificationAdapter` in
`asyncio_base.py`. Check which one you are editing.

### The context registry

Notification bodies are rendered at **send** time, not creation time, so scheduled notifications pick
up current data. A notification stores a `context_name` string; the callable behind that name is
looked up when the notification is sent.

```python
@register_context("my_context_generator")
def my_context_generator(user_id: str) -> NotificationContextDict:
    ...
```

Registration mutates the `Contexts` singleton, so a generator must be imported before the
notification is sent or lookup fails with `NotificationContextGenerationError`. Both sync and async
generators are supported; the service inspects the callable with `inspect.iscoroutinefunction`.

### Settings resolution

`app_settings.py` detects the host framework at runtime by attempting to import Django, Flask, and
FastAPI in that order, then reads settings from that framework's config object with an environment
variable taking precedence. FastAPI has no global config, so callers pass a `config` object into the
service constructor.

None of the three frameworks is a dependency of this package. They are probed, never required — keep
those imports local to their functions and never import a framework at module scope.

### Stubs are a deliverable

`stubs/` directories under each seam hold `FakeFileBackend`, `FakeEmailAdapter`,
`FakeInAppAdapter`, `FakeTemplateRenderer`, and their AsyncIO twins. These serve two audiences: the
test suite (they are the only backend the tests run against) and downstream authors, who read them as
the reference implementation.

**Keep them complete.** A new seam method gets a working fake in the same commit — not a stub that
raises. `fake_backend.py` running to ~1,000 lines, second only to `notification_service.py`, is
expected rather than a smell.

### Testing

- Tests live in `vintasend/tests/`, mirroring the package layout, and are discovered by the
  `test_*.py` pattern.
- The style is `unittest.TestCase` / `IsolatedAsyncioTestCase` classes, run under pytest. Follow the
  surrounding style rather than introducing bare pytest functions or fixtures into existing files.
- No mocking of the seams — tests exercise the real fakes end to end. `unittest.mock.patch` is used
  sparingly, for things like forcing an adapter failure.
- `freezegun`'s `freeze_time` controls scheduling behavior. Use it rather than sleeping.
- Tests run under `xdist` with `--dist=loadscope`, so a test must not depend on another test's
  leftover state.
- The suite runs in well under a second. Keep it that way — no network, no real filesystem churn
  beyond the fake backend's temp JSON.

## Tenant model

n/a — single-tenant. There is no tenancy concept in this library. `user_id` identifies an end user in
the *host application's* system and is opaque here (`int | str | uuid.UUID`). It is not a security
boundary this code enforces.

One thing that is a scoping concern: the in-app notification methods accept an optional `user_id`,
and `mark_read_bulk` silently skips ids not owned by that user. Any code path exposed to an HTTP
endpoint must pass `user_id` so one user cannot mark another's notifications as read.

## Environment Variables

There is no `.env` file and no `.env.example`. The package reads configuration through
`app_settings.py`, which checks environment variables before falling back to framework settings.

Recognized by `NotificationSettings`:

```
NOTIFICATION_ADAPTERS
NOTIFICATION_BACKEND
NOTIFICATION_MODEL
NOTIFICATION_DEFAULT_BCC_EMAILS
NOTIFICATION_DEFAULT_BASE_URL_PROTOCOL
NOTIFICATION_DEFAULT_BASE_URL_DOMAIN
NOTIFICATION_DEFAULT_FROM_EMAIL
```

These are read by the *host application's* deployment, not set in this repo.

## Dependency licenses

**Enforcement:** `block` — refuse the install and surface the conflict; only proceed if the user
explicitly approves an override.

**Forbidden SPDX licenses:**

- `GPL-2.0-only`
- `GPL-3.0-only`
- `AGPL-3.0-only`
- `SSPL-1.0`

**Pre-install check.** Before running `poetry add` (or `pip install`, `uv add`) for any new
dependency, look up its declared license — PyPI metadata, or the project's `LICENSE` file — and
compare against the list above. If it matches and is not an approved override, stop and surface the
conflict.

**Unknown or undeclared license.** If the lookup returns nothing, an empty value, `UNKNOWN`,
`SEE LICENSE IN <file>`, or an unstructured `LICENSE` with no SPDX identifier, **stop and ask**
regardless of enforcement mode. Do not assume MIT — an unlicensed package is all-rights-reserved by
default.

**Notes.** vintasend is MIT and is consumed as a library, so a viral-copyleft runtime dependency
would propagate its terms to every downstream application. The runtime dependency set is deliberately
tiny — prefer not adding a dependency at all over finding a permissively licensed one. Dev-only
dependencies are not distributed, but still route additions past a human rather than self-approving.

## Deployment

This is a library. There is no environment to deploy to; "release" means publishing to PyPI.

| Target | Trigger | Workflow |
|---|---|---|
| PyPI | push of a `v*` tag | `.github/workflows/publish.yml` |

The publish workflow re-runs the full 3.10–3.14 matrix, then builds, runs `twine check`, and
publishes. Release steps:

1. Bump `version` in `pyproject.toml`.
2. Add an entry to `RELEASE_NOTES.md`.
3. Merge to `main`.
4. Tag `vX.Y.Z` and push the tag.

Version numbers follow the seam-compatibility rules above: a new `@abstractmethod` on any base class
is a **minor** bump carrying a mandatory `### Backwards compatibility` note; renaming, reordering, or
removing a seam method is a major bump. See the [release-package](skills/release-package/SKILL.md)
skill for the full table and the release checklist.

## Downstream implementations

These live in separate repositories under the same organization and implement the seams:

| Package | Provides |
|---|---|
| `vintasend-django` | backend (Django ORM), email adapter, template renderer |
| `vintasend-sqlalchemy` | backend (SQLAlchemy, sync and async) |
| `vintasend-celery` | adapter factory for deferred sending on sync backends |
| `vintasend-fastapi-mail` | AsyncIO email adapter |
| `vintasend-flask-mail` | sync email adapter |
| `vintasend-jinja` | Jinja2 template renderer |

**Never edit them from this repo** — they are separate checkouts with their own release cycles. When
a change affects them:

1. Work out which packages are affected. Backend seam changes hit `vintasend-django` and
   `vintasend-sqlalchemy`; adapter changes hit the four adapter packages; renderer changes hit
   `vintasend-django` and `vintasend-jinja`.
2. State the impact in the PR body — which packages, and whether the change is source-compatible.
3. If implementers must change code (a new `@abstractmethod`, or any rename / removal), each
   affected package needs a matching release that widens its `vintasend` constraint. Land this
   repo's release first; downstream cannot pin a version that does not exist yet. The bump level
   follows the table in [release-package](skills/release-package/SKILL.md).
4. Update the reference implementation paths in `app_settings.py`'s framework default dicts if a
   downstream module was renamed or moved.

`MIGRATION_TO_1.0.0.md` is the worked example of how a breaking release is documented.

## Pull requests and commits

- **Branches:** `feat/<kebab-case-description>`. Never commit directly to `main`.
- **Commits:** short imperative subject, capitalized, no trailing period — `Add bulk read marking`,
  `Fix adapter deserialization`. Conventional-commit prefixes appear occasionally but are not the
  norm; match the surrounding history.
- **No AI co-author trailers.** Commits are attributed to the human author only.
- **Stage explicit paths.** Never `git add -A` or `git add .` — `.claude/`, `package.json`, and
  `package-lock.json` are untracked and not gitignored, and would be swept into a commit.
- Agents may open PRs. Include what changed, which downstream packages are affected, and whether the
  change is breaking.
- Every PR must pass `poetry run ruff check .`, `poetry run mypy`, and `poetry run pytest`. CI also
  runs the full tox matrix — a change that only works on new Python will fail there, not locally.

## Key documentation

- **`README.md`** — public usage guide: getting started, attachments, one-off notifications, in-app
  listing and read-marking, the glossary, and the list of official implementations. Read this before
  changing any public API, and update it in the same PR.
- **`MIGRATION_TO_1.0.0.md`** — the 1.0 breaking-change migration guide.
- **`RELEASE_NOTES.md`** — per-version changelog. Update on every release.
- **`.github/PUBLISHING.md`** — publishing mechanics.
- **`CODE_OF_CONDUCT.md`**
