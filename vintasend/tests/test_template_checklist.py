import importlib
import re
from pathlib import Path
from unittest import TestCase


_README = (
    Path(__file__).resolve().parents[2]
    / "templates"
    / "vintasend-implementation-template"
    / "README.md"
)

# Every ABC the template's per-component checklist documents, and where it lives. Adding a
# class here without also listing it (fully) in the README's checklist fails
# `test_every_readme_class_is_covered_by_this_map` below, so this map cannot silently drift
# out of sync with the doc either.
_ABC_LOCATIONS = {
    "BaseNotificationBackend": "vintasend.services.notification_backends.base",
    "AsyncIOBaseNotificationBackend": "vintasend.services.notification_backends.asyncio_base",
    "BaseNotificationAdapter": "vintasend.services.notification_adapters.base",
    "AsyncIOBaseNotificationAdapter": "vintasend.services.notification_adapters.asyncio_base",
    "BackgroundNotificationAdapter": "vintasend.services.notification_adapters.async_base",
    "AsyncIOBackgroundNotificationAdapter": (
        "vintasend.services.notification_adapters.asyncio_background_base"
    ),
    "BaseNotificationTemplateRenderer": ("vintasend.services.notification_template_renderers.base"),
    "BaseTemplatedEmailRenderer": (
        "vintasend.services.notification_template_renderers.base_templated_email_renderer"
    ),
    "BaseTemplatedSMSRenderer": (
        "vintasend.services.notification_template_renderers.base_templated_sms_renderer"
    ),
    "BaseNotificationQueueService": "vintasend.services.notification_queue_services.base",
    "AsyncIOBaseNotificationQueueService": (
        "vintasend.services.notification_queue_services.asyncio_base"
    ),
    "BaseNotificationReplicationQueueService": (
        "vintasend.services.notification_queue_services.replication_base"
    ),
    "AsyncIOBaseNotificationReplicationQueueService": (
        "vintasend.services.notification_queue_services.asyncio_replication_base"
    ),
    "BaseAttachmentManager": "vintasend.services.attachment_managers.base",
    "AsyncIOBaseAttachmentManager": "vintasend.services.attachment_managers.asyncio_base",
}


def _parse_checklist(readme_text: str) -> dict[str, set[str]]:
    """Parse every ```checklist fenced block into {ClassName: {method_name, ...}}.

    Each checklist line is a bare ``ClassName.method_name``, one per line. This is the format
    documented in the template README as the parse-friendly delimiter for this test.
    """
    by_class: dict[str, set[str]] = {}
    for block in re.findall(r"```checklist\n(.*?)```", readme_text, re.DOTALL):
        for line in block.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            class_name, _, method_name = line.partition(".")
            assert method_name, f"malformed checklist line (expected ClassName.method): {line!r}"
            by_class.setdefault(class_name, set()).add(method_name)
    return by_class


class TemplateChecklistTestCase(TestCase):
    """Guards the template README's per-component checklist against seam drift.

    The checklist names the exact abstract methods a new implementation must override, one
    fenced ```checklist block per seam. If a seam's ABC gains, loses, or renames an abstract
    method, this test fails until the README is updated to match -- the doc cannot silently
    rot out of sync with the ABCs it describes.
    """

    def setUp(self):
        self.readme_text = _README.read_text(encoding="utf-8")
        self.checklist = _parse_checklist(self.readme_text)

    def test_readme_has_at_least_one_checklist_block(self):
        assert self.checklist, "expected at least one ```checklist block in the README"

    def test_every_checklisted_class_is_a_known_abc(self):
        # Guards against a typo'd or renamed class slipping into the README unnoticed.
        unknown = set(self.checklist) - set(_ABC_LOCATIONS)
        assert not unknown, f"README checklist names unknown class(es): {unknown}"

    def test_every_known_abc_is_covered_by_the_readme(self):
        # Guards against a seam's ABC being added to _ABC_LOCATIONS without also documenting
        # it in the README, or a checklist block being deleted from the README by mistake.
        missing = set(_ABC_LOCATIONS) - set(self.checklist)
        assert not missing, f"README checklist is missing class(es): {missing}"

    def test_checklist_methods_match_the_abcs_abstract_methods_exactly(self):
        mismatches = {}
        for class_name, module_path in _ABC_LOCATIONS.items():
            module = importlib.import_module(module_path)
            abc = getattr(module, class_name)
            real_methods = set(abc.__abstractmethods__)
            documented_methods = self.checklist[class_name]
            if real_methods != documented_methods:
                mismatches[class_name] = {
                    "missing_from_readme": sorted(real_methods - documented_methods),
                    "no_longer_abstract": sorted(documented_methods - real_methods),
                }
        assert not mismatches, mismatches
