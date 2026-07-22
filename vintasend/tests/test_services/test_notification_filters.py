import datetime
import tempfile
import uuid
from unittest import IsolatedAsyncioTestCase, TestCase

from freezegun import freeze_time

from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.exceptions import NotificationResendError
from vintasend.services.dataclasses import (
    Notification,
    NotificationAttachment,
    NotificationContextDict,
    OneOffNotification,
)
from vintasend.services.notification_adapters.stubs.fake_adapter import (
    FakeAsyncIOEmailAdapter,
    FakeEmailAdapter,
)
from vintasend.services.notification_backends.filters import (
    DEFAULT_BACKEND_FILTER_CAPABILITIES,
    is_field_filter,
    is_string_filter_lookup,
    matches_filter,
)
from vintasend.services.notification_backends.stubs.fake_backend import (
    FakeAsyncIOFileBackend,
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


UTC = datetime.timezone.utc


def _dt(day: int, hour: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 1, day, hour, tzinfo=UTC)


def _build_notification(
    notification_id: str,
    *,
    user_id: int | str | uuid.UUID = 1,
    notification_type: str = NotificationTypes.EMAIL.value,
    status: str = NotificationStatus.PENDING_SEND.value,
    body_template: str = "welcome_body",
    subject_template: str = "welcome_subject",
    context_name: str = "welcome_context",
    adapter_used: str | None = None,
    tenant: str | None = None,
    send_after: datetime.datetime | None = None,
    created: datetime.datetime | None = None,
    modified: datetime.datetime | None = None,
    sent_at: datetime.datetime | None = None,
    read_at: datetime.datetime | None = None,
) -> Notification:
    return Notification(
        id=notification_id,
        user_id=user_id,
        notification_type=notification_type,
        title="Title",
        body_template=body_template,
        context_name=context_name,
        context_kwargs={},
        send_after=send_after,
        subject_template=subject_template,
        preheader_template="preheader",
        status=status,
        adapter_used=adapter_used,
        tenant=tenant,
        created=created,
        modified=modified,
        sent_at=sent_at,
        read_at=read_at,
    )


class MatchesFilterUnitTestCase(TestCase):
    """Direct tests of the pure evaluator, independent of any backend or pagination."""

    def setUp(self):
        self.email = _build_notification(
            "n-email",
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            status=NotificationStatus.SENT.value,
            body_template="Welcome Aboard",
            adapter_used="email.adapter",
            tenant="acme",
            send_after=_dt(10),
            created=_dt(1),
            sent_at=_dt(11),
            read_at=_dt(12),
        )
        self.sms = _build_notification(
            "n-sms",
            user_id=2,
            notification_type=NotificationTypes.SMS.value,
            status=NotificationStatus.PENDING_SEND.value,
            body_template="Reminder text",
            adapter_used=None,
            tenant=None,
            send_after=_dt(20),
            created=_dt(2),
            sent_at=None,
            read_at=None,
        )
        self.one_off = OneOffNotification(
            id="n-oneoff",
            email_or_phone="user@example.com",
            first_name="A",
            last_name="B",
            notification_type=NotificationTypes.EMAIL.value,
            title="Title",
            body_template="One off body",
            context_name="welcome_context",
            context_kwargs={},
            send_after=None,
            subject_template="welcome_subject",
            preheader_template="preheader",
            status=NotificationStatus.SENT.value,
            tenant="acme",
            created=_dt(3),
        )

    def test_empty_filter_matches_everything(self):
        for notification in (self.email, self.sms, self.one_off):
            assert matches_filter(notification, {}) is True

    def test_status_scalar(self):
        assert matches_filter(self.email, {"status": NotificationStatus.SENT.value}) is True
        assert matches_filter(self.sms, {"status": NotificationStatus.SENT.value}) is False

    def test_status_accepts_enum_member(self):
        assert matches_filter(self.email, {"status": NotificationStatus.SENT}) is True

    def test_status_list_membership(self):
        f = {"status": [NotificationStatus.SENT.value, NotificationStatus.READ.value]}
        assert matches_filter(self.email, f) is True
        assert matches_filter(self.sms, f) is False

    def test_notification_type_field(self):
        assert matches_filter(self.email, {"notification_type": NotificationTypes.EMAIL.value})
        assert not matches_filter(self.sms, {"notification_type": NotificationTypes.EMAIL.value})

    def test_user_id_field_scalar_and_str_coercion(self):
        assert matches_filter(self.email, {"user_id": 1}) is True
        assert matches_filter(self.email, {"user_id": "1"}) is True
        assert matches_filter(self.sms, {"user_id": 1}) is False

    def test_adapter_used_membership(self):
        assert matches_filter(self.email, {"adapter_used": "email.adapter"}) is True
        assert matches_filter(self.email, {"adapter_used": ["email.adapter", "x"]}) is True
        assert matches_filter(self.sms, {"adapter_used": "email.adapter"}) is False

    def test_tenant_field(self):
        assert matches_filter(self.email, {"tenant": "acme"}) is True
        assert matches_filter(self.email, {"tenant": ["acme", "other"]}) is True
        assert matches_filter(self.sms, {"tenant": "acme"}) is False

    def test_implicit_and_across_keys(self):
        assert matches_filter(
            self.email,
            {"status": NotificationStatus.SENT.value, "tenant": "acme"},
        )
        assert not matches_filter(
            self.email,
            {"status": NotificationStatus.SENT.value, "tenant": "nope"},
        )

    # --- string lookups -----------------------------------------------------

    def test_bare_string_is_case_sensitive_exact(self):
        assert matches_filter(self.email, {"body_template": "Welcome Aboard"}) is True
        assert matches_filter(self.email, {"body_template": "welcome aboard"}) is False

    def test_exact_lookup_both_case_modes(self):
        assert matches_filter(
            self.email, {"body_template": {"lookup": "exact", "value": "Welcome Aboard"}}
        )
        assert not matches_filter(
            self.email, {"body_template": {"lookup": "exact", "value": "welcome aboard"}}
        )
        assert matches_filter(
            self.email,
            {
                "body_template": {
                    "lookup": "exact",
                    "value": "welcome aboard",
                    "case_sensitive": False,
                }
            },
        )

    def test_starts_with_both_case_modes(self):
        assert matches_filter(
            self.email, {"body_template": {"lookup": "starts_with", "value": "Welcome"}}
        )
        assert not matches_filter(
            self.email, {"body_template": {"lookup": "starts_with", "value": "welcome"}}
        )
        assert matches_filter(
            self.email,
            {
                "body_template": {
                    "lookup": "starts_with",
                    "value": "welcome",
                    "case_sensitive": False,
                }
            },
        )

    def test_ends_with_both_case_modes(self):
        assert matches_filter(
            self.email, {"body_template": {"lookup": "ends_with", "value": "Aboard"}}
        )
        assert not matches_filter(
            self.email, {"body_template": {"lookup": "ends_with", "value": "aboard"}}
        )
        assert matches_filter(
            self.email,
            {"body_template": {"lookup": "ends_with", "value": "aboard", "case_sensitive": False}},
        )

    def test_includes_both_case_modes(self):
        assert matches_filter(
            self.email, {"body_template": {"lookup": "includes", "value": "come Ab"}}
        )
        assert not matches_filter(
            self.email, {"body_template": {"lookup": "includes", "value": "come ab"}}
        )
        assert matches_filter(
            self.email,
            {"body_template": {"lookup": "includes", "value": "come ab", "case_sensitive": False}},
        )

    def test_subject_and_context_string_fields(self):
        assert matches_filter(self.email, {"subject_template": "welcome_subject"})
        assert matches_filter(self.email, {"context_name": "welcome_context"})

    # --- ranges -------------------------------------------------------------

    def test_range_from_only(self):
        assert matches_filter(self.email, {"send_after_range": {"from": _dt(10)}}) is True
        assert matches_filter(self.email, {"send_after_range": {"from": _dt(11)}}) is False

    def test_range_to_only(self):
        assert matches_filter(self.email, {"send_after_range": {"to": _dt(10)}}) is True
        assert matches_filter(self.email, {"send_after_range": {"to": _dt(9)}}) is False

    def test_range_both_bounds_inclusive(self):
        # send_after is exactly _dt(10); inclusive bounds must include the boundary on both ends.
        assert matches_filter(self.email, {"send_after_range": {"from": _dt(10), "to": _dt(10)}})
        assert matches_filter(self.email, {"send_after_range": {"from": _dt(5), "to": _dt(10)}})
        assert matches_filter(self.email, {"send_after_range": {"from": _dt(10), "to": _dt(15)}})
        assert not matches_filter(
            self.email, {"send_after_range": {"from": _dt(11), "to": _dt(15)}}
        )

    def test_created_range_maps_to_created(self):
        assert matches_filter(self.email, {"created_at_range": {"from": _dt(1), "to": _dt(1)}})
        assert matches_filter(self.sms, {"created_at_range": {"to": _dt(1)}}) is False

    def test_sent_at_and_read_at_ranges(self):
        assert matches_filter(self.email, {"sent_at_range": {"from": _dt(11), "to": _dt(11)}})
        assert matches_filter(self.email, {"read_at_range": {"from": _dt(12), "to": _dt(12)}})

    def test_range_on_none_value_does_not_match(self):
        # sms has no sent_at/read_at
        assert matches_filter(self.sms, {"sent_at_range": {"from": _dt(1)}}) is False
        assert matches_filter(self.sms, {"read_at_range": {"to": _dt(30)}}) is False

    # --- logical operators --------------------------------------------------

    def test_and_operator(self):
        f = {"and": [{"status": NotificationStatus.SENT.value}, {"tenant": "acme"}]}
        assert matches_filter(self.email, f) is True
        assert matches_filter(self.sms, f) is False

    def test_or_operator(self):
        f = {"or": [{"user_id": 1}, {"user_id": 2}]}
        assert matches_filter(self.email, f) is True
        assert matches_filter(self.sms, f) is True
        assert matches_filter(self.one_off, f) is False

    def test_not_operator(self):
        assert matches_filter(self.email, {"not": {"tenant": "acme"}}) is False
        assert matches_filter(self.sms, {"not": {"tenant": "acme"}}) is True

    def test_not_wrapping_or(self):
        f = {"not": {"or": [{"user_id": 1}, {"user_id": 2}]}}
        assert matches_filter(self.email, f) is False
        assert matches_filter(self.sms, f) is False
        assert matches_filter(self.one_off, f) is True

    def test_arbitrary_nesting(self):
        f = {
            "and": [
                {"notification_type": NotificationTypes.EMAIL.value},
                {"or": [{"tenant": "acme"}, {"not": {"status": NotificationStatus.SENT.value}}]},
            ]
        }
        assert matches_filter(self.email, f) is True
        assert matches_filter(self.one_off, f) is True  # email + acme
        assert matches_filter(self.sms, f) is False  # not email

    # --- NULL handling under negation --------------------------------------

    def test_negation_includes_none_rows_for_nullable_fields(self):
        # sms.tenant is None; a positive tenant filter is False, so negation includes it.
        assert matches_filter(self.sms, {"not": {"tenant": "acme"}}) is True
        assert matches_filter(self.sms, {"not": {"adapter_used": ["email.adapter"]}}) is True
        assert matches_filter(self.sms, {"not": {"sent_at_range": {"from": _dt(1)}}}) is True

    # --- discriminators -----------------------------------------------------

    def test_is_field_filter(self):
        assert is_field_filter({"status": NotificationStatus.SENT.value}) is True
        assert is_field_filter({}) is True
        assert is_field_filter({"and": []}) is False
        assert is_field_filter({"or": []}) is False
        assert is_field_filter({"not": {}}) is False

    def test_is_string_filter_lookup(self):
        assert is_string_filter_lookup({"lookup": "exact", "value": "x"}) is True
        assert is_string_filter_lookup("bare string") is False


class FilterNotificationsBackendTestCase(TestCase):
    """Exercise ``filter_notifications`` on the sync fake, covering ordering and pagination."""

    def setUp(self):
        self.database_file_name = tempfile.mktemp(suffix=".json")
        self.backend = FakeFileBackend(database_file_name=self.database_file_name)

    def tearDown(self):
        self.backend.clear()

    def _seed(self, notifications: list[Notification]) -> None:
        self.backend.notifications = list(notifications)

    def test_empty_filter_returns_all(self):
        self._seed([_build_notification("a"), _build_notification("b")])
        assert len(self.backend.filter_notifications({}, 1, 10)) == 2

    def test_scalar_vs_list_membership(self):
        self._seed(
            [
                _build_notification("a", tenant="acme"),
                _build_notification("b", tenant="beta"),
                _build_notification("c", tenant="gamma"),
            ]
        )
        scalar = {n.id for n in self.backend.filter_notifications({"tenant": "acme"}, 1, 10)}
        assert scalar == {"a"}
        lst = {n.id for n in self.backend.filter_notifications({"tenant": ["acme", "beta"]}, 1, 10)}
        assert lst == {"a", "b"}

    def test_order_by_each_field_both_directions(self):
        n1 = _build_notification(
            "n1", send_after=_dt(1), sent_at=_dt(1), read_at=_dt(1), created=_dt(1), modified=_dt(1)
        )
        n2 = _build_notification(
            "n2", send_after=_dt(2), sent_at=_dt(2), read_at=_dt(2), created=_dt(2), modified=_dt(2)
        )
        n3 = _build_notification(
            "n3", send_after=_dt(3), sent_at=_dt(3), read_at=_dt(3), created=_dt(3), modified=_dt(3)
        )
        self._seed([n2, n3, n1])
        for field in ("send_after", "sent_at", "read_at", "created_at", "updated_at"):
            asc = [
                n.id
                for n in self.backend.filter_notifications(
                    {}, 1, 10, order_by={"field": field, "direction": "asc"}
                )
            ]
            assert asc == ["n1", "n2", "n3"], field
            desc = [
                n.id
                for n in self.backend.filter_notifications(
                    {}, 1, 10, order_by={"field": field, "direction": "desc"}
                )
            ]
            assert desc == ["n3", "n2", "n1"], field

    def test_order_by_tolerates_none_values(self):
        n1 = _build_notification("n1", sent_at=None)
        n2 = _build_notification("n2", sent_at=_dt(2))
        self._seed([n1, n2])
        # Must not raise despite the None sent_at.
        result = self.backend.filter_notifications(
            {}, 1, 10, order_by={"field": "sent_at", "direction": "asc"}
        )
        assert {n.id for n in result} == {"n1", "n2"}

    def test_pagination_sweep_with_shared_created_returns_every_row_once(self):
        # Every record shares the same ``created`` value, so the primary sort key ties on all of
        # them. The mandatory ``id`` tiebreaker is what keeps offset pagination from dropping or
        # duplicating rows. Insert in a scrambled order so a missing tiebreaker (falling back to
        # insertion order) would produce an order different from the asserted id-sorted one.
        shared = _dt(5)
        ids = [f"id-{i:02d}" for i in range(10)]
        scrambled = [ids[i] for i in (3, 7, 0, 9, 1, 5, 2, 8, 4, 6)]
        self._seed([_build_notification(i, created=shared) for i in scrambled])

        order_by = {"field": "created_at", "direction": "asc"}
        page_size = 3
        swept: list[str] = []
        page = 1
        while True:
            batch = self.backend.filter_notifications({}, page, page_size, order_by=order_by)
            if not batch:
                break
            swept.extend(n.id for n in batch)
            page += 1

        # No drops, no duplicates.
        assert sorted(swept) == ids
        assert len(swept) == len(set(swept)) == len(ids)
        # And the tiebreaker orders equal-``created`` rows by id ascending -- this is the part
        # that fails if the ``id`` tiebreaker is dropped.
        assert swept == ids

    def test_count_notifications(self):
        self._seed(
            [
                _build_notification("a", tenant="acme"),
                _build_notification("b", tenant="acme"),
                _build_notification("c", tenant="beta"),
            ]
        )
        assert self.backend.count_notifications({}) == 3
        assert self.backend.count_notifications({"tenant": "acme"}) == 2

    def test_get_filter_capabilities_is_empty(self):
        assert self.backend.get_filter_capabilities() == {}

    def test_pagination_sweep_with_shared_created_returns_every_row_once_descending(self):
        # Same setup as the ascending sweep above, but pinning that the ``id`` tiebreaker is
        # also honored -- in the SAME (descending) direction -- when the primary sort direction
        # is reversed. Phase 2 only exercised the ascending direction exhaustively.
        shared = _dt(5)
        ids = [f"id-{i:02d}" for i in range(10)]
        scrambled = [ids[i] for i in (3, 7, 0, 9, 1, 5, 2, 8, 4, 6)]
        self._seed([_build_notification(i, created=shared) for i in scrambled])

        order_by = {"field": "created_at", "direction": "desc"}
        page_size = 3
        swept: list[str] = []
        page = 1
        while True:
            batch = self.backend.filter_notifications({}, page, page_size, order_by=order_by)
            if not batch:
                break
            swept.extend(n.id for n in batch)
            page += 1

        # No drops, no duplicates.
        assert sorted(swept) == ids
        assert len(swept) == len(set(swept)) == len(ids)
        # And the tiebreaker orders equal-``created`` rows by id descending.
        assert swept == sorted(ids, reverse=True)

    def test_count_agrees_with_exhaustive_filtered_sweep(self):
        # filter -> count -> paginate through every page, asserting the union of every page
        # equals the filtered set exactly, with no duplicates.
        self._seed(
            [
                _build_notification(
                    f"id-{i:02d}",
                    tenant="acme" if i % 3 == 0 else "beta",
                    created=_dt((i % 5) + 1),
                )
                for i in range(23)
            ]
        )
        filt = {"tenant": "acme"}
        expected_ids = {n.id for n in self.backend.notifications if n.tenant == "acme"}
        total = self.backend.count_notifications(filt)
        assert total == len(expected_ids)

        order_by = {"field": "created_at", "direction": "asc"}
        page_size = 4
        swept: list[str] = []
        page = 1
        while True:
            batch = self.backend.filter_notifications(filt, page, page_size, order_by=order_by)
            if not batch:
                break
            swept.extend(n.id for n in batch)
            page += 1

        assert len(swept) == len(set(swept)) == total
        assert set(swept) == expected_ids


class FilterNotificationsAsyncBackendTestCase(IsolatedAsyncioTestCase):
    """AsyncIO mirror of the core backend filtering behavior."""

    def setUp(self):
        self.database_file_name = tempfile.mktemp(suffix=".json")
        self.backend = FakeAsyncIOFileBackend(database_file_name=self.database_file_name)

    async def asyncTearDown(self):
        await self.backend.clear()

    def _seed(self, notifications: list[Notification]) -> None:
        self.backend.notifications = list(notifications)

    async def test_empty_filter_returns_all(self):
        self._seed([_build_notification("a"), _build_notification("b")])
        assert len(await self.backend.filter_notifications({}, 1, 10)) == 2

    async def test_nested_not_wrapping_or(self):
        self._seed(
            [
                _build_notification("a", user_id=1),
                _build_notification("b", user_id=2),
                _build_notification("c", user_id=3),
            ]
        )
        f = {"not": {"or": [{"user_id": 1}, {"user_id": 2}]}}
        result = await self.backend.filter_notifications(f, 1, 10)
        assert {n.id for n in result} == {"c"}

    async def test_string_lookup_case_insensitive(self):
        self._seed([_build_notification("a", body_template="HELLO World")])
        f = {"body_template": {"lookup": "includes", "value": "hello", "case_sensitive": False}}
        result = await self.backend.filter_notifications(f, 1, 10)
        assert {n.id for n in result} == {"a"}

    async def test_range_boundaries_inclusive(self):
        self._seed(
            [
                _build_notification("a", sent_at=_dt(10)),
                _build_notification("b", sent_at=_dt(20)),
            ]
        )
        result = await self.backend.filter_notifications(
            {"sent_at_range": {"from": _dt(10), "to": _dt(20)}}, 1, 10
        )
        assert {n.id for n in result} == {"a", "b"}

    async def test_order_by_desc_with_tiebreaker(self):
        shared = _dt(5)
        ids = [f"id-{i:02d}" for i in range(6)]
        self._seed(
            [
                _build_notification(i, created=shared)
                for i in [ids[3], ids[0], ids[5], ids[1], ids[4], ids[2]]
            ]
        )
        result = await self.backend.filter_notifications(
            {}, 1, 10, order_by={"field": "created_at", "direction": "desc"}
        )
        # id tiebreaker is applied in the same (descending) direction as the primary key.
        assert [n.id for n in result] == sorted(ids, reverse=True)

    async def test_count_notifications(self):
        self._seed([_build_notification("a"), _build_notification("b")])
        assert await self.backend.count_notifications({}) == 2

    async def test_get_filter_capabilities_is_empty(self):
        assert await self.backend.get_filter_capabilities() == {}

    async def test_pagination_sweep_with_shared_created_returns_every_row_once_ascending(self):
        # AsyncIO mirror of the sync fake's exhaustive sweep -- no full multi-page sweep exists
        # yet on this fake in either direction, so both are added here.
        shared = _dt(5)
        ids = [f"id-{i:02d}" for i in range(10)]
        scrambled = [ids[i] for i in (3, 7, 0, 9, 1, 5, 2, 8, 4, 6)]
        self._seed([_build_notification(i, created=shared) for i in scrambled])

        order_by = {"field": "created_at", "direction": "asc"}
        page_size = 3
        swept: list[str] = []
        page = 1
        while True:
            batch = await self.backend.filter_notifications({}, page, page_size, order_by=order_by)
            if not batch:
                break
            swept.extend(n.id for n in batch)
            page += 1

        assert sorted(swept) == ids
        assert len(swept) == len(set(swept)) == len(ids)
        assert swept == ids

    async def test_pagination_sweep_with_shared_created_returns_every_row_once_descending(self):
        shared = _dt(5)
        ids = [f"id-{i:02d}" for i in range(10)]
        scrambled = [ids[i] for i in (3, 7, 0, 9, 1, 5, 2, 8, 4, 6)]
        self._seed([_build_notification(i, created=shared) for i in scrambled])

        order_by = {"field": "created_at", "direction": "desc"}
        page_size = 3
        swept: list[str] = []
        page = 1
        while True:
            batch = await self.backend.filter_notifications({}, page, page_size, order_by=order_by)
            if not batch:
                break
            swept.extend(n.id for n in batch)
            page += 1

        assert sorted(swept) == ids
        assert len(swept) == len(set(swept)) == len(ids)
        assert swept == sorted(ids, reverse=True)

    async def test_count_agrees_with_exhaustive_filtered_sweep(self):
        self._seed(
            [
                _build_notification(
                    f"id-{i:02d}",
                    tenant="acme" if i % 3 == 0 else "beta",
                    created=_dt((i % 5) + 1),
                )
                for i in range(23)
            ]
        )
        filt = {"tenant": "acme"}
        expected_ids = {n.id for n in self.backend.notifications if n.tenant == "acme"}
        total = await self.backend.count_notifications(filt)
        assert total == len(expected_ids)

        order_by = {"field": "created_at", "direction": "asc"}
        page_size = 4
        swept: list[str] = []
        page = 1
        while True:
            batch = await self.backend.filter_notifications(
                filt, page, page_size, order_by=order_by
            )
            if not batch:
                break
            swept.extend(n.id for n in batch)
            page += 1

        assert len(swept) == len(set(swept)) == total
        assert set(swept) == expected_ids


class _TenantUnsupportedBackend(FakeFileBackend):
    """A backend that declines exactly one capability, to prove the merge-over-default."""

    def get_filter_capabilities(self) -> dict[str, bool]:
        return {"fields.tenant": False}


class _TenantUnsupportedAsyncBackend(FakeAsyncIOFileBackend):
    async def get_filter_capabilities(self) -> dict[str, bool]:
        return {"fields.tenant": False}


class FilterNotificationsServiceTestCase(TestCase):
    """Exercise the sync service surface: filter, count and capability merging."""

    def setUp(self):
        self.database_file_name = tempfile.mktemp(suffix=".json")
        self.backend = FakeFileBackend(database_file_name=self.database_file_name)
        self.service: NotificationService = NotificationService(
            notification_adapters=[],
            notification_backend=self.backend,
        )

    def tearDown(self):
        self.backend.clear()

    def test_filter_and_count_through_service(self):
        self.backend.notifications = [
            _build_notification("a", tenant="acme"),
            _build_notification("b", tenant="beta"),
        ]
        result = self.service.filter_notifications({"tenant": "acme"}, 1, 10)
        assert {n.id for n in result} == {"a"}
        assert self.service.count_notifications({}) == 2

    def test_capabilities_all_true_for_full_backend(self):
        caps = self.service.get_backend_supported_filter_capabilities()
        assert caps == DEFAULT_BACKEND_FILTER_CAPABILITIES
        assert all(caps.values())

    def test_capabilities_merge_over_default(self):
        service: NotificationService = NotificationService(
            notification_adapters=[],
            notification_backend=_TenantUnsupportedBackend(
                database_file_name=self.database_file_name
            ),
        )
        caps = service.get_backend_supported_filter_capabilities()
        assert caps["fields.tenant"] is False
        # Every other key stays True after the merge.
        for key, value in caps.items():
            if key == "fields.tenant":
                continue
            assert value is True, key
        assert set(caps) == set(DEFAULT_BACKEND_FILTER_CAPABILITIES)


class FilterNotificationsAsyncServiceTestCase(IsolatedAsyncioTestCase):
    """AsyncIO mirror of the service-level filtering surface."""

    def setUp(self):
        self.database_file_name = tempfile.mktemp(suffix=".json")
        self.backend = FakeAsyncIOFileBackend(database_file_name=self.database_file_name)
        self.service: AsyncIONotificationService = AsyncIONotificationService(
            notification_adapters=[],
            notification_backend=self.backend,
        )

    async def asyncTearDown(self):
        await self.backend.clear()

    async def test_filter_and_count_through_service(self):
        self.backend.notifications = [
            _build_notification("a", tenant="acme"),
            _build_notification("b", tenant="beta"),
        ]
        result = await self.service.filter_notifications({"tenant": "acme"}, 1, 10)
        assert {n.id for n in result} == {"a"}
        assert await self.service.count_notifications({}) == 2

    async def test_capabilities_all_true_for_full_backend(self):
        caps = await self.service.get_backend_supported_filter_capabilities()
        assert caps == DEFAULT_BACKEND_FILTER_CAPABILITIES

    async def test_capabilities_merge_over_default(self):
        service: AsyncIONotificationService = AsyncIONotificationService(
            notification_adapters=[],
            notification_backend=_TenantUnsupportedAsyncBackend(
                database_file_name=self.database_file_name
            ),
        )
        caps = await service.get_backend_supported_filter_capabilities()
        assert caps["fields.tenant"] is False
        for key, value in caps.items():
            if key == "fields.tenant":
                continue
            assert value is True, key


class TimestampStampingServiceTestCase(TestCase):
    """Regression coverage: fake backends must stamp ``created``/``modified`` like a real ORM's
    ``auto_now_add``/``auto_now`` columns, so ``created_at_range`` and default ordering work.
    """

    def setUp(self):
        self.database_file_name = tempfile.mktemp(suffix=".json")
        self.backend = FakeFileBackend(database_file_name=self.database_file_name)
        self.service: NotificationService = NotificationService(
            notification_adapters=[],
            notification_backend=self.backend,
        )

    def tearDown(self):
        self.backend.clear()

    def _create(self, **overrides) -> Notification:
        # Keep send_after in the future relative to "now" so create_notification never attempts
        # to send (and therefore never needs a registered context/adapter).
        future = datetime.datetime.now(tz=UTC) + datetime.timedelta(days=1)
        kwargs: dict = {
            "user_id": 1,
            "notification_type": NotificationTypes.EMAIL.value,
            "title": "Title",
            "body_template": "body",
            "context_name": "welcome_context",
            "context_kwargs": NotificationContextDict({}),
            "send_after": future,
            "subject_template": "subject",
            "preheader_template": "preheader",
        }
        kwargs.update(overrides)
        return self.service.create_notification(**kwargs)

    def test_create_notification_stamps_created_and_modified(self):
        notification = self._create()
        assert notification.created is not None
        assert notification.modified is not None
        assert notification.modified == notification.created

    def test_update_notification_advances_modified_leaves_created_unchanged(self):
        with freeze_time(_dt(1)):
            notification = self._create()
        assert notification.created == _dt(1)

        with freeze_time(_dt(2)):
            updated = self.service.update_notification(notification.id, title="New Title")

        assert updated.created == _dt(1)
        assert updated.modified == _dt(2)

    def test_mark_transition_advances_modified_leaves_created_unchanged(self):
        # Go through the backend directly (bypassing service.send()) so this test doesn't need
        # a registered context/adapter -- it is exercising the mark-transition timestamp bump,
        # not the send pipeline.
        with freeze_time(_dt(1)):
            notification = self.backend.persist_notification(
                user_id=1,
                notification_type=NotificationTypes.EMAIL.value,
                title="Title",
                body_template="body",
                context_name="welcome_context",
                context_kwargs={},
                send_after=None,
                subject_template="subject",
                preheader_template="preheader",
            )
            self.backend.mark_pending_as_sent(notification.id)

        original_created = self.backend.get_notification(notification.id).created
        assert original_created == _dt(1)

        with freeze_time(_dt(2)):
            self.backend.mark_sent_as_read(notification.id)

        reloaded = self.backend.get_notification(notification.id)
        assert reloaded.created == original_created
        assert reloaded.modified == _dt(2)

    def test_created_at_range_matches_service_created_notification(self):
        with freeze_time(_dt(5)):
            notification = self._create()

        in_range = self.backend.filter_notifications(
            {"created_at_range": {"from": _dt(4), "to": _dt(6)}}, 1, 10
        )
        assert {n.id for n in in_range} == {notification.id}

        out_of_range = self.backend.filter_notifications(
            {"created_at_range": {"from": _dt(10), "to": _dt(20)}}, 1, 10
        )
        assert out_of_range == []

    def test_default_ordering_is_newest_first_by_created(self):
        with freeze_time(_dt(1)):
            first = self._create()
        with freeze_time(_dt(2)):
            second = self._create()
        with freeze_time(_dt(3)):
            third = self._create()

        result = self.backend.filter_notifications({}, 1, 10)
        assert [n.id for n in result] == [third.id, second.id, first.id]


class TimestampStampingAsyncServiceTestCase(IsolatedAsyncioTestCase):
    """AsyncIO mirror of ``TimestampStampingServiceTestCase``."""

    def setUp(self):
        self.database_file_name = tempfile.mktemp(suffix=".json")
        self.backend = FakeAsyncIOFileBackend(database_file_name=self.database_file_name)
        self.service: AsyncIONotificationService = AsyncIONotificationService(
            notification_adapters=[],
            notification_backend=self.backend,
        )

    async def asyncTearDown(self):
        await self.backend.clear()

    async def _create(self, **overrides) -> Notification:
        future = datetime.datetime.now(tz=UTC) + datetime.timedelta(days=1)
        kwargs: dict = {
            "user_id": 1,
            "notification_type": NotificationTypes.EMAIL.value,
            "title": "Title",
            "body_template": "body",
            "context_name": "welcome_context",
            "context_kwargs": NotificationContextDict({}),
            "send_after": future,
            "subject_template": "subject",
            "preheader_template": "preheader",
        }
        kwargs.update(overrides)
        return await self.service.create_notification(**kwargs)

    async def test_create_notification_stamps_created_and_modified(self):
        notification = await self._create()
        assert notification.created is not None
        assert notification.modified is not None
        assert notification.modified == notification.created

    async def test_update_notification_advances_modified_leaves_created_unchanged(self):
        with freeze_time(_dt(1)):
            notification = await self._create()
        assert notification.created == _dt(1)

        with freeze_time(_dt(2)):
            updated = await self.service.update_notification(notification.id, title="New Title")

        assert updated.created == _dt(1)
        assert updated.modified == _dt(2)

    async def test_mark_transition_advances_modified_leaves_created_unchanged(self):
        # Go through the backend directly (bypassing service.send()) so this test doesn't need
        # a registered context/adapter -- it is exercising the mark-transition timestamp bump,
        # not the send pipeline.
        with freeze_time(_dt(1)):
            notification = await self.backend.persist_notification(
                user_id=1,
                notification_type=NotificationTypes.EMAIL.value,
                title="Title",
                body_template="body",
                context_name="welcome_context",
                context_kwargs={},
                send_after=None,
                subject_template="subject",
                preheader_template="preheader",
            )
            await self.backend.mark_pending_as_sent(notification.id)

        original_created = (await self.backend.get_notification(notification.id)).created
        assert original_created == _dt(1)

        with freeze_time(_dt(2)):
            await self.backend.mark_sent_as_read(notification.id)

        reloaded = await self.backend.get_notification(notification.id)
        assert reloaded.created == original_created
        assert reloaded.modified == _dt(2)

    async def test_created_at_range_matches_service_created_notification(self):
        with freeze_time(_dt(5)):
            notification = await self._create()

        in_range = await self.backend.filter_notifications(
            {"created_at_range": {"from": _dt(4), "to": _dt(6)}}, 1, 10
        )
        assert {n.id for n in in_range} == {notification.id}

        out_of_range = await self.backend.filter_notifications(
            {"created_at_range": {"from": _dt(10), "to": _dt(20)}}, 1, 10
        )
        assert out_of_range == []

    async def test_default_ordering_is_newest_first_by_created(self):
        with freeze_time(_dt(1)):
            first = await self._create()
        with freeze_time(_dt(2)):
            second = await self._create()
        with freeze_time(_dt(3)):
            third = await self._create()

        result = await self.backend.filter_notifications({}, 1, 10)
        assert [n.id for n in result] == [third.id, second.id, first.id]


def _nonce_context(**_kwargs) -> NotificationContextDict:
    """A context generator that returns a fresh value every call.

    Used to distinguish "regenerated" (a new nonce) from "reused verbatim" (the exact same
    stored dict) in the ``resend_notification`` tests below.
    """
    return NotificationContextDict({"nonce": str(uuid.uuid4())})


class ResendNotificationServiceTestCase(TestCase):
    """Exercise ``resend_notification`` end to end against the sync fake + adapter."""

    def setUp(self):
        self.database_file_name = tempfile.mktemp(suffix=".json")
        self.backend = FakeFileBackend(database_file_name=self.database_file_name)
        self.adapter = FakeEmailAdapter(
            backend=self.backend, template_renderer=FakeTemplateRenderer()
        )
        self.service: NotificationService = NotificationService(
            notification_adapters=[self.adapter],
            notification_backend=self.backend,
        )
        register_context("resend_nonce_context")(_nonce_context)

    def tearDown(self):
        self.backend.clear()

    def _create_sent_notification(self, **overrides) -> Notification:
        kwargs: dict = {
            "user_id": 1,
            "notification_type": NotificationTypes.EMAIL.value,
            "title": "Title",
            "body_template": "body",
            "context_name": "resend_nonce_context",
            "context_kwargs": NotificationContextDict({}),
            "send_after": None,
            "subject_template": "subject",
            "preheader_template": "preheader",
        }
        kwargs.update(overrides)
        return self.service.create_notification(**kwargs)

    def test_resend_creates_new_row_and_leaves_original_untouched(self):
        source = self._create_sent_notification()
        original_snapshot = self.backend.get_notification(source.id)
        assert original_snapshot.status == NotificationStatus.SENT.value

        clone = self.service.resend_notification(source.id)

        assert clone.id != source.id
        assert clone.status == NotificationStatus.SENT.value
        assert len(self.backend.notifications) == 2

        reloaded_original = self.backend.get_notification(source.id)
        assert reloaded_original.id == original_snapshot.id
        assert reloaded_original.status == original_snapshot.status
        assert reloaded_original.sent_at == original_snapshot.sent_at
        assert reloaded_original.read_at == original_snapshot.read_at
        assert reloaded_original.context_used == original_snapshot.context_used

    def test_resend_regenerates_context_by_default(self):
        source = self._create_sent_notification()
        clone = self.service.resend_notification(source.id)
        assert clone.context_used != source.context_used

    def test_resend_reuses_stored_context_verbatim_when_requested(self):
        source = self._create_sent_notification()
        clone = self.service.resend_notification(source.id, use_stored_context_if_available=True)
        assert clone.context_used == source.context_used

    def test_resend_regenerates_when_no_stored_context_even_if_requested(self):
        # A notification with no stored context_used (never sent) falls back to regenerating,
        # even when the caller asks to reuse a stored context.
        notification = self.backend.persist_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Title",
            body_template="body",
            context_name="resend_nonce_context",
            context_kwargs={},
            send_after=None,
            subject_template="subject",
            preheader_template="preheader",
        )
        assert notification.context_used is None

        clone = self.service.resend_notification(
            notification.id, use_stored_context_if_available=True
        )
        assert clone.context_used is not None

    def test_resend_one_off_raises(self):
        one_off = self.service.create_one_off_notification(
            email_or_phone="user@example.com",
            first_name="A",
            last_name="B",
            notification_type=NotificationTypes.EMAIL.value,
            title="Title",
            body_template="body",
            context_name="resend_nonce_context",
            context_kwargs=NotificationContextDict({}),
            send_after=None,
            subject_template="subject",
            preheader_template="preheader",
        )
        assert len(self.backend.notifications) == 1

        with self.assertRaises(NotificationResendError):
            self.service.resend_notification(one_off.id)

        assert len(self.backend.notifications) == 1

    def test_resend_future_scheduled_raises(self):
        future = datetime.datetime.now(tz=UTC) + datetime.timedelta(days=1)
        notification = self.backend.persist_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Title",
            body_template="body",
            context_name="resend_nonce_context",
            context_kwargs={},
            send_after=future,
            subject_template="subject",
            preheader_template="preheader",
        )
        assert len(self.backend.notifications) == 1

        with self.assertRaises(NotificationResendError):
            self.service.resend_notification(notification.id)

        assert len(self.backend.notifications) == 1

    def test_resend_copies_attachment_rows(self):
        source = self._create_sent_notification(
            attachments=[
                NotificationAttachment(
                    file=b"hello world",
                    filename="hello.txt",
                    content_type="text/plain",
                )
            ]
        )
        assert len(source.attachments) == 1

        clone = self.service.resend_notification(source.id)

        assert len(clone.attachments) == 1
        assert clone.attachments[0].filename == "hello.txt"
        assert clone.attachments[0].get_file_data() == b"hello world"

    def test_resend_carries_tenant_forward(self):
        source = self._create_sent_notification(tenant="acme")
        clone = self.service.resend_notification(source.id)
        assert clone.tenant == "acme"


