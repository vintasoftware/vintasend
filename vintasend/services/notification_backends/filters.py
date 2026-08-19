"""Composable filter vocabulary for ``filter_notifications``.

This module defines the JSON-round-trippable filter object a dashboard POSTs to a backend,
the ordering descriptor, the backend capability report, and a reference in-memory evaluator
the fakes (and downstream test suites) reuse. Everything here is a ``TypedDict`` rather than a
dataclass precisely so a client can build a filter from parsed JSON with no adapter layer.

Two naming divergences are intentional and would otherwise read as oversights:

1. **Filter field names are snake_case; capability keys are camelCase.** The filter fields
   (``notification_type``, ``send_after_range``) are an in-process Python API that ``mypy``
   checks and developers type by hand, so snake_case is correct. The capability keys
   (``'fields.notificationType'``, ``'orderBy.sentAt'``) are *data a client reads*, kept
   byte-identical to the TypeScript sibling library so one dashboard can consume a capability
   report from either ecosystem with no translation table.

2. **Inside ``StringFilterLookup`` the lookup *values* are snake_case** (``starts_with``,
   ``ends_with``) to match the field-naming decision, while the corresponding *capability keys*
   stay camelCase (``'stringLookups.startsWith'``). The value is a Python literal a developer
   types; the key is wire data a client reads.

NULL / ``None`` semantics (mirrored by every backend, including ``vintasend-django`` in SQL):

* A positive filter on a field whose stored value is ``None`` does **not** match. Membership on
  ``None`` is false, a string lookup on ``None`` is false, and a date range on ``None`` is false.
* Because negation is applied by the ``not`` wrapper around the positive result, a ``None`` row
  is *included* under negation. ``{"not": {"tenant": "acme"}}`` therefore returns rows whose
  tenant is anything-but-``acme`` **and** rows whose tenant is ``None``. This matches the Django
  intent of ``~Q(field__in=[...]) | Q(field__isnull=True)`` for nullable columns.

Date-range bounds are **inclusive on both ends** (``from`` maps to ``>=``, ``to`` maps to
``<=``). Case sensitivity for string lookups defaults to **case-sensitive** when
``case_sensitive`` is absent; a bare ``str`` for a string field means a case-sensitive
``exact`` match.
"""

import datetime
import functools
import uuid
from enum import Enum
from typing import TYPE_CHECKING, Literal, TypeAlias, TypedDict, TypeGuard

from vintasend.constants import NotificationStatus, NotificationTypes


if TYPE_CHECKING:
    from vintasend.services.dataclasses import Notification, OneOffNotification


__all__ = [
    "DEFAULT_BACKEND_FILTER_CAPABILITIES",
    "AndFilter",
    "DateRange",
    "NotFilter",
    "NotificationFilter",
    "NotificationFilterFields",
    "NotificationOrderBy",
    "NotificationOrderByField",
    "NotificationOrderDirection",
    "OrFilter",
    "StringFieldFilter",
    "StringFilterLookup",
    "is_field_filter",
    "is_string_filter_lookup",
    "matches_filter",
    "sort_notifications",
]


# ``from`` is a Python keyword, so the wire key can only be expressed with the functional
# ``TypedDict`` syntax. The key stays ``from`` for cross-language parity with the TS sibling.
DateRange = TypedDict(
    "DateRange", {"from": datetime.datetime, "to": datetime.datetime}, total=False
)


class StringFilterLookup(TypedDict, total=False):
    # ``lookup`` and ``value`` are required in practice; ``total=False`` only because a JSON
    # payload might omit them and we validate at evaluation time rather than at typing time.
    lookup: Literal["exact", "starts_with", "ends_with", "includes"]
    value: str
    case_sensitive: bool


StringFieldFilter: TypeAlias = str | StringFilterLookup


NotificationOrderByField = Literal["send_after", "sent_at", "read_at", "created_at", "updated_at"]
NotificationOrderDirection = Literal["asc", "desc"]


class NotificationOrderBy(TypedDict):
    field: NotificationOrderByField
    direction: NotificationOrderDirection


