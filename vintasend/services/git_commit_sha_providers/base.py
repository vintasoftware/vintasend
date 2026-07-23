from abc import ABC, abstractmethod


class BaseGitCommitShaProvider(ABC):
    """Resolves the git commit SHA of the source-code revision currently running.

    Injected into a `NotificationService` exactly like `BaseNotificationQueueService` or
    `BaseAttachmentManager` -- as an instance, a dotted import string, or the
    `NOTIFICATION_GIT_COMMIT_SHA_PROVIDER` setting. A host that configures no provider gets
    no SHA resolution at all: `NotificationService` never calls this class and never writes
    to `Notification.git_commit_sha`.

    `get_current_git_commit_sha` is called on every send, foreground and background alike,
    so it should be cheap -- reading a baked-in value or an environment variable, not
    shelling out to `git` on a deployed artifact that has no `.git` directory.
    """

    @abstractmethod
    def get_current_git_commit_sha(self) -> str | None:
        """Return the current commit SHA, or None if it cannot be determined right now.

        A None return means "unknown this call" -- the service skips the write rather than
        raising. Only a non-null, malformed value (not 40 lowercase hex characters once
        trimmed) is treated as an error, by `normalize_git_commit_sha`.
        """
        ...
