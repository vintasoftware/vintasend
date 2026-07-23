"""Attachment storage seam stub.

Subclass ``BaseAttachmentManager`` (and its AsyncIO twin) to own every byte of attachment
storage: uploading a file, reconstructing a handle to a previously stored one, and deleting it.
A backend never reads, writes, or downloads a byte itself -- it persists rows and hands
``StorageIdentifiers`` back to whichever manager was injected.
"""

from typing import TYPE_CHECKING

from vintasend.services.attachment_managers.asyncio_base import AsyncIOBaseAttachmentManager
from vintasend.services.attachment_managers.base import BaseAttachmentManager


if TYPE_CHECKING:
    from vintasend.services.dataclasses import (
        AttachmentFile,
        AttachmentFileRecord,
        FileAttachment,
        StorageIdentifiers,
    )


class ImplementationTemplateAttachmentManager(BaseAttachmentManager):
    """TODO: rename and implement. See ``vintasend/services/attachment_managers/base.py``."""

    def upload_file(
        self,
        file: "FileAttachment",
        filename: str,
        content_type: str | None = None,
    ) -> "AttachmentFileRecord":
        """TODO: implement upload_file — see vintasend/services/attachment_managers/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement upload_file — see "
            "vintasend/services/attachment_managers/base.py for the contract"
        )

    def reconstruct_attachment_file(
        self, storage_identifiers: "StorageIdentifiers"
    ) -> "AttachmentFile":
        """TODO: implement reconstruct_attachment_file — see vintasend/services/attachment_managers/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement reconstruct_attachment_file — see "
            "vintasend/services/attachment_managers/base.py for the contract"
        )

    def delete_file_by_identifiers(self, storage_identifiers: "StorageIdentifiers") -> None:
        """TODO: implement delete_file_by_identifiers — see vintasend/services/attachment_managers/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement delete_file_by_identifiers — see "
            "vintasend/services/attachment_managers/base.py for the contract"
        )


class ImplementationTemplateAsyncIOAttachmentManager(AsyncIOBaseAttachmentManager):
    """TODO: rename and implement. See ``vintasend/services/attachment_managers/asyncio_base.py``."""

    async def upload_file(
        self,
        file: "FileAttachment",
        filename: str,
        content_type: str | None = None,
    ) -> "AttachmentFileRecord":
        """TODO: implement upload_file — see vintasend/services/attachment_managers/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement upload_file — see "
            "vintasend/services/attachment_managers/asyncio_base.py for the contract"
        )

    def reconstruct_attachment_file(
        self, storage_identifiers: "StorageIdentifiers"
    ) -> "AttachmentFile":
        """TODO: implement reconstruct_attachment_file — see vintasend/services/attachment_managers/asyncio_base.py for the contract.

        Stays synchronous even here: it performs no I/O, only builds a lazy handle.
        """
        raise NotImplementedError(
            "TODO: implement reconstruct_attachment_file — see "
            "vintasend/services/attachment_managers/asyncio_base.py for the contract"
        )

    async def delete_file_by_identifiers(self, storage_identifiers: "StorageIdentifiers") -> None:
        """TODO: implement delete_file_by_identifiers — see vintasend/services/attachment_managers/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement delete_file_by_identifiers — see "
            "vintasend/services/attachment_managers/asyncio_base.py for the contract"
        )