class NotificationFilterFields(TypedDict, total=False):
    status: NotificationStatus | list[NotificationStatus]
    notification_type: NotificationTypes | list[NotificationTypes]
    adapter_used: str | list[str]
    user_id: int | str | uuid.UUID
    body_template: StringFieldFilter
    subject_template: StringFieldFilter
    context_name: StringFieldFilter
    tenant: str | list[str]
    send_after_range: DateRange
    created_at_range: DateRange
    sent_at_range: DateRange
    read_at_range: DateRange


# ``and`` / ``or`` / ``not`` are Python keywords, so these single-key groups can only be
# expressed with the functional syntax. The wire keys stay ``and`` / ``or`` / ``not``.
AndFilter = TypedDict("AndFilter", {"and": list["NotificationFilter"]})
OrFilter = TypedDict("OrFilter", {"or": list["NotificationFilter"]})
NotFilter = TypedDict("NotFilter", {"not": "NotificationFilter"})


NotificationFilter: TypeAlias = NotificationFilterFields | AndFilter | OrFilter | NotFilter


# All capabilities default to ``True``. A backend reports only what it *cannot* do, and its
# report is merged OVER this default, so a filter field added in a later release does not force
# every backend to re-declare support. Keys are camelCase dotted, byte-identical to the TS
# sibling. See the module docstring for the snake_case-fields / camelCase-keys rationale.
DEFAULT_BACKEND_FILTER_CAPABILITIES: dict[str, bool] = {
    # Composition. A backend that can evaluate field filters but not assemble them into
    # ``and`` / ``or`` / ``not`` groups declines these. ``notNested`` is the separate,
    # narrower question of whether ``not`` may wrap a *group* rather than a single field
    # filter (``{"not": {"or": [...]}}``): a query builder able to negate one predicate
    # cannot always negate a whole subtree, so a backend may support ``logical.not`` and
    # decline ``logical.notNested``.
    "logical.and": True,
    "logical.or": True,
    "logical.not": True,
    "logical.notNested": True,
    "fields.status": True,
    "fields.notificationType": True,
    "fields.adapterUsed": True,
    "fields.userId": True,
    "fields.bodyTemplate": True,
    "fields.subjectTemplate": True,
    "fields.contextName": True,
    "fields.tenant": True,
    "fields.sendAfterRange": True,
    "fields.createdAtRange": True,
    "fields.sentAtRange": True,
    "fields.readAtRange": True,
    # Negating a date range specifically. Scoped to ranges because that is where backends
    # actually struggle: a negated membership or string lookup is a plain ``NOT IN`` /
    # ``NOT LIKE``, whereas a negated range has to include NULL rows to satisfy this
    # library's negation semantics (see the module docstring -- ``{"not": {...}}`` returns
    # rows whose value is anything-but, *plus* rows whose value is ``None``), which not
    # every query builder expresses. A backend supporting ``fields.sentAtRange`` may
    # therefore still decline ``negation.sentAtRange``.
    "negation.sendAfterRange": True,
    "negation.createdAtRange": True,
    "negation.sentAtRange": True,
    "negation.readAtRange": True,
    "stringLookups.exact": True,
    "stringLookups.startsWith": True,
    "stringLookups.endsWith": True,
    "stringLookups.includes": True,
    # These two are separate capabilities, not one flag and its negation. A backend can
    # lack either:
    #
    # * ``caseSensitive: False`` -- everything is forced case-insensitive, which is what a
    #   store on a case-insensitive collation (MySQL's ``*_ci``) does. It cannot honour
    #   ``case_sensitive: True``, nor a bare ``str`` filter, which means the same thing.
    # * ``caseInsensitive: False`` -- only exact-case matching is available, e.g. a store
    #   with ``LIKE`` but no ``ILIKE`` and no way to fold case. It cannot honour
    #   ``case_sensitive: False``.
    #
    # Most backends support both, hence both default to ``True``. A backend supporting
    # *neither* cannot match strings at all and should decline the lookups above instead.
    # Reading one as the inverse of the other silently inverts the answer for exactly the
    # backends that have a constraint worth reporting.
    "stringLookups.caseSensitive": True,
    "stringLookups.caseInsensitive": True,
    "orderBy.sendAfter": True,
    "orderBy.sentAt": True,
    "orderBy.readAt": True,
    "orderBy.createdAt": True,
    "orderBy.updatedAt": True,
    # Whether ``page`` is 1-indexed, i.e. whether page 1 is the first page. Every backend
    # in this library is, which is why the default is ``True`` and reads naturally under
    # the declare-only-what-you-cannot-do rule above.
    #
    # This is a *convention*, not a feature, and it is reported because getting it wrong
    # is silent: a caller that assumes the wrong base does not raise, it just serves the
    # wrong page, skips the first record, or returns an empty first page. A caller that
    # paginates a backend it did not write should read this key rather than assume --
    # notably an HTTP layer converting between its own page numbering and a backend's.
    # The `vintasend-ts` sibling library's backends are 0-indexed, so anything consuming
    # both ecosystems cannot hardcode either answer.
    #
    # It covers every paginated backend method -- ``filter_notifications``,
    # ``get_pending_notifications``, ``get_future_notifications`` and the rest -- not just
    # the filter API, since a backend has one pagination convention throughout.
    "pagination.oneIndexed": True,
}


