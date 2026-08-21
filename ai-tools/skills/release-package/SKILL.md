---
name: release-package
description: Cut a vintasend release to PyPI. Covers choosing the version number against the ABC-seam compatibility rules, writing the RELEASE_NOTES.md entry (including the Backwards compatibility section downstream implementers depend on), and tagging to trigger publish.yml. Use when the user says "cut a release", "publish to PyPI", "bump the version", or "release X.Y.Z".
---

# Release vintasend to PyPI

Releases are tag-triggered. Pushing a tag matching `v*` runs
[`.github/workflows/publish.yml`](../../../.github/workflows/publish.yml), which re-runs the full
3.10–3.14 test matrix, builds with Poetry, runs `twine check`, and publishes to PyPI. Nothing
publishes without a tag, and nothing publishes if the matrix is red.

`.github/PUBLISHING.md` documents the workflow's mechanics and the `PYPI_API_TOKEN` secret. This
skill covers the decisions around it.

## Decision: which version number

vintasend's public contract is not just its function signatures — it is the three ABC seams that
every `vintasend-*` package implements. Choose the number against what a **downstream implementer**
has to do:

| Change | Bump | Because |
|---|---|---|
| Bug fix, no API change | **patch** | Implementers do nothing. |
| New concrete method on a seam, or a new optional argument | **minor** | Implementers do nothing; they may override the default for efficiency. |
| New `@abstractmethod` on a seam | **minor**, with a mandatory `### Backwards compatibility` note | This is the project's established practice — see below. |
| Renamed / reordered / removed seam method, or changed semantics of an existing one | **major** | Implementers' existing overrides break silently or by signature. |

### The abstract-method rule, as this project actually practices it

Adding an `@abstractmethod` **does** break every downstream implementation — the class raises
`TypeError` at instantiation until the method is implemented. Strict semver would call that major.

**This project has chosen to ship those as minor releases** with an explicit, prominent
`### Backwards compatibility` section. Version 1.2.0 is the worked example: it added
`filter_all_in_app_notifications`, `filter_in_app_notifications`, and `mark_sent_as_read_bulk` to
both `BaseNotificationBackend` and `AsyncIOBaseNotificationBackend`, and the release notes say
plainly that custom backend subclasses MUST implement them.

Follow that precedent rather than unilaterally switching to major bumps. But:

- The `### Backwards compatibility` section is **not optional** for these releases. It is the only
  warning a downstream implementer gets.
- Name every added abstract method and both classes it landed on.
- State explicitly which additions are abstract (MUST implement) versus concrete-with-defaults
  (SHOULD override for efficiency). 1.2.0 draws exactly this distinction; keep it.
- If in doubt about whether a change is additive, ask the user before choosing the number. Getting
  this wrong strands every downstream package.

## Checklist

1. **Confirm the tree is releasable.**

   ```bash
   poetry run ruff check .
   poetry run mypy
   poetry run tox          # the full 3.10-3.14 matrix, not just poetry run pytest
   ```

   Run `tox`, not just `pytest`. The publish workflow runs the matrix and a release that only works
   on your local interpreter fails there — after you have already pushed the tag.

2. **Decide the version** using the table above. Confirm it with the user rather than inferring it
   from the diff alone.

3. **Bump the version with [`scripts/bump_version.py`](../../../scripts/bump_version.py).** Every
   `vintasend-*` package releases in lockstep on one shared number, so the version moves in this
   repo *and* in each submodule together:

   ```bash
   scripts/bump_version.py minor --dry-run   # inspect first
   scripts/bump_version.py minor
   ```

   It rewrites `version` in all 11 `pyproject.toml` files and moves each submodule's
   `vintasend` pin to match, keeping the operator it already used. `pyproject.toml` is the single
   source of truth for the published version; nothing derives it from the tag.

   The bump leaves every `poetry.lock` stale. Relock the root before tagging (`poetry lock`); the
   submodules can only be relocked after the root release is live on PyPI, since their pins now
   name a version that does not exist yet. [`scripts/lock_subpackages.py`](../../../scripts/lock_subpackages.py)
   does that walk — relock, commit, push, in each submodule's own repository — and the bump script
   prints this ordering when it finishes.

