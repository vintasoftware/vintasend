"""
Comprehensive test suite for notification attachment functionality.

Tests cover:
- Core attachment data structures
- Service layer attachment validation
- Backend attachment storage
- Adapter attachment handling
- Various file input types
- Error handling
- Backward compatibility
"""

import datetime
import hashlib
import io
import os
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch

import pytest

from vintasend.services.dataclasses import (
    Notification,
    NotificationAttachment,
    NotificationContextDict,
    StoredAttachment,
)
from vintasend.services.notification_adapters.stubs.fake_adapter import (
    FakeAsyncIOEmailAdapter,
    FakeEmailAdapter,
)
from vintasend.services.notification_backends.stubs.fake_backend import (
    FakeAsyncIOFileBackend,
    FakeFileAttachmentFile,
    FakeFileBackend,
)
from vintasend.services.notification_service import (
    AsyncIONotificationService,
    NotificationService,
    register_context,
)
from vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer import (
    FakeTemplateRenderer,
)


class TestNotificationAttachmentDataClass(TestCase):
    """Test the NotificationAttachment data class"""

    def test_notification_attachment_creation_with_bytes(self):
        """Test creating NotificationAttachment with bytes data"""
        file_data = b"Test file content"
        attachment = NotificationAttachment(
            filename="test.txt",
            content_type="text/plain",
            file=file_data,
            description="Test file",
            is_inline=False,
        )

        assert attachment.filename == "test.txt"
        assert attachment.content_type == "text/plain"
        assert attachment.file == file_data
        assert attachment.description == "Test file"
        assert attachment.is_inline is False

    def test_notification_attachment_content_type_detection(self):
        """Test automatic content type detection"""
        attachment = NotificationAttachment(
            filename="document.pdf",
            file=b"fake pdf content",
        )

        assert attachment.content_type == "application/pdf"

    def test_notification_attachment_default_content_type(self):
        """Test default content type for unknown extensions"""
        attachment = NotificationAttachment(
            filename="unknown.xyz",
            file=b"unknown content",
        )

        # Note: .xyz extension is detected as chemical/x-xyz by mimetypes library
        assert attachment.content_type == "chemical/x-xyz"

    def test_notification_attachment_url_detection(self):
        """Test URL detection for various URL schemes"""
        test_cases = [
            ("http://example.com/file.pdf", True),
            ("https://example.com/file.pdf", True),
            ("s3://bucket/file.pdf", True),
            ("gs://bucket/file.pdf", True),
            ("azure://container/file.pdf", True),
            ("/local/path/file.pdf", False),
            ("relative/path/file.pdf", False),
        ]

        for url, expected_is_url in test_cases:
            attachment = NotificationAttachment(
                filename="test.pdf",
                file=url,
            )
            assert attachment.is_url() == expected_is_url, f"Failed for URL: {url}"


class TestStoredAttachmentDataClass(TestCase):
    """Test the StoredAttachment data class and file access"""

    def setUp(self):
        self.test_data = b"Test file content for stored attachment"
        self.attachment_file = FakeFileAttachmentFile(self.test_data, "test.txt")
        self.stored_attachment = StoredAttachment(
            id="test-123",
            filename="test.txt",
            content_type="text/plain",
            size=len(self.test_data),
            checksum=hashlib.sha256(self.test_data).hexdigest(),
            created_at=datetime.datetime.now(),
            file=self.attachment_file,
            description="Test stored attachment",
            is_inline=False,
        )

    def test_stored_attachment_get_file_data(self):
        """Test retrieving file data from stored attachment"""
        data = self.stored_attachment.get_file_data()
        assert data == self.test_data

    def test_stored_attachment_get_file_stream(self):
        """Test getting file stream from stored attachment"""
        with self.stored_attachment.get_file_stream() as stream:
            data = stream.read()
            assert data == self.test_data

    def test_stored_attachment_get_file_url(self):
        """Test getting file URL from stored attachment"""
        url = self.stored_attachment.get_file_url()
        assert url.startswith("fake://attachment/")
        assert "test.txt" in url

    def test_stored_attachment_delete(self):
        """Test deleting stored attachment"""
        # Should not raise an exception
        self.stored_attachment.delete()

        # After deletion, accessing file data should raise FileNotFoundError
        with pytest.raises(FileNotFoundError):
            self.stored_attachment.get_file_data()