_LOGICAL_KEYS = ("and", "or", "not")

# String-lookup fields evaluate against the same-named attribute on the notification.
_STRING_LOOKUP_FIELDS = frozenset({"body_template", "subject_template", "context_name"})

# Scalar-or-list membership fields, mapped to the notification attribute they read.
_MEMBERSHIP_FIELDS: dict[str, str] = {
    "status": "status",
    "notification_type": "notification_type",
    "adapter_used": "adapter_used",
    "user_id": "user_id",
    "tenant": "tenant",
}

# Date-range fields, mapped to the notification attribute they read. Note ``created_at_range``
# reads ``created`` and there is no ``updated_at`` range field.
_RANGE_FIELDS: dict[str, str] = {
    "send_after_range": "send_after",
    "created_at_range": "created",
    "sent_at_range": "sent_at",
    "read_at_range": "read_at",
}

# Order-by field names, mapped to the dataclass attribute. ``updated_at`` maps to ``modified``
# and ``created_at`` maps to ``created``.
_ORDER_FIELD_TO_ATTR: dict[str, str] = {
    "send_after": "send_after",
    "sent_at": "sent_at",
    "read_at": "read_at",
    "created_at": "created",
    "updated_at": "modified",
}


def is_field_filter(filter: "NotificationFilter") -> TypeGuard["NotificationFilterFields"]:  # noqa: A002
    """Return ``True`` if ``filter`` is a field filter rather than an ``and``/``or``/``not`` group."""
    return not any(key in filter for key in _LOGICAL_KEYS)


def is_string_filter_lookup(value: "StringFieldFilter") -> TypeGuard["StringFilterLookup"]:
    """Return ``True`` if a string-field filter is a ``StringFilterLookup`` and not a bare ``str``."""
    return isinstance(value, dict)


def _scalar(value: object) -> str:
    """Normalize a scalar (including enum members) to a comparable string.

    ``user_id`` may be ``int``, ``str`` or ``uuid.UUID``; ``status`` / ``notification_type`` may
    arrive as their enum member or its ``.value``. Comparing by ``str`` matches the fakes'
    existing identity handling (``str(n.user_id) == str(user_id)``).
    """
    if isinstance(value, Enum):
        value = value.value
    return str(value)


def _matches_membership(actual: object, expected: object) -> bool:
    if actual is None:
        return False
    candidates = expected if isinstance(expected, (list, tuple, set)) else [expected]
    normalized_actual = _scalar(actual)
    return any(normalized_actual == _scalar(candidate) for candidate in candidates)