class ResendNotificationAsyncServiceTestCase(IsolatedAsyncioTestCase):
    """AsyncIO mirror of ``ResendNotificationServiceTestCase``."""

    def setUp(self):
        self.database_file_name = tempfile.mktemp(suffix=".json")
        self.backend = FakeAsyncIOFileBackend(database_file_name=self.database_file_name)
        self.adapter = FakeAsyncIOEmailAdapter(
            backend=self.backend, template_renderer=FakeTemplateRenderer()
        )
        self.service: AsyncIONotificationService = AsyncIONotificationService(
            notification_adapters=[self.adapter],
            notification_backend=self.backend,
        )
        register_context("resend_nonce_context_async")(_nonce_context)

    async def asyncTearDown(self):
        await self.backend.clear()

    async def _create_sent_notification(self, **overrides) -> Notification:
        kwargs: dict = {
            "user_id": 1,
            "notification_type": NotificationTypes.EMAIL.value,
            "title": "Title",
            "body_template": "body",
            "context_name": "resend_nonce_context_async",
            "context_kwargs": NotificationContextDict({}),
            "send_after": None,
            "subject_template": "subject",
            "preheader_template": "preheader",
        }
        kwargs.update(overrides)
        return await self.service.create_notification(**kwargs)

    async def test_resend_creates_new_row_and_leaves_original_untouched(self):
        source = await self._create_sent_notification()
        original_snapshot = await self.backend.get_notification(source.id)
        assert original_snapshot.status == NotificationStatus.SENT.value

        clone = await self.service.resend_notification(source.id)

        assert clone.id != source.id
        assert clone.status == NotificationStatus.SENT.value
        assert len(self.backend.notifications) == 2

        reloaded_original = await self.backend.get_notification(source.id)
        assert reloaded_original.id == original_snapshot.id
        assert reloaded_original.status == original_snapshot.status
        assert reloaded_original.sent_at == original_snapshot.sent_at
        assert reloaded_original.read_at == original_snapshot.read_at
        assert reloaded_original.context_used == original_snapshot.context_used

    async def test_resend_regenerates_context_by_default(self):
        source = await self._create_sent_notification()
        clone = await self.service.resend_notification(source.id)
        assert clone.context_used != source.context_used

    async def test_resend_reuses_stored_context_verbatim_when_requested(self):
        source = await self._create_sent_notification()
        clone = await self.service.resend_notification(
            source.id, use_stored_context_if_available=True
        )
        assert clone.context_used == source.context_used

    async def test_resend_regenerates_when_no_stored_context_even_if_requested(self):
        notification = await self.backend.persist_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Title",
            body_template="body",
            context_name="resend_nonce_context_async",
            context_kwargs={},
            send_after=None,
            subject_template="subject",
            preheader_template="preheader",
        )
        assert notification.context_used is None

        clone = await self.service.resend_notification(
            notification.id, use_stored_context_if_available=True
        )
        assert clone.context_used is not None

    async def test_resend_one_off_raises(self):
        one_off = await self.service.create_one_off_notification(
            email_or_phone="user@example.com",
            first_name="A",
            last_name="B",
            notification_type=NotificationTypes.EMAIL.value,
            title="Title",
            body_template="body",
            context_name="resend_nonce_context_async",
            context_kwargs=NotificationContextDict({}),
            send_after=None,
            subject_template="subject",
            preheader_template="preheader",
        )
        assert len(self.backend.notifications) == 1

        with self.assertRaises(NotificationResendError):
            await self.service.resend_notification(one_off.id)

        assert len(self.backend.notifications) == 1

    async def test_resend_future_scheduled_raises(self):
        future = datetime.datetime.now(tz=UTC) + datetime.timedelta(days=1)
        notification = await self.backend.persist_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Title",
            body_template="body",
            context_name="resend_nonce_context_async",
            context_kwargs={},
            send_after=future,
            subject_template="subject",
            preheader_template="preheader",
        )
        assert len(self.backend.notifications) == 1

        with self.assertRaises(NotificationResendError):
            await self.service.resend_notification(notification.id)

        assert len(self.backend.notifications) == 1

    async def test_resend_copies_attachment_rows(self):
        source = await self._create_sent_notification(
            attachments=[
                NotificationAttachment(
                    file=b"hello world",
                    filename="hello.txt",
                    content_type="text/plain",
                )
            ]
        )
        assert len(source.attachments) == 1

        clone = await self.service.resend_notification(source.id)

        assert len(clone.attachments) == 1
        assert clone.attachments[0].filename == "hello.txt"
        assert clone.attachments[0].get_file_data() == b"hello world"

    async def test_resend_carries_tenant_forward(self):
        source = await self._create_sent_notification(tenant="acme")
        clone = await self.service.resend_notification(source.id)
        assert clone.tenant == "acme"
