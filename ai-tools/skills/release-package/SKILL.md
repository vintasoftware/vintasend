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

3. **Bump `version` in [`pyproject.toml`](../../../pyproject.toml)** under `[tool.poetry]`. This is the
   single source of truth for the published version; nothing derives it from the tag.

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

7. **Tag and push the tag.**

   ```bash
   git checkout main && git pull --ff-only
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

   The tag must be `v`-prefixed to match the workflow's `tags: - 'v*'` trigger, and its version must
   equal the `pyproject.toml` version. Nothing enforces that equality — check it by eye.

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