def _matches_string(actual: object, spec: "StringFieldFilter") -> bool:
    if not isinstance(actual, str):
        return False
    if isinstance(spec, dict):
        lookup: str = spec.get("lookup", "exact")
        target: str = spec.get("value", "")
        case_sensitive = spec.get("case_sensitive", True)
    else:
        lookup = "exact"
        target = spec
        case_sensitive = True

    haystack = actual if case_sensitive else actual.lower()
    needle = target if case_sensitive else target.lower()

    if lookup == "exact":
        return haystack == needle
    if lookup == "starts_with":
        return haystack.startswith(needle)
    if lookup == "ends_with":
        return haystack.endswith(needle)
    if lookup == "includes":
        return needle in haystack
    return False


def _matches_range(actual: object, date_range: "DateRange") -> bool:
    if not isinstance(actual, datetime.datetime):
        return False
    lower = date_range.get("from")
    upper = date_range.get("to")
    if lower is not None and actual < lower:
        return False
    if upper is not None and actual > upper:
        return False
    return True


def _matches_field(
    notification: "Notification | OneOffNotification", field: str, value: object
) -> bool:
    if field in _RANGE_FIELDS:
        return _matches_range(getattr(notification, _RANGE_FIELDS[field], None), value)  # type: ignore[arg-type]
    if field in _STRING_LOOKUP_FIELDS:
        return _matches_string(getattr(notification, field, None), value)  # type: ignore[arg-type]
    if field in _MEMBERSHIP_FIELDS:
        return _matches_membership(getattr(notification, _MEMBERSHIP_FIELDS[field], None), value)
    # Unknown field: treat as non-matching rather than raising, so an over-eager client cannot
    # crash the backend. Downstream ORM backends may choose to reject instead.
    return False


def matches_filter(
    notification: "Notification | OneOffNotification",
    filter: "NotificationFilter",  # noqa: A002
) -> bool:
    """Evaluate a composable filter against a single notification.

    Semantics:
        * An empty filter (``{}``) matches every notification.
        * Multiple keys inside one field filter are an implicit ``AND``.
        * A scalar means equality; a list means membership.
        * ``and`` / ``or`` / ``not`` compose arbitrarily and nest.
        * ``None`` handling and range inclusivity are documented at module level.
    """
    if "and" in filter:
        return all(matches_filter(notification, sub) for sub in filter["and"])  # type: ignore[typeddict-item]
    if "or" in filter:
        return any(matches_filter(notification, sub) for sub in filter["or"])  # type: ignore[typeddict-item]
    if "not" in filter:
        return not matches_filter(notification, filter["not"])  # type: ignore[typeddict-item]
    # Field filter (possibly empty -> matches everything).
    return all(_matches_field(notification, key, value) for key, value in filter.items())


def _compare_optional(left: object, right: object) -> int:
    """Total order over optionally-``None`` values; ``None`` sorts before non-``None`` ascending."""
    if left is None and right is None:
        return 0
    if left is None:
        return -1
    if right is None:
        return 1
    if left < right:  # type: ignore[operator]
        return -1
    if left > right:  # type: ignore[operator]
        return 1
    return 0


def sort_notifications(
    notifications: "list[Notification | OneOffNotification]",
    order_by: "NotificationOrderBy | None",
) -> "list[Notification | OneOffNotification]":
    """Stably order notifications, always appending ``id`` in the primary sort direction.

    Defaults to ``created_at`` descending when ``order_by`` is ``None``, matching the Django
    backend's ``Meta.ordering = ("-created",)``. ``None`` values in a nullable sort column are
    tolerated without raising. The ``id`` tiebreaker is mandatory: without it, offset pagination
    over a non-unique sort key silently drops and duplicates rows across pages.
    """
    if order_by is None:
        attr = "created"
        reverse = True
    else:
        attr = _ORDER_FIELD_TO_ATTR[order_by["field"]]
        reverse = order_by["direction"] == "desc"

    def compare(
        first: "Notification | OneOffNotification", second: "Notification | OneOffNotification"
    ) -> int:
        result = _compare_optional(getattr(first, attr, None), getattr(second, attr, None))
        if result == 0:
            # Tiebreaker in the SAME direction as the primary key.
            result = _compare_optional(str(first.id), str(second.id))
        return -result if reverse else result

    return sorted(notifications, key=functools.cmp_to_key(compare))
