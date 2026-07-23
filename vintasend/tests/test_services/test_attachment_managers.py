"""Unit tests for the attachment manager seam: `BaseAttachmentManager`,
`AsyncIOBaseAttachmentManager`, and the in-memory fakes.

`ReadFileDataTestCase`, `IsUrlTestCase`, and `DownloadFromUrlTestCase` used to live in
`test_service_utils.py`, testing `read_file_data` / `is_url` / `download_from_url` as
standalone `service_utils` functions. That logic moved onto
`BaseAttachmentManager.file_to_bytes`; these assertions moved with it.
"""

import dataclasses
import datetime
import io
import os
import sys
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch

import pytest

from vintasend.exceptions import UnsupportedAttachmentFileTypeError
from vintasend.services.attachment_managers import base as sync_base
from vintasend.services.attachment_managers.stubs.fake_attachment_manager import (
    FakeAsyncIOAttachmentManager,
    FakeAttachmentManager,
)
from vintasend.services.dataclasses import (
    AttachmentFileRecord,
    NotificationAttachment,
    NotificationAttachmentReference,
    StoredAttachment,
    is_attachment_reference,
)


class ReadFileDataTestCase(TestCase):
    """Exercises `file_to_bytes` over the same inputs `read_file_data` used to cover."""

    def setUp(self):
        self.manager = FakeAttachmentManager()

    def test_read_file_data_with_bytesio(self):
        test_data = b"BytesIO content"
        file_obj = io.BytesIO(test_data)
        file_obj.seek(5)

        result = self.manager.file_to_bytes(file_obj)

        assert result == test_data
        assert file_obj.tell() == 5

    def test_read_file_data_with_stringio(self):
        test_data = "StringIO content"
        file_obj = io.StringIO(test_data)

        result = self.manager.file_to_bytes(file_obj)

        assert result == test_data.encode("utf-8")

    def test_read_file_data_with_file_path_string(self):
        test_data = b"File path content"
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(test_data)
            temp_file_path = temp_file.name

        try:
            result = self.manager.file_to_bytes(temp_file_path)
            assert result == test_data
        finally:
            os.unlink(temp_file_path)

    def test_read_file_data_with_path_object(self):
        test_data = b"Path object content"
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(test_data)
            temp_file_path = Path(temp_file.name)

        try:
            result = self.manager.file_to_bytes(temp_file_path)
            assert result == test_data
        finally:
            os.unlink(temp_file_path)

    def test_read_file_data_with_bytes_is_supported_by_file_to_bytes(self):
        """Unlike the old `read_file_data`, `file_to_bytes` accepts raw bytes directly --
        it is part of `FileAttachment` and there is nothing to "read"."""
        result = self.manager.file_to_bytes(b"raw bytes content")

        assert result == b"raw bytes content"

    def test_read_file_data_with_unsupported_type(self):
        with pytest.raises(UnsupportedAttachmentFileTypeError, match="Unsupported file type"):
            self.manager.file_to_bytes(12345)

    def test_read_file_data_with_nonexistent_file(self):
        with pytest.raises(FileNotFoundError):
            self.manager.file_to_bytes("/path/that/does/not/exist.txt")

    @patch("requests.get")
    def test_read_file_data_with_url(self, mock_get):
        mock_response = Mock()
        mock_response.content = b"Mocked downloaded content"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        url = "http://example.com/test.pdf"
        result = self.manager.file_to_bytes(url)

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

        result = self.manager.file_to_bytes(file_obj)

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

        result = self.manager.file_to_bytes(file_obj)

        assert result == test_data

    def test_read_file_data_with_empty_stringio(self):
        assert self.manager.file_to_bytes(io.StringIO("")) == b""

    def test_read_file_data_with_empty_bytesio(self):
        assert self.manager.file_to_bytes(io.BytesIO(b"")) == b""

    def test_read_file_data_large_content(self):
        large_data = b"x" * 10000
        result = self.manager.file_to_bytes(io.BytesIO(large_data))
        assert result == large_data
        assert len(result) == 10000


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
            result = sync_base.is_url(url)
            assert result == expected, f"URL: {url}, Expected: {expected}, Got: {result}"

    def test_is_url_with_query_parameters_and_fragments(self):
        # A URL keeps being a URL when it carries a query string or fragment.
        for url in (
            "https://example.com/file.pdf?version=1&download=true",
            "http://example.com/image.png#preview",
            "https://api.example.com/data.json?format=pdf&size=large",
        ):
            assert sync_base.is_url(url) is True