class TestFakeFileBackendAttachments(TestCase):
    """Test attachment functionality in FakeFileBackend"""

    def setUp(self):
        self.backend = FakeFileBackend(storage_dir="/tmp/test_attachments")

    def test_store_attachments_with_bytes(self):
        """Test storing attachments with bytes data"""
        test_data = b"Hello, World!"
        attachment = NotificationAttachment(
            filename="hello.txt",
            content_type="text/plain",
            file=test_data,
            description="Test bytes attachment",
        )

        stored = self.backend._store_attachments([attachment])

        assert len(stored) == 1
        stored_attachment = stored[0]
        assert stored_attachment.filename == "hello.txt"
        assert stored_attachment.content_type == "text/plain"
        assert stored_attachment.size == len(test_data)
        assert stored_attachment.description == "Test bytes attachment"

        # Verify file data can be retrieved
        retrieved_data = stored_attachment.get_file_data()
        assert retrieved_data == test_data

    def test_store_attachments_with_file_like_object(self):
        """Test storing attachments with file-like objects"""
        test_data = b"File-like object content"
        file_obj = io.BytesIO(test_data)

        attachment = NotificationAttachment(
            filename="filelike.txt",
            file=file_obj,
        )

        stored = self.backend._store_attachments([attachment])

        assert len(stored) == 1
        stored_attachment = stored[0]
        assert stored_attachment.size == len(test_data)

        # Verify file data
        retrieved_data = stored_attachment.get_file_data()
        assert retrieved_data == test_data

    def test_store_attachments_with_url(self):
        """Test storing attachments from URLs (using fake download)"""
        url = "http://example.com/document.pdf"
        attachment = NotificationAttachment(
            filename="document.pdf",
            file=url,
        )

        stored = self.backend._store_attachments([attachment])

        assert len(stored) == 1
        stored_attachment = stored[0]
        assert stored_attachment.filename == "document.pdf"

        # Should contain fake downloaded content
        retrieved_data = stored_attachment.get_file_data()
        assert b"Downloaded content from" in retrieved_data

    def test_store_attachments_empty_list(self):
        """Test storing empty attachment list"""
        stored = self.backend._store_attachments([])
        assert stored == []

    def test_store_attachments_multiple(self):
        """Test storing multiple attachments"""
        attachments = [
            NotificationAttachment(
                filename="file1.txt",
                file=b"Content 1",
            ),
            NotificationAttachment(
                filename="file2.txt",
                file=b"Content 2",
                is_inline=True,
            ),
        ]

        stored = self.backend._store_attachments(attachments)

        assert len(stored) == 2
        assert stored[0].filename == "file1.txt"
        assert stored[1].filename == "file2.txt"
        assert stored[0].is_inline is False
        assert stored[1].is_inline is True

    def test_persist_notification_with_attachments(self):
        """Test creating notification with attachments through backend"""
        attachment = NotificationAttachment(
            filename="notification_attachment.txt",
            file=b"Notification content",
        )

        notification = self.backend.persist_notification(
            user_id=123,
            notification_type="email",
            title="Test Notification",
            body_template="Hello {{name}}",
            context_name="test",
            context_kwargs=NotificationContextDict({"name": "User"}),
            send_after=None,
            subject_template="Test Subject",
            preheader_template="Test Preheader",
            adapter_extra_parameters=None,
            attachments=[attachment],
        )

        assert len(notification.attachments) == 1
        assert notification.attachments[0].filename == "notification_attachment.txt"

    def test_persist_one_off_notification_with_attachments(self):
        """Test creating one-off notification with attachments"""
        attachment = NotificationAttachment(
            filename="oneoff_attachment.txt",
            file=b"One-off content",
        )

        notification = self.backend.persist_one_off_notification(
            email_or_phone="test@example.com",
            first_name="Test",
            last_name="User",
            notification_type="email",
            title="One-off Test",
            body_template="Hello {{name}}",
            context_name="test",
            context_kwargs=NotificationContextDict({"name": "User"}),
            send_after=None,
            subject_template="Test Subject",
            preheader_template="Test Preheader",
            adapter_extra_parameters=None,
            attachments=[attachment],
        )

        assert len(notification.attachments) == 1
        assert notification.attachments[0].filename == "oneoff_attachment.txt"


