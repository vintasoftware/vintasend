from abc import ABC, abstractmethod


class AsyncIOBaseGitCommitShaProvider(ABC):
    """AsyncIO twin of `BaseGitCommitShaProvider`. See its docstring."""

    @abstractmethod
    async def get_current_git_commit_sha(self) -> str | None:
        """Return the current commit SHA, or None if it cannot be determined right now.

        A None return means "unknown this call" -- the service skips the write rather than
        raising. Only a non-null, malformed value (not 40 lowercase hex characters once
        trimmed) is treated as an error, by `normalize_git_commit_sha`.
        """
        ...
