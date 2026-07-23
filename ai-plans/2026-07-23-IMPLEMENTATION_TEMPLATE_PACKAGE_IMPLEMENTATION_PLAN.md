# Implementation Template Package — Implementation Plan

Ports `vintasend-ts`'s `vintasend-implementation-template` (its v0.10.0 contributor tooling): a
copyable package skeleton that a third-party author clones to build a new `vintasend-*` integration,
with a stub per seam, a matching test per stub, a per-component checklist, and a clone script.
Baseline is `vintasend` 2.0.0, so the template must cover all the injectable components that exist
now — the three seams plus the queue service and attachment manager.

## 1. Goals

1. Ship an in-repo `templates/vintasend-implementation-template/` skeleton: a complete, installable
   Python package that does nothing useful on its own but compiles, type-checks and passes an empty
   test suite out of the box.
2. Provide one stub file per implementable component, each with the abstract methods present and
   marked `TODO`, plus a matching test file pre-wired with scaffold assertions.
3. Provide a clone script that copies the skeleton to a new directory, renames the package, and
   leaves a working starting point.
4. Document the workflow: what to implement, in what order, and how each component is wired.

Non-goals:

- **No new runtime code in `vintasend` itself.** This is contributor tooling. The published
  `vintasend` wheel is unchanged; the template is a directory in the repo, not an installed
  dependency.
- **No new standalone repo or submodule.** Per the placement decision, the template lives in-repo so
  it stays in lockstep with the seams. Bootstrapping a *standalone* package is what the clone script
  does at use time.
- **No working integration.** The template deliberately ships stubs that raise or return placeholder
  values, exactly as TS's does. It is a starting point, not a reference implementation — the fakes
  under each seam's `stubs/` remain the reference.
- **No CI wiring into this repo's matrix.** The template has its own `pyproject.toml` and is excluded
  from `vintasend`'s lint/type/test scope, matching how `implementations/` is already excluded.

## 2. Guiding Decisions

| Decision | Resolution |
|---|---|
| **In-repo `templates/` directory** | The Python repo keeps *concrete* integrations as submodules under `implementations/`, but a template is not an integration — it is scaffolding that must track the seams exactly. Living in-repo means a seam change and its template update land in one PR, so the skeleton never drifts from the contract it teaches. TS ships it the same way, inside `src/implementations/`. |
| **Separate top-level `templates/`, not under `implementations/`** | `implementations/` is reserved for the six real submodule packages, and `AGENTS.md` describes it as exactly that. Putting a non-submodule skeleton there would blur "these are shipping packages" with "this is a starting point". A sibling `templates/` directory keeps the distinction crisp. |
| **Cover every 2.0 component, not just the original three seams** | The template must reflect the library as it is now: backend, adapter, template renderer, **plus** queue service and attachment manager (both added in 2.0), and a logger placeholder if the logger gap is later filled. A template that stops at the three original seams would teach an outdated shape and send new implementers looking for the queue/attachment contracts on their own. |
| **One stub + one test per component** | TS pairs every stub file with a scaffold test. The tests do double duty: they prove the skeleton is wired correctly before the author writes anything, and they show *how* to test each component against the fakes. This is the highest-leverage part of the template — an author who runs `pytest` and sees green knows their environment works. |
| **Stubs mark abstract methods `TODO`, do not silently pass** | Each stub subclasses the real ABC and lists every abstract method with a `raise NotImplementedError("TODO: …")` body and a docstring pointing at the contract. Instantiating an unfinished stub fails loudly — the author cannot accidentally ship an empty implementation that "works" because a method silently returned `None`. |
| **Clone script over a cookiecutter dependency** | A small, dependency-free `scripts/clone.py` (or shell) that copies the directory and string-replaces the package name keeps the tooling in the same spirit as the library's tiny-dependency ethos. A cookiecutter template would be more powerful and more machinery than a six-file skeleton warrants. TS shipped a plain clone script for the same reason. |
| **Pins `vintasend` by a version range in its own `pyproject.toml`** | The template's manifest depends on `vintasend ^2.0` (or the current line), so a cloned package starts with a correct, current pin. The clone script does not need to edit the dependency — only the package name. |
| **No feature flag, no release-note compatibility section** | Nothing in the shipped library changes. This is a docs-and-tooling addition; a normal minor or even a patch, with a plain release note, no `### Backwards compatibility` section because no seam moves. |