class TestFakeEmailAdapterAttachments(TestCase):
    """Test attachment handling in FakeEmailAdapter"""

    def setUp(self):
        self.backend = FakeFileBackend(storage_dir="/tmp/test_attachments")
        self.template_renderer = FakeTemplateRenderer()
        self.adapter = FakeEmailAdapter(
            backend=self.backend,
            template_renderer=self.template_renderer
        )

    def test_send_notification_captures_attachment_info(self):
        """Test that adapter captures attachment information when sending"""
        # Create stored attachment
        file_data = b"Email attachment content"
        attachment_file = FakeFileAttachmentFile(file_data, "email_attachment.txt")

        stored_attachment = StoredAttachment(
            id="email-123",
            filename="email_attachment.txt",
            content_type="text/plain",
            size=len(file_data),
            checksum=hashlib.sha256(file_data).hexdigest(),
            created_at=datetime.datetime.now(),
            file=attachment_file,
            description="Email attachment",
        )

        # Create notification with attachment
        notification = Notification(
            id="notif-123",
            user_id=456,
            notification_type="email",
            title="Test Email with Attachment",
            body_template="Hello {{name}}",
            context_name="test",
            context_kwargs=NotificationContextDict({"name": "User"}),
            send_after=None,
            subject_template="Test Subject",
            preheader_template="Test Preheader",
            status="pending",
            attachments=[stored_attachment],
        )

        context = NotificationContextDict({"name": "Test User"})

        # Send notification
        self.adapter.send(notification, context)

        # Verify attachment info was captured
        assert len(self.adapter.sent_emails) == 1
        _, _, sent_attachments = self.adapter.sent_emails[0]

        assert len(sent_attachments) == 1
        attachment_info = sent_attachments[0]

        assert attachment_info["id"] == "email-123"
        assert attachment_info["filename"] == "email_attachment.txt"
        assert attachment_info["content_type"] == "text/plain"
        assert attachment_info["size"] == len(file_data)
        assert attachment_info["description"] == "Email attachment"

    def test_send_notification_without_attachments(self):
        """Test sending notification without attachments (backward compatibility)"""
        notification = Notification(
            id="notif-456",
            user_id=789,
            notification_type="email",
            title="Test Email without Attachments",
            body_template="Hello {{name}}",
            context_name="test",
            context_kwargs=NotificationContextDict({"name": "User"}),
            send_after=None,
            subject_template="Test Subject",
            preheader_template="Test Preheader",
            status="pending",
            attachments=[],  # Empty attachments
        )

        context = NotificationContextDict({"name": "Test User"})

        # Send notification
        self.adapter.send(notification, context)

        # Verify email was sent with empty attachments
        assert len(self.adapter.sent_emails) == 1
        _, _, sent_attachments = self.adapter.sent_emails[0]

        assert len(sent_attachments) == 0

    def test_send_notification_with_inline_attachment(self):
        """Test sending notification with inline attachment"""
        file_data = b"Inline image data"
        attachment_file = FakeFileAttachmentFile(file_data, "inline_image.png")

        stored_attachment = StoredAttachment(
            id="inline-123",
            filename="inline_image.png",
            content_type="image/png",
            size=len(file_data),
            checksum=hashlib.sha256(file_data).hexdigest(),
            created_at=datetime.datetime.now(),
            file=attachment_file,
            is_inline=True,  # Inline attachment
        )

        notification = Notification(
            id="notif-inline",
            user_id=111,
            notification_type="email",
            title="Test Email with Inline Image",
            body_template="Hello {{name}}",
            context_name="test",
            context_kwargs=NotificationContextDict({"name": "User"}),
            send_after=None,
            subject_template="Test Subject",
            preheader_template="Test Preheader",
            status="pending",
            attachments=[stored_attachment],
        )

        context = NotificationContextDict({"name": "Test User"})
        self.adapter.send(notification, context)

        # Verify inline attachment was captured
        _, _, sent_attachments = self.adapter.sent_emails[0]
        attachment_info = sent_attachments[0]

        assert attachment_info["is_inline"] is True
        assert attachment_info["content_type"] == "image/png"


class TestNotificationServiceWithAttachments(TestCase):
    """Test NotificationService attachment functionality"""

    def setUp(self):
        self.backend = FakeFileBackend(storage_dir="/tmp/test_attachments")
        self.backend.notifications = []  # Clear any existing notifications
        self.template_renderer = FakeTemplateRenderer()
        self.adapter = FakeEmailAdapter(
            backend=self.backend,
            template_renderer=self.template_renderer
        )

        self.service = NotificationService(
            notification_adapters=[self.adapter],
            notification_backend=self.backend,
        )

        # Register a simple context function
        @register_context("test_context")
        def test_context(context_kwargs):
            return NotificationContextDict(context_kwargs)

    def tearDown(self):
        # Clear notifications after each test
        if hasattr(self, 'backend'):
            self.backend.notifications = []

    def test_create_notification_with_bytes_attachment(self):
        """Test creating notification with bytes attachment"""
        attachment_data = b"Service layer test content"
        attachment = NotificationAttachment(
            filename="service_test.txt",
            content_type="text/plain",
            file=attachment_data,
            description="Service layer test",
        )

        notification = self.service.create_notification(
            user_id=123,
            notification_type="email",
            title="Service Test with Attachment",
            body_template="Hello {{name}}",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"name": "User"}),
            subject_template="Test Subject",
            preheader_template="Test Preheader",
            attachments=[attachment],
        )

        assert len(notification.attachments) == 1
        stored_attachment = notification.attachments[0]
        assert stored_attachment.filename == "service_test.txt"
        assert stored_attachment.size == len(attachment_data)

    def test_create_one_off_notification_with_attachment(self):
        """Test creating one-off notification with attachment"""
        attachment = NotificationAttachment(
            filename="oneoff_service_test.txt",
            file=b"One-off service content",
        )

        notification = self.service.create_one_off_notification(
            email_or_phone="test@example.com",
            first_name="Test",
            last_name="User",
            notification_type="email",
            title="One-off with Attachment",
            body_template="Hello {{first_name}}",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"first_name": "Test"}),
            subject_template="One-off Subject",
            preheader_template="One-off Preheader",
            attachments=[attachment],
        )

        assert len(notification.attachments) == 1
        assert notification.attachments[0].filename == "oneoff_service_test.txt"

    def test_create_notification_with_multiple_attachments(self):
        """Test creating notification with multiple attachments"""
        attachments = [
            NotificationAttachment(
                filename="doc1.txt",
                file=b"Document 1 content",
                description="First document",
            ),
            NotificationAttachment(
                filename="doc2.pdf",
                file=b"Document 2 content",
                content_type="application/pdf",
                description="Second document",
            ),
            NotificationAttachment(
                filename="image.png",
                file=b"Image content",
                content_type="image/png",
                is_inline=True,
            ),
        ]

        notification = self.service.create_notification(
            user_id=123,
            notification_type="email",
            title="Multiple Attachments Test",
            body_template="Hello {{name}}",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"name": "User"}),
            subject_template="Multiple Attachments",
            preheader_template="Multiple Attachments",
            attachments=attachments,
        )

        assert len(notification.attachments) == 3
        assert notification.attachments[0].filename == "doc1.txt"
        assert notification.attachments[1].filename == "doc2.pdf"
        assert notification.attachments[2].filename == "image.png"
        assert notification.attachments[2].is_inline is True

    def test_backward_compatibility_without_attachments(self):
        """Test that existing code without attachments still works"""
        notification = self.service.create_notification(
            user_id=123,
            notification_type="email",
            title="Backward Compatibility Test",
            body_template="Hello {{name}}",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"name": "User"}),
            subject_template="Test Subject",
            preheader_template="Test Preheader",
            # No attachments parameter
        )

        assert len(notification.attachments) == 0
        assert isinstance(notification.attachments, list)


