class NotificationError(ValueError):
    pass


class NotificationContextGenerationError(NotificationError):
    pass


class NotificationUpdateError(NotificationError):
    pass


class NotificationMarkFailedError(NotificationUpdateError):
    pass


class NotificationMarkSentError(NotificationUpdateError):
    pass


class NotificationCancelError(NotificationError):
    pass


class NotificationNotFoundError(NotificationError):
    pass


class NotificationAlreadySentError(NotificationError):
    pass


class NotificationUserNotFoundError(NotificationError):
    pass


class NotificationTemplateRenderingError(NotificationError):
    pass


class NotificationPreheaderTemplateRenderingError(NotificationTemplateRenderingError):
    pass


class NotificationSubjectTemplateRenderingError(NotificationTemplateRenderingError):
    pass


class NotificationBodyTemplateRenderingError(NotificationTemplateRenderingError):
    pass


class NotificationSendError(NotificationError):
    pass


class DuplicateNotificationAdapterError(NotificationError):
    """Raised when two or more adapters declare the same notification type."""


class InvalidOneOffNotificationRecipientError(NotificationError):
    """Raised when a one-off notification's email_or_phone is empty or malformed."""


class UnsupportedAttachmentFileTypeError(NotificationError):
    """Raised when an attachment manager is given a file input it cannot read."""


class AttachmentFileNotFoundError(NotificationError):
    """Raised when a `NotificationAttachmentReference` points at an unknown `file_id`."""


class AttachmentUploadError(NotificationError):
    """Raised by an attachment manager when it fails to store a file's bytes."""


class TenantReassignmentError(NotificationError):
    """Raised when an update attempts to change a notification's tenant after creation."""


class NotificationResendError(NotificationError):
    """Raised when a notification cannot be resent -- it is a one-off, or it is scheduled
    in the future (``send_after`` set and not yet due)."""


class NotificationQueueServiceMissingError(NotificationError):
    """Raised when no queue service import string is configured, and no default is set either."""


class NotificationQueueServiceResolutionError(NotificationError):
    """Raised when a configured queue service import string cannot be turned into a working
    queue service: the import fails, the class cannot be instantiated, or the resolved object
    is not a queue service.
    """


class NotificationServiceFactoryError(NotificationError):
    """Raised when a worker's ``NOTIFICATION_SERVICE_FACTORY`` cannot be imported or called."""


class InvalidGitCommitShaError(NotificationError):
    """Raised when a git commit SHA provider returns a non-null value that is not 40
    lowercase hex characters once trimmed and lowercased."""


class GitCommitShaReassignmentError(NotificationError):
    """Raised when an update attempts to set a notification's git_commit_sha. The field is
    system-managed -- only NotificationService writes it, at send time, through
    store_git_commit_sha."""


class BackendNotFoundError(NotificationError):
    """Raised when a multi-backend read or operation names a ``backend_identifier`` that
    is not registered on the service -- neither the primary nor any additional backend."""


class DuplicateBackendIdentifierError(NotificationError):
    """Raised when two configured backends resolve to the same identifier."""


class NotificationRenderError(NotificationError):
    """Raised when a notification has no email renderer available to render it: either no
    adapter is configured for its notification type, or the configured adapter's template
    renderer is not a BaseTemplatedEmailRenderer.

    Distinct from NotificationTemplateRenderingError, which covers a renderer failing while
    actually rendering a template it was handed."""
