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
