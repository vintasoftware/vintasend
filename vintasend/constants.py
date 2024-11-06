from enum import Enum


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