## 3. Data Model Changes

None. No runtime code, no dataclasses, no seams touched.

## 4. Phased Rollout

Two phases: the skeleton and its stubs, then the workflow docs and clone script. Small enough that
bundling is defensible, but the docs read better once the skeleton they describe exists.

### Phase 1 — Package skeleton, stubs, and scaffold tests

**Goal**: `templates/vintasend-implementation-template/` is a complete package that installs,
type-checks and passes its test suite, with a stub and a matching test for every implementable
component.

**Feature flag**: none — new, isolated directory that no shipping code imports.

Changes:

1. New `@templates/vintasend-implementation-template/pyproject.toml` — Poetry package named
   `vintasend-implementation-template`, depending on the current `vintasend` line, with `ruff`,
   `mypy`, `pytest` and `pytest-asyncio` dev deps mirroring this repo's config, and `py.typed`.
2. New `@templates/vintasend-implementation-template/README.md` — placeholder pointing at the
   workflow doc added in Phase 2 (kept minimal here so the two do not drift).
3. Stub sources under `src/` (or the package dir), one per component, each subclassing the real ABC
   with every abstract method present and `TODO`-bodied:
   - `backend.py` → `BaseNotificationBackend`
   - `adapter.py` → `BaseNotificationAdapter`
   - `template_renderer.py` → `BaseNotificationTemplateRenderer` (and/or the email/SMS subclasses)
   - `queue_service.py` → `BaseNotificationQueueService`
   - `attachment_manager.py` → `BaseAttachmentManager`
   - the asyncio counterpart for each component that has one (`AsyncIOBase*`), since a real
     integration usually ships both halves.
4. One test file per stub under `tests/`, each asserting the stub is importable, subclasses the right
   ABC, and lists the expected abstract methods — the scaffold assertions that go green on a fresh
   clone.
5. `.gitignore` / config so the template is excluded from `vintasend`'s own lint/type/test runs, the
   same way `implementations/` already is (confirm the exclusion globs in `pyproject.toml` and
   `tox.ini`).

Spec use-case: no spec — ports the `vintasend-implementation-template` package structure from
`vintasend-ts` v0.10.0.

Tests:

- **Template's own suite**: `cd templates/vintasend-implementation-template && pytest` passes on the
  scaffold assertions (green before any real implementation is written), and `mypy` / `ruff check`
  are clean.
- **This repo's suite**: a guard test in `vintasend/tests/` asserting the template directory is
  excluded from `vintasend`'s configured lint/type/test paths, so the stubs' `NotImplementedError`
  bodies never fail the main suite. Confirm `poetry run pytest` and `poetry run mypy` at repo root do
  not descend into `templates/`.

**Suggested AI model**: Tier 2 (IDs in [resources/ai-models.yaml](../.claude/skills/plan-feature/resources/ai-models.yaml)).
Scaffolding a package plus symmetric stubs and tests — repetitive but must stay faithful to each
current ABC's method set, which is the part worth care.

Acceptance: `cd templates/vintasend-implementation-template && poetry install && poetry run pytest && poetry run mypy`
succeeds on the untouched skeleton, and this repo's root `pytest`/`mypy` do not descend into it.

### Phase 2 — Clone script and workflow documentation

**Goal**: a contributor can run one command to get a renamed working package, then follow a written,
ordered checklist to fill it in.

**Feature flag**: none.

Changes:

1. New `@templates/vintasend-implementation-template/scripts/clone.py` (or `.sh`) — copy the skeleton
   to a target directory, replace the package name throughout (`pyproject.toml`, package dir, imports),
   and print next steps. Dependency-free.
2. Flesh out the template `README.md`: a numbered workflow (clone → rename → implement each component
   → test against the fakes → publish) and a per-component checklist naming the abstract methods to
   implement and pointing at the corresponding `stubs/` fake as the reference. Cover the queue service
   and attachment manager explicitly, since those are the components a pre-2.0 mental model would
   miss.
