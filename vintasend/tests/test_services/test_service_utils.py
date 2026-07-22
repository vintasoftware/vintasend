"""Unit tests for the pure helpers shared between NotificationService and
AsyncIONotificationService.
"""

import io
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import pytest

from vintasend.exceptions import InvalidOneOffNotificationRecipientError
from vintasend.services.dataclasses import NotificationAttachment
from vintasend.services.service_utils import (
    download_from_url,
    is_asyncio_context_function,
    is_sync_context_function,
    is_url,
    read_file_data,
    validate_attachments,
    validate_email_or_phone,
)


class ReadFileDataTestCase(TestCase):
    def test_read_file_data_with_bytesio(self):
        test_data = b"BytesIO content"
        file_obj = io.BytesIO(test_data)
        file_obj.seek(5)

        result = read_file_data(file_obj)

        assert result == test_data
        assert file_obj.tell() == 5

    def test_read_file_data_with_stringio(self):
        test_data = "StringIO content"
        file_obj = io.StringIO(test_data)

        result = read_file_data(file_obj)

        assert result == test_data.encode("utf-8")

    def test_read_file_data_with_file_path_string(self):
        test_data = b"File path content"
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(test_data)
            temp_file_path = temp_file.name

        try:
            result = read_file_data(temp_file_path)
            assert result == test_data
        finally:
            os.unlink(temp_file_path)

    def test_read_file_data_with_path_object(self):
        test_data = b"Path object content"
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(test_data)
            temp_file_path = Path(temp_file.name)

        try:
            result = read_file_data(temp_file_path)
            assert result == test_data
        finally:
            os.unlink(temp_file_path)

    def test_read_file_data_with_bytes_is_unsupported(self):
        with pytest.raises(ValueError, match="Unsupported file type"):
            read_file_data(b"raw bytes content")

    def test_read_file_data_with_unsupported_type(self):
        with pytest.raises(ValueError, match="Unsupported file type"):
            read_file_data(12345)

    def test_read_file_data_with_nonexistent_file(self):
        with pytest.raises(FileNotFoundError):
            read_file_data("/path/that/does/not/exist.txt")

    @patch("requests.get")
    def test_read_file_data_with_url(self, mock_get):
        mock_response = Mock()
        mock_response.content = b"Mocked downloaded content"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        url = "http://example.com/test.pdf"
        result = read_file_data(url)

        assert result == b"Mocked downloaded content"
        mock_get.assert_called_once_with(url, timeout=30)

    def test_read_file_data_seek_behavior_without_tell(self):
        class NoTellFile:
            def __init__(self, data):
                self.data = data
                self.position = 0

            def read(self):
                return self.data[self.position :]

            def seek(self, pos):
                self.position = pos

        test_data = b"No tell file content"
        file_obj = NoTellFile(test_data)

        result = read_file_data(file_obj)

        assert result == test_data

    def test_read_file_data_no_seek_support(self):
        class NoSeekFile:
            def __init__(self, data):
                self.data = data

            def read(self):
                return self.data

            def tell(self):
                return 0

        test_data = b"No seek file content"
        file_obj = NoSeekFile(test_data)

        result = read_file_data(file_obj)

        assert result == test_data


class IsUrlTestCase(TestCase):
    def test_is_url_detection(self):
        test_cases = [
            ("http://example.com/file.pdf", True),
            ("https://example.com/file.pdf", True),
            ("s3://bucket/file.pdf", True),
            ("gs://bucket/file.pdf", True),
            ("azure://container/file.pdf", True),
            ("/local/path/file.pdf", False),
            ("relative/path/file.pdf", False),
            ("file.pdf", False),
            ("ftp://example.com/file.pdf", False),  # not a supported scheme
        ]

        for url, expected in test_cases:
            result = is_url(url)
            assert result == expected, f"URL: {url}, Expected: {expected}, Got: {result}"


class DownloadFromUrlTestCase(TestCase):
    @patch("requests.get")
    def test_download_from_url_success(self, mock_get):
        mock_response = Mock()
        mock_response.content = b"Mocked document content"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        url = "http://example.com/document.pdf"
        result = download_from_url(url)

        assert result == b"Mocked document content"
        mock_get.assert_called_once_with(url, timeout=30)

    def test_download_from_url_raises_friendly_import_error_when_requests_missing(self):
        with patch.dict(sys.modules, {"requests": None}):
            with pytest.raises(ImportError, match="requests library is required"):
                download_from_url("https://example.com/test-import.pdf")


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
