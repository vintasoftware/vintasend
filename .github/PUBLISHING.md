# GitHub Actions Publishing Workflow

This document explains how to use the automated publishing workflow for VintaSend.

## How It Works

The workflow (`/.github/workflows/publish.yml`) automatically:

1. **Triggers** when you push a git tag (e.g., `v1.0.0`, `v1.2.3`)
2. **Runs tests** across multiple Python versions (3.10-3.13) to ensure quality
3. **Builds** the package using Poetry
4. **Publishes** to PyPI if all tests pass
5. **Creates** a GitHub release with the built artifacts

## Required Secrets

Before using this workflow, you need to configure these repository secrets:

### PyPI API Token
1. Go to [PyPI Account Settings](https://pypi.org/manage/account/)
2. Create an API token with scope for the `vintasend` project
3. Add it as a repository secret named `PYPI_API_TOKEN`

### GitHub Token
The `GITHUB_TOKEN` is automatically provided by GitHub Actions - no setup needed.

## How to Release

1. **Update version** in `pyproject.toml` (optional - the workflow will do this automatically based on the tag)
2. **Commit your changes** and push to main
3. **Create and push a tag**:
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

The workflow will automatically:
- Run all tests
- Build the package
- Publish to PyPI
- Create a GitHub release

## Tag Format

Use semantic versioning with a `v` prefix:
- `v1.0.0` - Major release
- `v1.1.0` - Minor release  
- `v1.1.1` - Patch release
- `v2.0.0-alpha.1` - Pre-release

## Manual Release (Alternative)

If you prefer manual control, you can also publish manually:

```bash
# Build the package
poetry build

# Publish to PyPI
poetry config pypi-token.pypi YOUR_TOKEN_HERE
poetry publish

# Create GitHub release manually through the web interface
```

## Troubleshooting

### Tests Fail
If tests fail, the package won't be published. Fix the issues and create a new tag.

### PyPI Upload Fails
- Check that the `PYPI_API_TOKEN` secret is correctly configured
- Ensure the version doesn't already exist on PyPI
- Verify the package name and metadata

### GitHub Release Fails
- Check repository permissions
- Ensure the workflow has `contents: write` permission (already configured)

## Security

- The workflow uses trusted publishing patterns
- Secrets are never logged or exposed
- Only tagged releases trigger publishing
- All tests must pass before publishing
