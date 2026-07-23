from vintasend.services.git_commit_sha_providers.asyncio_base import AsyncIOBaseGitCommitShaProvider
from vintasend.services.git_commit_sha_providers.base import BaseGitCommitShaProvider


# A syntactically valid 40-character lowercase hex SHA, used as a fixed stand-in for "the
# current commit" in tests and as a reference value for downstream authors.
FAKE_GIT_COMMIT_SHA = "a" * 40


class FakeGitCommitShaProvider(BaseGitCommitShaProvider):
    """In-memory git commit SHA provider used by tests and as a reference implementation.

    Returns a fixed, valid 40-character SHA by default. Construct with `sha=None` (or any
    other value) to model a provider that cannot currently determine the revision.
    """

    def __init__(self, sha: str | None = FAKE_GIT_COMMIT_SHA) -> None:
        self.sha = sha

    def get_current_git_commit_sha(self) -> str | None:
        return self.sha


class FakeAsyncIOGitCommitShaProvider(AsyncIOBaseGitCommitShaProvider):
    """AsyncIO twin of `FakeGitCommitShaProvider`. See its docstring."""

    def __init__(self, sha: str | None = FAKE_GIT_COMMIT_SHA) -> None:
        self.sha = sha

    async def get_current_git_commit_sha(self) -> str | None:
        return self.sha
