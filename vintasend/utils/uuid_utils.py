"""UUIDv7 generation for the Python versions that lack ``uuid.uuid7``.

``uuid.uuid7`` arrived in Python 3.14, and this package supports 3.10. Backends use a UUIDv7 as
the version token of a notification (see ``BaseNotificationBackend.filter_notification_versions``):
a fresh one on every write, compared for equality only. Time-ordering is a convenience for
humans reading the column; nothing in this library relies on it.
"""

import os
import sys
import time
import uuid


__all__ = ["uuid7"]


if sys.version_info >= (3, 14):

    def uuid7() -> uuid.UUID:
        """A new UUIDv7, from the standard library."""
        return uuid.uuid7()

else:

    def uuid7() -> uuid.UUID:
        """A new UUIDv7 (RFC 9562): a 48-bit Unix time in milliseconds, then 74 random bits."""
        unix_ms = time.time_ns() // 1_000_000
        random_bits = int.from_bytes(os.urandom(10), "big")
        rand_a = random_bits >> 62 & 0xFFF
        rand_b = random_bits & ((1 << 62) - 1)
        value = (unix_ms & ((1 << 48) - 1)) << 80
        value |= 0x7 << 76
        value |= rand_a << 64
        value |= 0b10 << 62
        value |= rand_b
        return uuid.UUID(int=value)