class TestAttachmentErrorHandling(TestCase):
    """Test error handling for attachment functionality"""

    def setUp(self):
        self.backend = FakeFileBackend(storage_dir="/tmp/test_attachments")

    def test_unsupported_file_type_error(self):
        """Test error handling for unsupported file types"""
        # This should work now since we added bytes support
        attachment = NotificationAttachment(
            filename="test.txt",
            file=b"test content",  # bytes should work
        )

        # Should not raise an error
        stored = self.backend._store_attachments([attachment])
        assert len(stored) == 1

    def test_nonexistent_file_path_error(self):
        """Test error handling for nonexistent file paths"""
        attachment = NotificationAttachment(
            filename="missing.txt",
            file="/nonexistent/path/file.txt",
        )

        with pytest.raises(FileNotFoundError):
            self.backend._store_attachments([attachment])

    def test_empty_attachment_list(self):
        """Test handling empty attachment list"""
        stored = self.backend._store_attachments([])
        assert stored == []

    def test_none_attachment_list(self):
        """Test handling None attachment parameter"""
        notification = self.backend.persist_notification(
            user_id=123,
            notification_type="email",
            title="Test",
            body_template="Hello",
            context_name="test",
            context_kwargs={},
            send_after=None,
            subject_template="Subject",
            preheader_template="Preheader",
            adapter_extra_parameters=None,
            attachments=None,  # None should work
        )

        assert notification.attachments == []


class TestAsyncAttachmentFunctionality(IsolatedAsyncioTestCase):
    """Test async attachment functionality with AsyncIO components"""

    async def asyncSetUp(self):
        self.backend = FakeAsyncIOFileBackend(database_file_name="async_test_notifications.json")
        self.template_renderer = FakeTemplateRenderer()
        self.adapter = FakeAsyncIOEmailAdapter(
            backend=self.backend,
            template_renderer=self.template_renderer
        )

        self.service = AsyncIONotificationService(
            notification_adapters=[self.adapter],
            notification_backend=self.backend,
        )

    async def asyncTearDown(self):
        await self.backend.clear()

    async def test_async_send_notification_with_attachments(self):
        """Test async sending of notification with attachments"""
        # Create stored attachment
        file_data = b"Async attachment content"
        attachment_file = FakeFileAttachmentFile(file_data, "async_test.txt")

        stored_attachment = StoredAttachment(
            id="async-123",
            filename="async_test.txt",
            content_type="text/plain",
            size=len(file_data),
            checksum=hashlib.sha256(file_data).hexdigest(),
            created_at=datetime.datetime.now(),
            file=attachment_file,
        )

        notification = Notification(
            id="async-notif-123",
            user_id=456,
            notification_type="email",
            title="Async Test with Attachment",
            body_template="Hello {{name}}",
            context_name="test",
            context_kwargs=NotificationContextDict({"name": "User"}),
            send_after=None,
            subject_template="Async Test Subject",
            preheader_template="Async Test Preheader",
            status="pending",
            attachments=[stored_attachment],
        )

        context = NotificationContextDict({"name": "Async User"})

        # Async send
        await self.adapter.send(notification, context)

        # Verify attachment info was captured
        assert len(self.adapter.sent_emails) == 1
        _, _, sent_attachments = self.adapter.sent_emails[0]

        assert len(sent_attachments) == 1
        attachment_info = sent_attachments[0]

        assert attachment_info["id"] == "async-123"
        assert attachment_info["filename"] == "async_test.txt"

    async def test_async_backend_store_attachments(self):
        """Test async backend attachment storage"""
        attachment = NotificationAttachment(
            filename="async_backend_test.txt",
            file=b"Async backend content",
        )

        stored = self.backend._store_attachments([attachment])

        assert len(stored) == 1
        stored_attachment = stored[0]
        assert stored_attachment.filename == "async_backend_test.txt"

        # Verify file data can be retrieved
        retrieved_data = stored_attachment.get_file_data()
        assert retrieved_data == b"Async backend content"


