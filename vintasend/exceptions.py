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


class NotificationQueueServiceMissingError(NotificationError):
    """Raised when no queue service import string is configured, and no default is set either."""


class NotificationQueueServiceResolutionError(NotificationError):
    """Raised when a configured queue service import string cannot be turned into a working
    queue service: the import fails, the class cannot be instantiated, or the resolved object
    is not a queue service.
    """


class NotificationServiceFactoryError(NotificationError):
    """Raised when a worker's ``NOTIFICATION_SERVICE_FACTORY`` cannot be imported or called."""
