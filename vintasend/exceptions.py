import uuid
from typing import Literal


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


class UnconfirmedNotificationUpdateError(NotificationUpdateError):
    """Raised when a backend issued a write but cannot tell whether it applied, because the
    affected-row count came back in a form it could not read.

    Distinct from a plain ``NotificationUpdateError``, which means the write definitively did
    not apply -- no row matched. Here the write may or may not have landed, so a caller that
    needs to know should re-read the notification rather than assume either outcome. It
    subclasses ``NotificationUpdateError`` so existing update handling still catches it; catch
    this type specifically only when indeterminate and failed need to be told apart."""


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


class UsedTemplateVersionReassignmentError(NotificationError):
    """Raised when an update attempts to set a notification's used_template_version. The
    field is system-managed -- only NotificationService writes it, at send time, from the
    version the renderer reported, through store_template_version. Set
    ``requested_template_version`` instead to change which version a notification renders."""


class BackendNotFoundError(NotificationError):
    """Raised when a multi-backend read or operation names a ``backend_identifier`` that
    is not registered on the service -- neither the primary nor any additional backend."""


class DuplicateBackendIdentifierError(NotificationError):
    """Raised when two configured backends resolve to the same identifier."""


class ReplicationError(NotificationError):
    """Raised when a replication worker cannot carry out a replication it was told to do --
    for example ``process_replication`` is handed a notification id that does not resolve on
    the primary backend, so there is no authoritative snapshot to replicate.

    Distinct from a best-effort replica write failing during inline or queued fan-out: those
    are logged and reconciled later, never raised, because the primary write is the source of
    truth. A ``ReplicationError`` means the reconciliation entrypoint itself was given something
    it cannot act on."""


class BackendMigrationError(NotificationError):
    """Raised for a migration-level misuse of ``NotificationService.migrate_to_backend`` --
    for example the destination backend named is the same as the source backend.

    Distinct from a single record's destination-write failure during migration: those are
    captured in the returned ``BackendMigrationResult.failures`` rather than raised, so one bad
    record does not abort the whole migration."""


class NotificationRenderError(NotificationError):
    """Raised when a notification has no email renderer available to render it: either no
    adapter is configured for its notification type, or the configured adapter's template
    renderer is not a BaseTemplatedEmailRenderer.

    Distinct from NotificationTemplateRenderingError, which covers a renderer failing while
    actually rendering a template it was handed."""


NotificationAction = Literal["requeue", "repin"]

# Why ``requeue_notification`` or ``repin_notification`` refused:
#
# * ``NOT_FOUND``: no notification has that ID in the primary backend.
# * ``INVALID_STATUS``: the notification's status does not allow the action. The error's
#   ``status`` says which status it was, when the service knows it.
# * ``NO_TEMPLATE_VERSION``: a repin to the current version found none. Either no renderer for
#   the notification's type versions its templates, or the renderer failed to answer.
NotificationActionErrorReason = Literal["NOT_FOUND", "INVALID_STATUS", "NO_TEMPLATE_VERSION"]


class NotificationActionError(NotificationError):
    """Raised when ``requeue_notification`` or ``repin_notification`` refuses, before anything
    is written.

    Callers branch on ``reason``, not on the message. An HTTP API maps ``NOT_FOUND`` to 404 and
    every other reason to 409.

    The message names the notification by its ID and nothing else, so it is safe to log and to
    return to a client.
    """

    def __init__(
        self,
        message: str,
        *,
        action: NotificationAction,
        reason: NotificationActionErrorReason,
        notification_id: "int | str | uuid.UUID",
        status: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.action: NotificationAction = action
        self.reason: NotificationActionErrorReason = reason
        self.notification_id = notification_id
        self.status = status


class InvalidTemplateVersionError(NotificationError):
    """Raised when a template version passed to ``repin_notification`` is not a non-negative
    ``int``. ``bool`` is refused too, although it is an ``int`` subclass. Raised before anything
    is read."""


class NotificationRequeueError(NotificationUpdateError):
    """Raised by a backend's ``requeue_notification`` when the notification exists but its
    status is not one of ``REQUEUEABLE_NOTIFICATION_STATUSES``, so nothing was written.

    The service checks the status first, so a backend raises this only when another caller
    changed the status in between. For example, a worker sent the notification, or a second
    caller requeued it first."""