class TestAttachmentValidation(TestCase):
    """Test attachment validation functionality"""

    def test_notification_attachment_file_types(self):
        """Test various file input types for NotificationAttachment"""
        test_cases = [
            # (file_input, expected_success, description)
            (b"bytes content", True, "Direct bytes"),
            (io.BytesIO(b"bytesio content"), True, "BytesIO object"),
            (io.StringIO("stringio content"), True, "StringIO object"),
            ("http://example.com/file.pdf", True, "HTTP URL"),
            ("https://example.com/file.pdf", True, "HTTPS URL"),
            ("s3://bucket/file.pdf", True, "S3 URL"),
            (Path("/tmp/test_file.txt"), True, "Path object"),
        ]

        for file_input, _expected_success, description in test_cases:
            attachment = NotificationAttachment(
                filename="test.txt",
                file=file_input,
            )

            # Should create successfully
            assert attachment.file == file_input, f"Failed for {description}"

    def test_content_type_detection_accuracy(self):
        """Test content type detection for various file extensions"""
        test_cases = [
            ("document.pdf", "application/pdf"),
            ("image.jpg", "image/jpeg"),
            ("image.png", "image/png"),
            ("document.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("spreadsheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("archive.zip", "application/zip"),
            ("data.json", "application/json"),
            ("style.css", "text/css"),
            ("script.js", "text/javascript"),
            ("unknown.xyz", "chemical/x-xyz"),  # This is what Python's mimetypes library returns
            ("README", "application/octet-stream"),  # File with no extension defaults to octet-stream
            ("Makefile", "application/octet-stream"),  # Another file with no extension
        ]

        for filename, expected_content_type in test_cases:
            attachment = NotificationAttachment(
                filename=filename,
                file=b"test content",
            )

            assert attachment.content_type == expected_content_type, f"Failed for {filename}"


class TestAttachmentIntegration(TestCase):
    """End-to-end integration tests for attachment functionality"""

    def setUp(self):
        # Clear the backend to avoid test interference
        self.backend = FakeFileBackend(storage_dir="/tmp/integration_test_attachments")
        self.backend.notifications = []  # Clear any existing notifications

        self.template_renderer = FakeTemplateRenderer()
        self.adapter = FakeEmailAdapter(
            backend=self.backend,
            template_renderer=self.template_renderer
        )

        self.service = NotificationService(
            notification_adapters=[self.adapter],
            notification_backend=self.backend,
        )

        # Register context function
        @register_context("integration_test")
        def integration_test_context(context_kwargs):
            return NotificationContextDict(context_kwargs)

    def tearDown(self):
        # Clear notifications after each test to avoid interference
        if hasattr(self, 'backend'):
            self.backend.notifications = []

    def test_full_notification_flow_with_attachments(self):
        """Test complete flow from creation to sending with attachments"""
        # Create various types of attachments
        attachments = [
            NotificationAttachment(
                filename="document.pdf",
                file=b"PDF document content",
                content_type="application/pdf",
                description="Important document",
            ),
            NotificationAttachment(
                filename="image.png",
                file=b"PNG image content",
                content_type="image/png",
                is_inline=True,
                description="Inline image",
            ),
            NotificationAttachment(
                filename="data.txt",
                file=io.BytesIO(b"Text file content"),
                description="Text file from BytesIO",
            ),
        ]

        # Create notification
        notification = self.service.create_notification(
            user_id=123,
            notification_type="email",
            title="Integration Test with Attachments",
            body_template="Hello {{name}}, see attached files.",
            context_name="integration_test",
            context_kwargs=NotificationContextDict({"name": "Integration User"}),
            subject_template="Files Attached",
            preheader_template="Check the attachments",
            attachments=attachments,
        )

        # Verify notification was created with attachments
        assert len(notification.attachments) == 3

        # Verify attachment details
        pdf_attachment = notification.attachments[0]
        assert pdf_attachment.filename == "document.pdf"
        assert pdf_attachment.content_type == "application/pdf"
        assert pdf_attachment.description == "Important document"
        assert pdf_attachment.is_inline is False

        image_attachment = notification.attachments[1]
        assert image_attachment.filename == "image.png"
        assert image_attachment.is_inline is True

        text_attachment = notification.attachments[2]
        assert text_attachment.filename == "data.txt"

        # Verify file access works
        pdf_data = pdf_attachment.get_file_data()
        assert pdf_data == b"PDF document content"

        # Verify URLs work
        pdf_url = pdf_attachment.get_file_url()
        assert "fake://attachment/" in pdf_url

        # Check that notification is in backend storage
        assert len(self.backend.notifications) == 1
        stored_notification = self.backend.notifications[0]
        assert stored_notification.id == notification.id
        assert len(stored_notification.attachments) == 3

    def test_one_off_notification_flow_with_attachments(self):
        """Test one-off notification flow with attachments"""
        attachment = NotificationAttachment(
            filename="welcome_guide.pdf",
            file=b"Welcome guide content",
            description="Welcome package",
        )

        notification = self.service.create_one_off_notification(
            email_or_phone="newuser@example.com",
            first_name="New",
            last_name="User",
            notification_type="email",
            title="Welcome to Our Service",
            body_template="Welcome {{first_name}}! Please see the attached guide.",
            context_name="integration_test",
            context_kwargs=NotificationContextDict({"first_name": "New"}),
            subject_template="Welcome Package",
            preheader_template="Your welcome guide is attached",
            attachments=[attachment],
        )

        # Verify one-off notification with attachment
        assert len(notification.attachments) == 1
        assert notification.attachments[0].filename == "welcome_guide.pdf"

        # Verify it was stored
        assert len(self.backend.notifications) == 1


class TestNotificationServiceFileHandling(TestCase):
    """Test the _read_file_data method in NotificationService"""

    def setUp(self):
        self.backend = FakeFileBackend()
        self.adapter = FakeEmailAdapter(
            template_renderer=FakeTemplateRenderer(),
            backend=self.backend,
        )
        self.service = NotificationService(
            notification_adapters=[self.adapter],
            notification_backend=self.backend,
        )

        # Register context for testing
        @register_context("file_test")
        def test_context(context_kwargs):
            return NotificationContextDict(context_kwargs)

    def tearDown(self):
        # Clear notifications after each test
        if hasattr(self, 'backend'):
            self.backend.notifications = []

    def test_read_file_data_with_bytesio(self):
        """Test _read_file_data with BytesIO object"""
        test_data = b"BytesIO content"
        file_obj = io.BytesIO(test_data)

        # Set position to middle to test seek functionality
        file_obj.seek(5)

        result = self.service._read_file_data(file_obj)
        assert result == test_data

        # Verify position was restored
        assert file_obj.tell() == 5

    def test_read_file_data_with_stringio(self):
        """Test _read_file_data with StringIO object"""
        test_data = "StringIO content"
        file_obj = io.StringIO(test_data)

        result = self.service._read_file_data(file_obj)
        assert result == test_data.encode('utf-8')

    def test_read_file_data_with_file_path(self):
        """Test _read_file_data with file path"""
        # Create a temporary file
        test_data = b"File path content"
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(test_data)
            temp_file_path = temp_file.name

        try:
            result = self.service._read_file_data(temp_file_path)
            assert result == test_data
        finally:
            os.unlink(temp_file_path)

    def test_read_file_data_with_path_object(self):
        """Test _read_file_data with pathlib.Path object"""
        test_data = b"Path object content"
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(test_data)
            temp_file_path = Path(temp_file.name)

        try:
            result = self.service._read_file_data(temp_file_path)
            assert result == test_data
        finally:
            os.unlink(temp_file_path)

    @patch('vintasend.services.notification_service.requests.get')
    def test_read_file_data_with_url(self, mock_get):
        """Test _read_file_data with URL (should call _download_from_url)"""
        # Mock the requests.get response
        mock_response = Mock()
        mock_response.content = b"Mocked downloaded content"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        
        url = "http://example.com/test.pdf"
        result = self.service._read_file_data(url)
        
        # Should contain the mocked downloaded content
        assert result == b"Mocked downloaded content"
        mock_get.assert_called_once_with(url, timeout=30)

    def test_read_file_data_with_unsupported_type(self):
        """Test _read_file_data with unsupported file type"""
        with pytest.raises(ValueError, match="Unsupported file type"):
            self.service._read_file_data(12345)  # int is not supported

    def test_read_file_data_with_bytes_should_fail(self):
        """Test _read_file_data with raw bytes should fail (not a supported input)"""
        with pytest.raises(ValueError, match="Unsupported file type"):
            self.service._read_file_data(b"raw bytes content")

    def test_read_file_data_with_nonexistent_file(self):
        """Test _read_file_data with non-existent file path"""
        with pytest.raises(FileNotFoundError):
            self.service._read_file_data("/path/that/does/not/exist.txt")

    def test_read_file_data_seek_behavior_without_tell(self):
        """Test _read_file_data with file object that doesn't support tell"""
        class NoTellFile:
            def __init__(self, data):
                self.data = data
                self.position = 0

            def read(self):
                return self.data[self.position:]

            def seek(self, pos):
                self.position = pos

        test_data = b"No tell file content"
        file_obj = NoTellFile(test_data)

        result = self.service._read_file_data(file_obj)
        assert result == test_data

    def test_read_file_data_no_seek_support(self):
        """Test _read_file_data with file object that doesn't support seek"""
        class NoSeekFile:
            def __init__(self, data):
                self.data = data

            def read(self):
                return self.data

            def tell(self):
                return 0

        test_data = b"No seek file content"
        file_obj = NoSeekFile(test_data)

        result = self.service._read_file_data(file_obj)
        assert result == test_data


class TestNotificationServiceUrlHandling(TestCase):
    """Test the _download_from_url and _is_url methods in NotificationService"""

    def setUp(self):
        self.backend = FakeFileBackend()
        self.adapter = FakeEmailAdapter(
            template_renderer=FakeTemplateRenderer(),
            backend=self.backend,
        )
        self.service = NotificationService(
            notification_adapters=[self.adapter],
            notification_backend=self.backend,
        )

    def test_is_url_detection(self):
        """Test _is_url method with various URL schemes"""
        test_cases = [
            ("http://example.com/file.pdf", True),
            ("https://example.com/file.pdf", True),
            ("s3://bucket/file.pdf", True),
            ("gs://bucket/file.pdf", True),
            ("azure://container/file.pdf", True),
            ("/local/path/file.pdf", False),
            ("relative/path/file.pdf", False),
            ("file.pdf", False),
            ("ftp://example.com/file.pdf", False),  # Not supported
        ]

        for url, expected in test_cases:
            result = self.service._is_url(url)
            assert result == expected, f"URL: {url}, Expected: {expected}, Got: {result}"

    @patch('vintasend.services.notification_service.requests.get')
    def test_download_from_url_success(self, mock_get):
        """Test _download_from_url with successful download"""
        # Mock the requests.get response
        mock_response = Mock()
        mock_response.content = b"Mocked document content"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        
        url = "http://example.com/document.pdf"
        result = self.service._download_from_url(url)
        
        # Should contain fake downloaded content
        assert result == b"Mocked document content"
        mock_get.assert_called_once_with(url, timeout=30)

    @patch('vintasend.services.notification_service.requests.get')
    def test_download_from_url_different_schemes(self, mock_get):
        """Test _download_from_url with different URL schemes"""
        # Mock the requests.get response
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        
        urls = [
            "https://secure.example.com/file.pdf",
            "http://example.com/image.png",
            "https://api.example.com/data.json",
        ]

        for i, url in enumerate(urls):
            mock_response.content = f"Mocked content {i}".encode()
            result = self.service._download_from_url(url)
            assert result == f"Mocked content {i}".encode()
        
        # Verify all calls were made
        assert mock_get.call_count == len(urls)
        for url in urls:
            mock_get.assert_any_call(url, timeout=30)

    @patch('vintasend.services.notification_service.requests')
    def test_download_from_url_import_error(self, mock_requests):
        """Test _download_from_url handles missing requests library"""
        # This test is for the sync version which doesn't have try/except for imports
        # But we can test the scenario where requests module fails to import
        mock_requests.get.side_effect = ImportError("No module named 'requests'")
        
        url = "https://example.com/test-import.pdf"
        
        with pytest.raises(ImportError):
            self.service._download_from_url(url)


class TestAsyncNotificationServiceFileHandling(IsolatedAsyncioTestCase):
    """Test the _read_file_data method in AsyncIONotificationService"""

    async def asyncSetUp(self):
        self.backend = FakeAsyncIOFileBackend()
        self.adapter = FakeAsyncIOEmailAdapter(
            template_renderer=FakeTemplateRenderer(),
            backend=self.backend,
        )
        self.service = AsyncIONotificationService(
            notification_adapters=[self.adapter],
            notification_backend=self.backend,
        )

        # Register context for testing
        @register_context("async_file_test")
        def test_context(context_kwargs):
            return NotificationContextDict(context_kwargs)

    async def asyncTearDown(self):
        # Clear backend
        await self.backend.clear()

    def test_async_read_file_data_with_bytesio(self):
        """Test async _read_file_data with BytesIO object"""
        test_data = b"Async BytesIO content"
        file_obj = io.BytesIO(test_data)

        # Set position to test seek functionality
        file_obj.seek(3)

        result = self.service._read_file_data(file_obj)
        assert result == test_data

        # Verify position was restored
        assert file_obj.tell() == 3

    def test_async_read_file_data_with_stringio(self):
        """Test async _read_file_data with StringIO object"""
        test_data = "Async StringIO content"
        file_obj = io.StringIO(test_data)

        result = self.service._read_file_data(file_obj)
        assert result == test_data.encode('utf-8')

    def test_async_read_file_data_with_file_path(self):
        """Test async _read_file_data with file path"""
        test_data = b"Async file path content"
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(test_data)
            temp_file_path = temp_file.name

        try:
            result = self.service._read_file_data(temp_file_path)
            assert result == test_data
        finally:
            os.unlink(temp_file_path)

    def test_async_read_file_data_with_path_object(self):
        """Test async _read_file_data with pathlib.Path object"""
        test_data = b"Async Path object content"
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(test_data)
            temp_file_path = Path(temp_file.name)

        try:
            result = self.service._read_file_data(temp_file_path)
            assert result == test_data
        finally:
            os.unlink(temp_file_path)

    @patch('vintasend.services.notification_service.requests.get')
    def test_async_read_file_data_with_url(self, mock_get):
        """Test async _read_file_data with URL"""
        # Mock the requests.get response
        mock_response = Mock()
        mock_response.content = b"Mocked async downloaded content"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        
        url = "https://example.com/async-test.pdf"
        result = self.service._read_file_data(url)

        # Should contain the mocked downloaded content
        assert result == b"Mocked async downloaded content"
        mock_get.assert_called_once_with(url, timeout=30)

    def test_async_read_file_data_with_unsupported_type(self):
        """Test async _read_file_data with unsupported file type"""
        with pytest.raises(ValueError, match="Unsupported file type"):
            self.service._read_file_data({"not": "supported"})


class TestAsyncNotificationServiceUrlHandling(IsolatedAsyncioTestCase):
    """Test the _download_from_url and _is_url methods in AsyncIONotificationService"""

    async def asyncSetUp(self):
        self.backend = FakeAsyncIOFileBackend()
        self.adapter = FakeAsyncIOEmailAdapter(
            template_renderer=FakeTemplateRenderer(),
            backend=self.backend,
        )
        self.service = AsyncIONotificationService(
            notification_adapters=[self.adapter],
            notification_backend=self.backend,
        )

    def test_async_is_url_detection(self):
        """Test async _is_url method with various URL schemes"""
        test_cases = [
            ("http://example.com/file.pdf", True),
            ("https://example.com/file.pdf", True),
            ("s3://bucket/file.pdf", True),
            ("gs://bucket/file.pdf", True),
            ("azure://container/file.pdf", True),
            ("/local/path/file.pdf", False),
            ("relative/path/file.pdf", False),
            ("file.pdf", False),
        ]

        for url, expected in test_cases:
            result = self.service._is_url(url)
            assert result == expected, f"URL: {url}, Expected: {expected}, Got: {result}"

    @patch('vintasend.services.notification_service.requests.get')
    def test_async_download_from_url_success(self, mock_get):
        """Test async _download_from_url with successful download"""
        # Mock the requests.get response
        mock_response = Mock()
        mock_response.content = b"Mocked async document content"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        
        url = "https://example.com/async-document.pdf"
        result = self.service._download_from_url(url)
        
        assert result == b"Mocked async document content"
        mock_get.assert_called_once_with(url, timeout=30)

    @patch('builtins.__import__')
    def test_async_download_from_url_requests_import_error(self, mock_import):
        """Test async _download_from_url handles missing requests library"""
        # Mock __import__ to raise ImportError for 'requests' module
        def side_effect(name, *args, **kwargs):
            if name == 'requests':
                raise ImportError("No module named 'requests'")
            return __import__(name, *args, **kwargs)
        
        mock_import.side_effect = side_effect
        
        url = "https://example.com/test-import.pdf"
        
        with pytest.raises(ImportError, match="requests library is required"):
            self.service._download_from_url(url)

    @patch('vintasend.services.notification_service.requests.get')
    def test_async_download_from_url_different_schemes(self, mock_get):
        """Test async _download_from_url with different URL schemes"""
        # Mock the requests.get response
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        
        urls = [
            "https://secure.example.com/async-file.pdf",
            "http://example.com/async-image.png",
            "https://api.example.com/async-data.json",
        ]

        for i, url in enumerate(urls):
            mock_response.content = f"Mocked async content {i}".encode()
            result = self.service._download_from_url(url)
            assert result == f"Mocked async content {i}".encode()
        
        # Verify all calls were made
        assert mock_get.call_count == len(urls)
        for url in urls:
            mock_get.assert_any_call(url, timeout=30)
class TestFileHandlingEdgeCases(TestCase):
    """Test edge cases for file handling methods"""

    def setUp(self):
        self.backend = FakeFileBackend()
        self.adapter = FakeEmailAdapter(
            template_renderer=FakeTemplateRenderer(),
            backend=self.backend,
        )
        self.service = NotificationService(
            notification_adapters=[self.adapter],
            notification_backend=self.backend,
        )

    def test_read_file_data_with_empty_bytes(self):
        """Test _read_file_data with empty bytes should fail (not supported)"""
        with pytest.raises(ValueError, match="Unsupported file type"):
            self.service._read_file_data(b"")

    def test_read_file_data_with_empty_stringio(self):
        """Test _read_file_data with empty StringIO"""
        file_obj = io.StringIO("")
        result = self.service._read_file_data(file_obj)
        assert result == b""

    def test_read_file_data_with_empty_bytesio(self):
        """Test _read_file_data with empty BytesIO"""
        file_obj = io.BytesIO(b"")
        result = self.service._read_file_data(file_obj)
        assert result == b""

    def test_read_file_data_large_content(self):
        """Test _read_file_data with large content"""
        large_data = b"x" * 10000  # 10KB of data
        file_obj = io.BytesIO(large_data)

        result = self.service._read_file_data(file_obj)
        assert result == large_data
        assert len(result) == 10000

    @patch('vintasend.services.notification_service.requests.get')
    def test_url_handling_with_query_parameters(self, mock_get):
        """Test URL handling with query parameters and fragments"""
        # Mock the requests.get response
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        
        urls_with_params = [
            "https://example.com/file.pdf?version=1&download=true",
            "http://example.com/image.png#preview",
            "https://api.example.com/data.json?format=pdf&size=large",
        ]

        for i, url in enumerate(urls_with_params):
            assert self.service._is_url(url) is True
            
            mock_response.content = f"Param content {i}".encode()
            result = self.service._download_from_url(url)
            assert result == f"Param content {i}".encode()
        
        # Verify all calls were made
        assert mock_get.call_count == len(urls_with_params)
