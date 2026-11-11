from enum import Enum
from typing import Final, Literal


class NotificationStatus(Enum):
    PENDING_SEND = "PENDING_SEND"
    SENT = "SENT"
    FAILED = "FAILED"
    READ = "READ"
    CANCELLED = "CANCELLED"


class NotificationTypes(Enum):
    PUSH = "PUSH"
    EMAIL = "EMAIL"
    SMS = "SMS"
    IN_APP = "IN_APP"


# The statuses a notification can be requeued from: the ones that did not go out.
#
# SENT and READ are left out because requeueing a delivered notification would deliver it twice
# under one record. ``resend_notification`` creates a new notification for that instead.
# PENDING_SEND is left out because it is already queued.
REQUEUEABLE_NOTIFICATION_STATUSES: Final[tuple[str, ...]] = (
    NotificationStatus.FAILED.value,
    NotificationStatus.CANCELLED.value,
)

# The statuses whose template pin can still change: every status that may still render.
#
# A delivered notification's ``requested_template_version`` records what it was asked to render
# when it went out. Moving the pin afterwards would make the record disagree with what was sent.
REPINNABLE_NOTIFICATION_STATUSES: Final[tuple[str, ...]] = (
    NotificationStatus.PENDING_SEND.value,
    NotificationStatus.FAILED.value,
    NotificationStatus.CANCELLED.value,
)


class Unset(Enum):
    """The type of ``UNSET``, a value that means "the caller did not pass this argument".

    Used where ``None`` is itself a meaningful value. ``requeue_notification`` is the example:
    ``send_after=None`` means "send it now", and leaving ``send_after`` out means "keep the
    notification's own ``send_after``".

    An enum with one member, so mypy narrows ``value is UNSET`` the same way it narrows
    ``value is None``.
    """

    UNSET = "UNSET"


UNSET: Final = Unset.UNSET

# The type of an argument that may be left out, given a value, or set to ``None``.
UnsetType = Literal[Unset.UNSET]


# Which optional notification actions a backend supports beyond the required ones. A backend
# reports them from ``get_action_capabilities()`` and the service merges the report over this
# default. Keys are camelCase dotted, byte-identical to the TypeScript library, like the filter
# capability keys.
#
# Unlike the filter capabilities, every key defaults to ``False``. The service passes an option
# only to a primary backend that reports it and refuses it otherwise, with
# ``NotificationActionError`` reason ``UNSUPPORTED``, so a backend written before these options
# existed is never handed one it would ignore.
#
# * ``repin.expectedVersion``: ``repin_notification`` is implemented and checks
#   ``expected_version``.
# * ``requeue.expectedVersion``: ``requeue_notification`` checks ``expected_version``.
# * ``requeue.templateVersion``: ``requeue_notification`` pins ``template_version`` in the same
#   write that re-queues.
# * ``versions.filter``: ``filter_notification_versions`` is implemented.
DEFAULT_BACKEND_ACTION_CAPABILITIES: Final[dict[str, bool]] = {
    "repin.expectedVersion": False,
    "requeue.expectedVersion": False,
    "requeue.templateVersion": False,
    "versions.filter": False,
}