class DownloadFromUrlTestCase(TestCase):
    @patch("requests.get")
    def test_download_from_url_success(self, mock_get):
        mock_response = Mock()
        mock_response.content = b"Mocked document content"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        url = "http://example.com/document.pdf"
        result = sync_base.download_from_url(url)

        assert result == b"Mocked document content"
        mock_get.assert_called_once_with(url, timeout=30)

    @patch("requests.get")
    def test_download_from_url_handles_multiple_schemes(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        urls = [
            "https://secure.example.com/file.pdf",
            "http://example.com/image.png",
            "https://api.example.com/data.json?format=pdf",
        ]
        for i, url in enumerate(urls):
            mock_response.content = f"Mocked content {i}".encode()
            assert sync_base.download_from_url(url) == f"Mocked content {i}".encode()

        assert mock_get.call_count == len(urls)
        for url in urls:
            mock_get.assert_any_call(url, timeout=30)

    @patch("requests.get")
    def test_download_from_url_propagates_errors_from_requests(self, mock_get):
        # An error raised while downloading is not swallowed.
        mock_get.side_effect = ImportError("boom")

        with pytest.raises(ImportError):
            sync_base.download_from_url("https://example.com/x.pdf")

    def test_download_from_url_raises_friendly_import_error_when_requests_missing(self):
        with patch.dict(sys.modules, {"requests": None}):
            with pytest.raises(ImportError, match="requests library is required"):
                sync_base.download_from_url("https://example.com/test-import.pdf")


class CalculateChecksumTestCase(TestCase):
    def setUp(self):
        self.manager = FakeAttachmentManager()

    def test_calculate_checksum_is_stable_sha256(self):
        import hashlib

        data = b"some file content"

        result = self.manager.calculate_checksum(data)

        assert result == hashlib.sha256(data).hexdigest()
        # Calling it again with the same bytes must produce the same digest.
        assert result == self.manager.calculate_checksum(data)

    def test_calculate_checksum_differs_for_different_data(self):
        assert self.manager.calculate_checksum(b"a") != self.manager.calculate_checksum(b"b")


class DetectContentTypeTestCase(TestCase):
    def setUp(self):
        self.manager = FakeAttachmentManager()

    def test_detect_content_type_known_extension(self):
        assert self.manager.detect_content_type("file.txt") == "text/plain"

    def test_detect_content_type_falls_back_to_octet_stream(self):
        assert self.manager.detect_content_type("file.unknownext") == "application/octet-stream"

    def test_detect_content_type_no_extension_falls_back(self):
        assert self.manager.detect_content_type("noextension") == "application/octet-stream"


class FakeAttachmentManagerTestCase(TestCase):
    """Round trip through the sync in-memory fake."""

    def setUp(self):
        self.manager = FakeAttachmentManager()

    def test_upload_file_returns_attachment_file_record_with_sha256_checksum(self):
        record = self.manager.upload_file(b"x", "a.txt")

        assert isinstance(record, AttachmentFileRecord)
        assert record.filename == "a.txt"
        assert record.content_type == "text/plain"
        assert record.size == 1
        assert record.checksum == self.manager.calculate_checksum(b"x")
        assert record.storage_identifiers["id"]

    def test_reconstruct_attachment_file_reads_back_uploaded_bytes(self):
        record = self.manager.upload_file(b"x", "a.txt")

        attachment_file = self.manager.reconstruct_attachment_file(record.storage_identifiers)

        assert attachment_file.read() == b"x"

    def test_reconstruct_attachment_file_stream_reads_back_uploaded_bytes(self):
        record = self.manager.upload_file(b"hello world", "a.txt")

        attachment_file = self.manager.reconstruct_attachment_file(record.storage_identifiers)

        assert attachment_file.stream().read() == b"hello world"

    def test_reconstruct_attachment_file_url_is_non_empty(self):
        record = self.manager.upload_file(b"x", "a.txt")

        attachment_file = self.manager.reconstruct_attachment_file(record.storage_identifiers)

        assert attachment_file.url()

    def test_upload_file_honors_explicit_content_type(self):
        record = self.manager.upload_file(b"x", "a.bin", content_type="application/custom")

        assert record.content_type == "application/custom"

    def test_delete_file_by_identifiers_removes_the_file(self):
        record = self.manager.upload_file(b"x", "a.txt")

        self.manager.delete_file_by_identifiers(record.storage_identifiers)

        with pytest.raises(FileNotFoundError):
            self.manager.reconstruct_attachment_file(record.storage_identifiers).read()

    def test_delete_file_by_identifiers_never_raises_for_unknown_id(self):
        # Never raising on a missing id is deliberate -- deletion is idempotent.
        self.manager.delete_file_by_identifiers({"id": "does-not-exist"})

    def test_upload_file_accepts_path_input(self):
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(b"path input content")
            temp_file_path = Path(temp_file.name)

        try:
            record = self.manager.upload_file(temp_file_path, "path.txt")
            attachment_file = self.manager.reconstruct_attachment_file(record.storage_identifiers)
            assert attachment_file.read() == b"path input content"
        finally:
            os.unlink(temp_file_path)

    def test_two_uploads_produce_independent_records(self):
        record_one = self.manager.upload_file(b"one", "one.txt")
        record_two = self.manager.upload_file(b"two", "two.txt")

        assert record_one.id != record_two.id
        assert record_one.storage_identifiers != record_two.storage_identifiers


class FakeAsyncIOAttachmentManagerTestCase(IsolatedAsyncioTestCase):
    """Round trip through the AsyncIO in-memory fake."""

    def setUp(self):
        self.manager = FakeAsyncIOAttachmentManager()

    async def test_upload_file_returns_attachment_file_record_with_sha256_checksum(self):
        record = await self.manager.upload_file(b"x", "a.txt")

        assert isinstance(record, AttachmentFileRecord)
        assert record.checksum == self.manager.calculate_checksum(b"x")
        assert record.storage_identifiers["id"]

    async def test_reconstruct_attachment_file_stays_sync_and_reads_back_uploaded_bytes(self):
        record = await self.manager.upload_file(b"x", "a.txt")

        # `reconstruct_attachment_file` performs no I/O, so it is called without `await`.
        attachment_file = self.manager.reconstruct_attachment_file(record.storage_identifiers)

        assert attachment_file.read() == b"x"

    async def test_delete_file_by_identifiers_removes_the_file(self):
        record = await self.manager.upload_file(b"x", "a.txt")

        await self.manager.delete_file_by_identifiers(record.storage_identifiers)

        with pytest.raises(FileNotFoundError):
            self.manager.reconstruct_attachment_file(record.storage_identifiers).read()

    async def test_upload_file_honors_explicit_content_type(self):
        record = await self.manager.upload_file(b"x", "a.bin", content_type="application/custom")

        assert record.content_type == "application/custom"

    async def test_upload_file_accepts_path_input(self):
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(b"path input content")
            temp_file_path = Path(temp_file.name)

        try:
            record = await self.manager.upload_file(temp_file_path, "path.txt")
            attachment_file = self.manager.reconstruct_attachment_file(record.storage_identifiers)
            assert attachment_file.read() == b"path input content"
        finally:
            os.unlink(temp_file_path)


class StoredAttachmentStorageMetadataAliasTestCase(TestCase):
    """`storage_metadata` is a deprecated alias for `storage_identifiers`, kept for
    backwards compatibility -- `MIGRATION_TO_1.0.0.md` documents `storage_metadata=`
    as the downstream constructor kwarg.
    """

    def _make_stored_attachment(self, **kwargs) -> StoredAttachment:
        return StoredAttachment(
            id="1",
            filename="a.txt",
            content_type="text/plain",
            size=1,
            checksum="c",
            created_at=datetime.datetime.now(tz=datetime.timezone.utc),
            file=Mock(),
            **kwargs,
        )

    def test_storage_metadata_kwarg_populates_storage_identifiers(self):
        sa = self._make_stored_attachment(storage_metadata={"a": 1})

        assert sa.storage_identifiers == {"a": 1}
        assert sa.storage_metadata == {"a": 1}

    def test_storage_identifiers_kwarg_populates_storage_metadata(self):
        sa = self._make_stored_attachment(storage_identifiers={"id": "x"})

        assert sa.storage_metadata == {"id": "x"}

    def test_asdict_includes_storage_metadata_key(self):
        sa = self._make_stored_attachment(storage_identifiers={"id": "x"})

        assert dataclasses.asdict(sa)["storage_metadata"] == {"id": "x"}


class IsAttachmentReferenceTestCase(TestCase):
    def test_upload_is_not_a_reference(self):
        attachment = NotificationAttachment(file=b"x", filename="a.txt")

        assert is_attachment_reference(attachment) is False

    def test_reference_is_a_reference(self):
        reference = NotificationAttachmentReference(file_id="some-file-id")

        assert is_attachment_reference(reference) is True
