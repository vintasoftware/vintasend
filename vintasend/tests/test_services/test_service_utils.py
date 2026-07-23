"""Unit tests for the pure helpers shared between NotificationService and
AsyncIONotificationService.

`read_file_data` / `is_url` / `download_from_url` used to live in this module. They
moved onto `BaseAttachmentManager.file_to_bytes` (see
`vintasend/services/attachment_managers/base.py`) and their assertions moved with them
to `vintasend/tests/test_services/test_attachment_managers.py`.
"""

from unittest import TestCase

import pytest

from vintasend.exceptions import InvalidOneOffNotificationRecipientError
from vintasend.services.dataclasses import NotificationAttachment
from vintasend.services.service_utils import (
    is_asyncio_context_function,
    is_sync_context_function,
    validate_attachments,
    validate_email_or_phone,
)


class ValidateAttachmentsTestCase(TestCase):
    def test_validate_attachments_returns_the_same_list_unchanged_for_url_attachment(self):
        attachments = [
            NotificationAttachment(file="https://example.com/file.pdf", filename="file.pdf"),
        ]

        result = validate_attachments(attachments)

        assert result is attachments

    def test_validate_attachments_returns_the_same_list_unchanged_for_local_attachment(self):
        attachments = [
            NotificationAttachment(file="local/path.txt", filename="path.txt"),
        ]

        result = validate_attachments(attachments)

        assert result is attachments


class ContextFunctionTypeGuardsTestCase(TestCase):
    def test_sync_function_is_detected_as_sync(self):
        def sync_context(**kwargs):
            return kwargs

        assert is_sync_context_function(sync_context) is True
        assert is_asyncio_context_function(sync_context) is False

    def test_async_function_is_detected_as_asyncio(self):
        async def async_context(**kwargs):
            return kwargs

        assert is_asyncio_context_function(async_context) is True
        assert is_sync_context_function(async_context) is False


class ValidateEmailOrPhoneTestCase(TestCase):
    """Table-driven tests for email and phone validation."""

    def test_valid_emails(self):
        valid_emails = [
            "user@example.com",
            "test.email@subdomain.example.co.uk",
            "a@b.c",
            "name+tag@domain.org",
        ]
        for email in valid_emails:
            try:
                validate_email_or_phone(email)
            except InvalidOneOffNotificationRecipientError:
                pytest.fail(f"Valid email '{email}' was rejected")

    def test_valid_phones(self):
        valid_phones = [
            "1234567890",  # 10 digits
            "12345678901",  # 11 digits
            "123456789012345",  # 15 digits
            "+1234567890",
            "+12345678901",  # with leading + and 11 digits
            "+123456789012345",  # with leading + and 15 digits
        ]
        for phone in valid_phones:
            try:
                validate_email_or_phone(phone)
            except InvalidOneOffNotificationRecipientError:
                pytest.fail(f"Valid phone '{phone}' was rejected")

    def test_empty_string_rejected(self):
        with pytest.raises(
            InvalidOneOffNotificationRecipientError,
            match="email_or_phone must not be empty or whitespace-only",
        ):
            validate_email_or_phone("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(InvalidOneOffNotificationRecipientError):
            validate_email_or_phone("   ")

    def test_invalid_email_format_rejected(self):
        with pytest.raises(
            InvalidOneOffNotificationRecipientError,
            match="email_or_phone must be a valid email or phone number",
        ):
            validate_email_or_phone("not-an-email")

    def test_phone_too_short_rejected(self):
        with pytest.raises(InvalidOneOffNotificationRecipientError):
            validate_email_or_phone("123456789")  # 9 digits

    def test_phone_too_long_rejected(self):
        with pytest.raises(InvalidOneOffNotificationRecipientError):
            validate_email_or_phone("1234567890123456")  # 16 digits

    def test_email_with_trailing_newline_rejected(self):
        with pytest.raises(InvalidOneOffNotificationRecipientError):
            validate_email_or_phone("user@example.com\n")

    def test_phone_with_trailing_newline_rejected(self):
        with pytest.raises(InvalidOneOffNotificationRecipientError):
            validate_email_or_phone("1234567890\n")

    def test_phone_with_leading_plus_and_trailing_newline_rejected(self):
        with pytest.raises(InvalidOneOffNotificationRecipientError):
            validate_email_or_phone("+1234567890\n")

    def test_email_with_trailing_crlf_rejected(self):
        with pytest.raises(InvalidOneOffNotificationRecipientError):
            validate_email_or_phone("user@ex.com\r\n")