4. **Add the `RELEASE_NOTES.md` entry** at the top, directly under `# Release Notes`. Match the
   existing shape:

   ```markdown
   ## Version X.Y.Z (YYYY-MM-DD)

   ### Features
   * ...

   ### Bug Fixes
   * ...

   ### Backwards compatibility
   * ...
   ```

   Use only the subsections that apply — older entries use plain bullet lists for small patch
   releases, which is fine. **`### Backwards compatibility` is mandatory whenever a seam changed**,
   and should say "no existing method signature or semantic changed; this is an additive minor
   release" when that is true, so its absence is never ambiguous.

   Write for a downstream implementer maintaining `vintasend-django` or `vintasend-sqlalchemy`, not
   for an end user of this repo. The useful sentence is "you must implement X on your backend", not
   "we improved in-app notifications".

5. **Check whether `README.md` needs updating.** New public API means new usage docs. The README is
   the primary documentation for a library.

6. **Commit, open a PR, merge to `main`.** Normal flow — do not tag from a branch.

7. **Tag and push the tag** with [`scripts/tag_release.py`](../../../scripts/tag_release.py):

   ```bash
   git checkout main && git pull --ff-only
   scripts/tag_release.py --dry-run   # run every check, change nothing
   scripts/tag_release.py             # check, show the plan, ask, then push
   ```

   It takes the version from `pyproject.toml` rather than an argument, so the tag can never
   disagree with what Poetry will build, and it refuses to run unless you are on `main` with a
   clean tree whose HEAD is already pushed, the tag is absent both locally and on origin, every
   package shares the version, and `RELEASE_NOTES.md` has the matching entry. It then pushes an
   annotated `vX.Y.Z` tag and opens the GitHub release using that entry as the body.

   Then release the submodules — **after** this release is live on PyPI, since every subpackage
   pins `vintasend` at this exact version and cannot resolve it before then.

   That happens in waves, not in one go. A package can only be locked and tagged once every
   vintasend it depends on is installable, and two of them depend on a sibling rather than only on
   the root: `vintasend-django-templates-manager` and `vintasend-templates-management-api` both
   need `vintasend-managed-templates` published first. So per wave:

   ```bash
   scripts/lock_subpackages.py --dry-run   # shows the wave map and what is ready
   scripts/lock_subpackages.py             # relock, commit and push the ready packages
   scripts/tag_subpackages.py              # tag that wave; the rest report as waiting
   # wait for those publishes to land on PyPI, then repeat for the next wave
   ```

   Both scripts print the same release order, tag only what is ready, and check every package in
   the wave before pushing any tag — so a blocked package stops that wave rather than leaving it
   half-published. A package already tagged on origin is reported as done, which is what makes the
   second and third runs safe.

8. **Watch the workflow.** `gh run watch` or the Actions tab. It runs the matrix, then
   `test-before-publish` → `check-tests` → `publish-release`. A red matrix means nothing is
   published and the tag now points at a commit that cannot ship: fix forward, bump to the next
   patch, and tag again. Do not delete and re-push a tag that may already have published.

9. **Verify on PyPI**: `https://pypi.org/project/vintasend/`, and confirm the version installs
   cleanly in a scratch environment.

10. **Coordinate downstream** if a seam changed. Each affected `vintasend-*` package needs its own
    release widening its `vintasend` constraint. This repo's release must land first — downstream
    cannot depend on a version that does not exist yet. See the **Downstream implementations**
    section of [AGENTS.md](../../AGENTS.md) for which packages each seam affects.

## Pitfalls

- **Tagging without bumping `pyproject.toml`.** Poetry builds the version from `pyproject.toml`, not
  the tag. A `v1.3.0` tag on a tree that still says `1.2.0` publishes `1.2.0` — and PyPI rejects it
  as a duplicate, so the release silently fails at the last step.
- **Skipping `### Backwards compatibility` on a seam change.** Downstream maintainers find out when
  their test suite explodes with `TypeError: Can't instantiate abstract class`.
- **Running only `poetry run pytest` before tagging.** The publish workflow runs the whole matrix.
  Version-sensitive code passes locally on 3.14 and fails on 3.10.
- **Re-pushing a deleted tag.** If `publish-release` already uploaded to PyPI, that version is
  immutable — PyPI does not accept re-uploads. Always move forward to a new version.
- **Releasing a change to a seam without checking the AsyncIO twin.** A method added only to
  `BaseNotificationBackend` and not `AsyncIOBaseNotificationBackend` ships an asymmetric contract
  that is far more awkward to fix in a later release than to catch now.

## Verification

Before pushing the tag:

```bash
grep '^version' pyproject.toml          # matches the tag you are about to push
head -5 RELEASE_NOTES.md                # top entry is this version, with today's date
poetry build && poetry run twine check dist/*
```

After the workflow completes: the version is live on PyPI, the GitHub release exists with its
artifacts, and `pip install vintasend==X.Y.Z` works in a clean environment.