3. `AGENTS.md` / this repo's `README.md`: a short "Creating a new implementation" pointer to the
   template and the clone script, alongside the existing `implementations/` submodule guidance.

Spec use-case: no spec — ports TS's clone script and contributor workflow.

Tests:

- **Script test**: running the clone script into a temp directory produces a package whose name is
  replaced everywhere and whose own `pytest` passes — proving a clone is immediately green. Run it in
  the repo's suite against a `tmp_path`.
- **Doc check**: every abstract method named in the checklist exists on the current ABC (a small test
  that reflects over the ABCs and diffs against the checklist keeps the doc from rotting when a seam
  changes).

**Suggested AI model**: Tier 2. A small script plus prose, with one reflection-based doc-freshness
test that needs a little care.

**Reusable skills**: `Skill(deslop-comments)` over the new README prose.

Acceptance: the clone script produces a renamed package whose test suite passes in a temp directory,
and the checklist-vs-ABC reflection test confirms every named method still exists.

## 5. Risk & Rollout Notes

- **Zero runtime risk.** The shipped `vintasend` wheel does not change; the template is an
  in-repo directory excluded from the package build. Confirm it is not swept into the sdist/wheel via
  `pyproject.toml` packaging config — a template must not be published *inside* `vintasend`.
- **The real risk is drift.** A template that lags the seams is worse than none, because it teaches a
  stale contract. The Phase 2 checklist-vs-ABC reflection test is the guard; keep it, and treat "add a
  seam method ⇒ update the template stub" as part of any future seam change (worth a line in
  `AGENTS.md`'s seam-change rules).
- **No migration, no backfill, no rollback complexity.** Revert the directory.
- **Downstream**: none affected — no seam, setting, or exported name changes. No `vintasend-*`
  release needed.
- **Staging patch build**: after Phase 1, run `poetry build` at repo root and inspect the wheel
  contents to confirm `templates/` is absent before release.

## 6. Open Questions

| Question | Recommended default |
|---|---|
| Clone script in Python or shell? | **Python.** Cross-platform, no quoting pitfalls, and this repo's contributors already have Python. A shell script would be shorter but fails on Windows contributors. |
| Should the template include a logger stub? | **Only if the logger seam ships.** Logger injection is an unfilled gap today. If it lands before this plan, add a `logger.py` stub; if not, leave a `TODO` note in the README so it is added alongside the logger work. |
| Should the template be a submodule like the real packages? | **No.** A submodule drifts independently and needs its own release discipline, which is the opposite of what a seam-tracking skeleton needs. In-repo keeps it honest. |
| Publish the template to PyPI? | **No.** It is a starting point to copy, not a dependency to install. Publishing it would invite people to `pip install` scaffolding. The clone script is the distribution mechanism. |
| Cover asyncio variants for every component? | **Yes, where the ABC has an `AsyncIO*` twin.** Real backends and adapters routinely ship both, and omitting the async stubs would send implementers to re-derive them. |

## 7. Touch List

**Phase 1**

- `@templates/vintasend-implementation-template/pyproject.toml` — new.
- `@templates/vintasend-implementation-template/README.md` — new (placeholder).
- `@templates/vintasend-implementation-template/src/…/backend.py`, `adapter.py`, `template_renderer.py`, `queue_service.py`, `attachment_manager.py` (+ asyncio twins) — new stubs.
- `@templates/vintasend-implementation-template/tests/…` — one scaffold test per stub.
- [pyproject.toml](../pyproject.toml), [tox.ini](../tox.ini) — confirm `templates/` is excluded from lint/type/test and from the package build.
- `@vintasend/tests/…` — a guard test asserting the exclusion.

**Phase 2**

- `@templates/vintasend-implementation-template/scripts/clone.py` — new.
- `@templates/vintasend-implementation-template/README.md` — full workflow + per-component checklist.
- [README.md](../README.md), [AGENTS.md](../ai-tools/AGENTS.md) — "Creating a new implementation" pointer.
- `@vintasend/tests/…` — clone-script test and checklist-vs-ABC reflection test.

**Both phases**

- [RELEASE_NOTES.md](../RELEASE_NOTES.md) — plain minor/patch entry; no `### Backwards compatibility` section (no seam moves).
- [pyproject.toml](../pyproject.toml) — version bump.
