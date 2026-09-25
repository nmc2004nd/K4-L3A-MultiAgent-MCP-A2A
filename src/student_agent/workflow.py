from __future__ import annotations

import asyncio
from collections.abc import Iterable, Iterator
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from . import OUTPUT_SCHEMA_VERSION
from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter

PAYMENT_ISSUES = {"valid_split_payment", "payment_mismatch", "duplicate_charge"}
DELIVERY_ISSUES = {"late_delivery_seller", "late_delivery_logistics"}
REFUND_ISSUES = {
    "canceled_order_paid",
    "unavailable_order_paid",
    "refund_pending",
    "refund_failed",
}
OPTIONAL_TIMELINE_TOOLS = {"get_payment_timeline", "get_refund_timeline"}
ACTION_ISSUES = {
    "canceled_order_paid",
    "unavailable_order_paid",
    "late_delivery_seller",
    "late_delivery_logistics",
    "payment_mismatch",
    "duplicate_charge",
    "refund_pending",
    "refund_failed",
}

ISSUE_DETAILS: dict[str, tuple[str, str, str]] = {
    "canceled_order_paid": ("ORDER_CANCELED_AFTER_PAYMENT", "platform", "ISSUE_FULL_REFUND"),
    "unavailable_order_paid": ("ORDER_UNAVAILABLE_AFTER_PAYMENT", "seller", "ISSUE_FULL_REFUND"),
    "late_delivery_seller": ("SELLER_HANDOFF_DELAY", "seller", "REVIEW_SELLER_DELAY"),
    "late_delivery_logistics": (
        "LOGISTICS_TRANSIT_DELAY",
        "logistics_provider",
        "REVIEW_LOGISTICS_DELAY",
    ),
    "valid_split_payment": ("VALID_SPLIT_PAYMENT", "", "NO_ACTION_REQUIRED"),
    "payment_mismatch": ("PAYMENT_TOTAL_MISMATCH", "payment_provider", "RECONCILE_PAYMENT"),
    "duplicate_charge": ("DUPLICATE_PAYMENT_CAPTURE", "payment_provider", "REFUND_DUPLICATE"),
    "refund_pending": ("REFUND_PROCESSING_PENDING", "payment_provider", "MONITOR_REFUND"),
    "refund_failed": ("REFUND_PROCESSING_FAILED", "payment_provider", "RETRY_REFUND"),
    "unsupported_claim": ("CLAIM_NOT_SUPPORTED", "", "NO_ACTION_REQUIRED"),
    "insufficient_evidence": ("INSUFFICIENT_EVIDENCE", "unknown", "MANUAL_INVESTIGATION"),
}

ISSUE_TOOLS = {
    "canceled_order_paid": {"get_order", "get_order_payments", "get_refund_timeline", "get_policy"},
    "unavailable_order_paid": {
        "get_order",
        "get_order_items",
        "get_order_payments",
        "get_sellers",
        "get_refund_timeline",
        "get_policy",
    },
    "late_delivery_seller": {
        "get_order",
        "get_order_items",
        "get_shipment_summary",
        "get_sellers",
        "get_policy",
    },
    "late_delivery_logistics": {
        "get_order",
        "get_order_items",
        "get_shipment_summary",
        "get_sellers",
        "get_policy",
    },
    "valid_split_payment": {
        "get_order",
        "get_order_items",
        "get_order_payments",
        "get_payment_timeline",
        "get_policy",
    },
    "payment_mismatch": {
        "get_order",
        "get_order_items",
        "get_order_payments",
        "get_payment_timeline",
        "get_policy",
    },
    "duplicate_charge": {
        "get_order",
        "get_order_items",
        "get_order_payments",
        "get_payment_timeline",
        "get_policy",
    },
    "refund_pending": {
        "get_order",
        "get_order_payments",
        "get_refund_timeline",
        "get_policy",
    },
    "refund_failed": {
        "get_order",
        "get_order_payments",
        "get_refund_timeline",
        "get_policy",
    },
    "unsupported_claim": {
        "get_order",
        "get_order_items",
        "get_order_payments",
        "get_shipment_summary",
        "get_policy",
    },
}


def _walk(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key.lower(), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def _values(value: Any, keys: Iterable[str]) -> list[Any]:
    wanted = {key.lower() for key in keys}
    return [child for key, child in _walk(value) if key in wanted and child is not None]


def _direct(value: dict[str, Any], *keys: str) -> Any:
    wanted = {key.lower() for key in keys}
    for key, child in value.items():
        if key.lower() in wanted:
            return child
    return None


def _first(value: Any, *keys: str) -> Any:
    values = _values(value, keys)
    return values[0] if values else None


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _money(value: Decimal) -> float:
    return float(max(value, Decimal()).quantize(Decimal("0.01")))


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _text(value: Any) -> str:
    parts: list[str] = []
    for _, child in _walk(value):
        if isinstance(child, (str, bool)):
            parts.append(str(child).lower())
    return " ".join(parts)


def _labels(value: Any, *keys: str) -> set[str]:
    labels: set[str] = set()
    for child in _values(value, keys):
        candidates = child if isinstance(child, list) else [child]
        labels.update(str(candidate).strip().lower() for candidate in candidates)
    return labels


def _true_flag(value: Any, *keys: str) -> bool:
    for child in _values(value, keys):
        if child is True or child == 1:
            return True
        if isinstance(child, str) and child.strip().lower() in {"true", "yes", "1"}:
            return True
    return False


def _records(value: Any, *container_keys: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    for candidate in _values(value, container_keys):
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return [value] if isinstance(value, dict) else []


def _sum_records(records: list[dict[str, Any]], keys: tuple[str, ...]) -> Decimal | None:
    amounts: list[Decimal] = []
    for record in records:
        amount = _decimal(_first(record, *keys))
        if amount is not None:
            amounts.append(amount)
    return sum(amounts, Decimal()) if amounts else None


def _payment_total(data: Any) -> Decimal | None:
    explicit = _decimal(
        _direct(
            data,
            "captured_total_brl",
            "captured_total",
            "paid_total_brl",
            "payment_total",
        )
        if isinstance(data, dict)
        else None
    )
    if explicit is not None:
        return explicit
    records = _records(data, "payments", "payment_records", "transactions", "captures")
    return _sum_records(records, ("payment_value", "amount_brl", "amount", "value"))


def _expected_total(data: Any) -> Decimal | None:
    explicit = _decimal(
        _direct(data, "expected_total_brl", "order_total_brl", "order_total")
        if isinstance(data, dict)
        else None
    )
    if explicit is not None:
        return explicit
    records = _records(data, "items", "order_items")
    if not records:
        return None
    total = Decimal()
    found = False
    for record in records:
        price = _decimal(_first(record, "price", "item_price", "price_brl"))
        freight = _decimal(_first(record, "freight_value", "freight_brl", "shipping_cost"))
        if price is not None or freight is not None:
            total += (price or Decimal()) + (freight or Decimal())
            found = True
    return total if found else None


def _refund_amount(data: Any) -> tuple[Decimal | None, Decimal]:
    requested = _decimal(
        _first(data, "refundable_total_brl", "requested_refund_brl", "refund_amount_brl", "amount")
    )
    refunded = _decimal(_first(data, "refunded_total_brl", "refunded_amount_brl")) or Decimal()
    return requested, refunded


def _ids(evidence: dict[str, dict[str, Any]], keys: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for envelope in evidence.values():
        for value in _values(envelope["data"], keys):
            candidates = value if isinstance(value, list) else [value]
            for candidate in candidates:
                if isinstance(candidate, (str, int)) and str(candidate) not in found:
                    found.append(str(candidate))
    return found[:20]


def _seller_is_late(order: Any, items: Any, shipment: Any) -> bool:
    text = f"{_text(items)} {_text(shipment)}"
    verdicts = _labels(shipment, "verdict", "classification", "delay_type", "responsibility")
    if (
        verdicts & {"seller_delay", "seller_late", "late_handoff", "seller"}
        or _true_flag(shipment, "seller_delay", "seller_late", "late_handoff")
        or any(marker in text for marker in ("seller_delay", "seller_late", "late_handoff"))
    ):
        return True
    shared_handoff = _timestamp(
        _first(shipment, "order_delivered_carrier_date", "carrier_handoff_at", "shipped_at")
        or _first(order, "order_delivered_carrier_date", "carrier_handoff_at", "shipped_at")
    )
    for record in _records(items, "items", "order_items"):
        deadline = _timestamp(_first(record, "shipping_limit_date", "seller_deadline"))
        handoff = (
            _timestamp(
                _first(record, "order_delivered_carrier_date", "carrier_handoff_at", "shipped_at")
            )
            or shared_handoff
        )
        if deadline and handoff:
            try:
                if handoff > deadline:
                    return True
            except TypeError:
                continue
    return False


def _logistics_is_late(order: Any, shipment: Any) -> bool:
    text = f"{_text(order)} {_text(shipment)}"
    verdicts = _labels(shipment, "verdict", "classification", "delay_type", "responsibility")
    if (
        verdicts & {"logistics_delay", "carrier_delay", "late_delivery", "logistics_provider"}
        or _true_flag(shipment, "logistics_delay", "carrier_delay", "late_delivery")
        or any(marker in text for marker in ("logistics_delay", "carrier_delay", "late_delivery"))
    ):
        return True
    delivered = _timestamp(
        _first(shipment, "order_delivered_customer_date", "delivered_at", "delivery_date")
        or _first(order, "order_delivered_customer_date", "delivered_at")
    )
    estimate = _timestamp(
        _first(shipment, "order_estimated_delivery_date", "estimated_delivery_at")
        or _first(order, "order_estimated_delivery_date", "estimated_delivery_at")
    )
    if not delivered or not estimate:
        return False
    try:
        return delivered > estimate
    except TypeError:
        return False


def _classify(
    evidence: dict[str, dict[str, Any]], claim_topic: str
) -> tuple[str, float, Decimal | None, Decimal | None]:
    order = evidence.get("get_order", {}).get("data", {})
    items = evidence.get("get_order_items", {}).get("data", {})
    payments = evidence.get("get_order_payments", {}).get("data", {})
    payment_timeline = evidence.get("get_payment_timeline", {}).get("data", {})
    shipment = evidence.get("get_shipment_summary", {}).get("data", {})
    refund = evidence.get("get_refund_timeline", {}).get("data", {})
    captured = _payment_total(payments)
    expected = _expected_total(items)
    paid = captured is not None and captured > 0
    order_text = _text(order)
    payment_text = f"{_text(payments)} {_text(payment_timeline)}"
    refund_text = _text(refund)
    signals: set[str] = set()
    strengths: dict[str, float] = {}

    def add_signal(issue: str, strength: float) -> None:
        signals.add(issue)
        strengths[issue] = max(strength, strengths.get(issue, 0.0))

    payment_verdicts = _labels(
        {"payments": payments, "timeline": payment_timeline},
        "verdict",
        "classification",
        "payment_verdict",
        "status_code",
        "issue_code",
    )
    refund_verdicts = _labels(
        refund, "verdict", "classification", "refund_status", "status", "status_code"
    )

    if paid and any(word in order_text for word in ("canceled", "cancelled")):
        add_signal("canceled_order_paid", 0.95)
    if paid and "unavailable" in order_text:
        add_signal("unavailable_order_paid", 0.95)
    if _seller_is_late(order, items, shipment):
        add_signal("late_delivery_seller", 0.9)
    elif _logistics_is_late(order, shipment):
        add_signal("late_delivery_logistics", 0.9)
    if refund_verdicts & {"refund_failed", "failed", "rejected", "error"} or any(
        word in refund_text for word in ("failed", "rejected", "error")
    ):
        add_signal("refund_failed", 0.95)
    elif refund_verdicts & {"refund_pending", "pending", "processing", "initiated"} or any(
        word in refund_text for word in ("pending", "processing", "initiated")
    ):
        add_signal("refund_pending", 0.95)

    duplicate = bool(
        payment_verdicts & {"duplicate_capture", "duplicate_charge", "duplicate"}
    ) or _true_flag(
        {"payments": payments, "timeline": payment_timeline},
        "duplicate_capture",
        "duplicate_charge",
        "is_duplicate",
        "has_duplicate_capture",
    )
    split = bool(
        payment_verdicts & {"valid_split_payment", "split_payment", "reconciled_split"}
    ) or _true_flag(
        {"payments": payments, "timeline": payment_timeline},
        "valid_split_payment",
        "is_split_payment",
        "split_payment",
        "is_reconciled_split",
    )
    explicit_mismatch = bool(
        payment_verdicts & {"capture_mismatch", "payment_mismatch"}
    ) or _true_flag(
        {"payments": payments, "timeline": payment_timeline},
        "capture_mismatch",
        "payment_mismatch",
        "has_payment_mismatch",
    )
    reconciled = bool(payment_verdicts & {"reconciled", "matched", "paid"}) or _true_flag(
        {"payments": payments, "timeline": payment_timeline},
        "reconciled",
        "is_reconciled",
    )
    if duplicate or any(
        word in payment_text for word in ("duplicate_capture", "duplicate_charge", "duplicated")
    ):
        add_signal("duplicate_charge", 0.95 if duplicate else 0.85)

    payment_records = _records(payments, "payments", "payment_records", "transactions", "captures")
    mismatch = (
        captured is not None and expected is not None and abs(captured - expected) > Decimal("0.01")
    )
    if split:
        add_signal("valid_split_payment", 0.95)
    elif len(payment_records) > 1 and not mismatch and captured is not None:
        add_signal("valid_split_payment", 0.84)
    if explicit_mismatch:
        add_signal("payment_mismatch", 0.95)
    elif mismatch and not duplicate and not split and not reconciled:
        add_signal("payment_mismatch", 0.7)
    if _true_flag(
        {"order": order, "shipment": shipment, "payments": payments},
        "unsupported_claim",
        "claim_unsupported",
    ):
        add_signal("unsupported_claim", 0.9)

    if claim_topic in signals:
        return claim_topic, strengths[claim_topic], captured, expected
    precedence = (
        "refund_failed",
        "refund_pending",
        "duplicate_charge",
        "canceled_order_paid",
        "unavailable_order_paid",
        "late_delivery_seller",
        "late_delivery_logistics",
        "valid_split_payment",
        "payment_mismatch",
        "unsupported_claim",
    )
    for issue in precedence:
        if issue in signals:
            return issue, strengths[issue], captured, expected
    if evidence.keys() >= {
        "get_order",
        "get_order_items",
        "get_order_payments",
        "get_shipment_summary",
    }:
        return "unsupported_claim", 0.82, captured, expected
    return "insufficient_evidence", 0.35, captured, expected


def _data_conflicts(evidence: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    order = evidence.get("get_order", {}).get("data", {})
    shipment = evidence.get("get_shipment_summary", {}).get("data", {})
    for field, keys in (
        ("delivered_at", ("order_delivered_customer_date", "delivered_at")),
        ("estimated_delivery_at", ("order_estimated_delivery_date", "estimated_delivery_at")),
    ):
        order_value = _first(order, *keys)
        shipment_value = _first(shipment, *keys)
        if order_value is not None and shipment_value is not None and order_value != shipment_value:
            conflicts.append(
                {
                    "field": field,
                    "sources": ["get_order", "get_shipment_summary"],
                    "selected_source": "get_shipment_summary",
                    "resolution_code": "PREFER_DOMAIN_TIMELINE",
                }
            )

    item_sellers = {
        str(value)
        for value in _values(evidence.get("get_order_items", {}).get("data", {}), ("seller_id",))
    }
    seller_records = {
        str(value)
        for value in _values(evidence.get("get_sellers", {}).get("data", {}), ("seller_id",))
    }
    if item_sellers and seller_records and item_sellers != seller_records:
        conflicts.append(
            {
                "field": "seller_ids",
                "sources": ["get_order_items", "get_sellers"],
                "selected_source": "get_order_items",
                "resolution_code": "PREFER_ORDER_ITEM_OWNERSHIP",
            }
        )
    return conflicts[:5]


def _calibrate_confidence(
    issue: str,
    evidence: dict[str, dict[str, Any]],
    conflicts: list[dict[str, Any]],
    signal_confidence: float,
) -> float:
    required = ISSUE_TOOLS.get(issue, set(evidence))
    completeness = len(required & evidence.keys()) / len(required) if required else 0.0
    warning_count = sum(len(item.get("warnings", [])) for item in evidence.values())
    evidence_score = 0.55 + (0.4 * completeness)
    calibrated = min(signal_confidence, evidence_score)
    calibrated -= min(len(conflicts) * 0.1, 0.3)
    calibrated -= min(warning_count * 0.02, 0.15)
    return round(max(0.2, min(calibrated, 0.95)), 2)


async def _call(
    gateway: EvidenceGateway,
    tool_name: str,
    *,
    case_id: str,
    arguments: dict[str, str],
    required: bool,
) -> dict[str, Any] | None:
    attempts = 3 if required else 2
    for attempt in range(attempts):
        try:
            return await asyncio.wait_for(
                gateway.call(tool_name, case_id=case_id, **arguments), timeout=90
            )
        except (TimeoutError, RuntimeError) as exc:
            if attempt == attempts - 1:
                if not required:
                    return None
                raise RuntimeError(
                    f"{case_id}: required MCP tool {tool_name} failed after retry"
                ) from exc
            await asyncio.sleep(2**attempt)
    raise AssertionError("unreachable")


def _claim_evidence_refs(
    topic: Any,
    issue: str,
    evidence: dict[str, dict[str, Any]],
) -> list[str]:
    if topic == "requested_full_refund":
        tools = {"get_order", "get_order_payments", "get_refund_timeline", "get_policy"}
        if issue in {"late_delivery_seller", "late_delivery_logistics"}:
            tools |= {"get_order_items", "get_shipment_summary"}
    elif isinstance(topic, str) and topic in ISSUE_TOOLS:
        tools = ISSUE_TOOLS[topic]
    else:
        tools = ISSUE_TOOLS.get(issue, set(evidence))
    return [
        envelope["evidence_ref"] for tool_name, envelope in evidence.items() if tool_name in tools
    ]


def _policy_details(
    policy_data: Any,
    issue: str,
    default_cause: str,
    default_party: str,
    default_action: str,
) -> tuple[str, str, list[str]]:
    candidates: list[dict[str, Any]] = []
    for item in _objects(policy_data):
        keyed_rule = _direct(item, issue)
        if isinstance(keyed_rule, dict):
            candidates.append(keyed_rule)
        marker = _direct(item, "primary_issue", "issue", "issue_code", "topic")
        if isinstance(marker, str) and marker.lower() == issue:
            candidates.append(item)
    candidates.append(policy_data if isinstance(policy_data, dict) else {})

    cause = default_cause
    party = default_party
    actions = [default_action]
    allowed_parties = {
        "seller",
        "platform",
        "logistics_provider",
        "payment_provider",
        "customer",
        "unknown",
    }
    for rule in candidates:
        policy_cause = _direct(rule, "cause_code", "root_cause_code")
        if (
            isinstance(policy_cause, str)
            and policy_cause.isupper()
            and policy_cause.replace("_", "").isalnum()
        ):
            cause = policy_cause
        policy_party = _direct(rule, "responsible_party", "party_type")
        if isinstance(policy_party, str) and policy_party in allowed_parties:
            party = policy_party
        policy_actions = _direct(
            rule,
            "resolution_actions",
            "recommended_actions",
            "actions",
            "action_code",
            "action",
        )
        if isinstance(policy_actions, str) and 0 < len(policy_actions) <= 80:
            actions = [policy_actions]
        elif isinstance(policy_actions, list):
            valid_actions = [
                action
                for action in policy_actions
                if isinstance(action, str) and 0 < len(action) <= 80
            ]
            if valid_actions:
                actions = list(dict.fromkeys(valid_actions))[:8]
        if rule is not candidates[-1]:
            break
    return cause, party, actions


def _refund_resolution(
    issue: str,
    captured: Decimal | None,
    expected: Decimal | None,
    refund_data: Any,
    order_id: str,
) -> dict[str, Any]:
    amount = Decimal()
    reason = ""
    requested, refunded = _refund_amount(refund_data)
    if issue in {"canceled_order_paid", "unavailable_order_paid"}:
        amount = max((requested or captured or Decimal()) - refunded, Decimal())
        reason = "FULL_REFUND_DUE"
    elif issue == "duplicate_charge" and captured is not None and expected is not None:
        amount = max(captured - expected, Decimal())
        reason = "DUPLICATE_CAPTURE"
    elif issue == "payment_mismatch" and captured is not None and expected is not None:
        amount = max(captured - expected, Decimal())
        reason = "PAYMENT_OVERCHARGE"
    elif issue in {"refund_pending", "refund_failed"}:
        amount = max((requested or captured or Decimal()) - refunded, Decimal())
        reason = issue.upper()
    refund_lines = (
        [{"reason_code": reason, "amount_brl": _money(amount), "entity_id": order_id}]
        if amount > 0
        else []
    )
    return {
        "currency": "BRL",
        "recommended_refund_brl": _money(amount),
        "refund_lines": refund_lines,
    }


async def solve_case(
    case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter
) -> dict[str, Any]:
    """Investigate one L3A case using scoped MCP evidence and deterministic rules."""
    case_id = case.get("case_id")
    request = case.get("customer_request")
    if not isinstance(case_id, str) or not isinstance(request, dict):
        raise ValueError("case must contain case_id and customer_request")
    order_id = request.get("claimed_order_id")
    policy_version = case.get("policy_version")
    claims = request.get("claims", [])
    if not isinstance(order_id, str) or not isinstance(policy_version, str):
        raise ValueError(f"{case_id}: missing claimed_order_id or policy_version")
    claim_topic = (
        claims[0].get("topic")
        if claims and isinstance(claims[0], dict) and isinstance(claims[0].get("topic"), str)
        else ""
    )

    order_tools = ["get_order"]
    if claim_topic in {
        "unavailable_order_paid",
        "unsupported_claim",
        *PAYMENT_ISSUES,
        *DELIVERY_ISSUES,
    }:
        order_tools.append("get_order_items")
    if claim_topic in {"unavailable_order_paid", "unsupported_claim", *DELIVERY_ISSUES}:
        order_tools.append("get_sellers")

    assignments = {
        "order-agent": order_tools,
        "payment-agent": ["get_order_payments"],
        "policy-agent": ["get_policy"],
    }
    if claim_topic in {"unsupported_claim", *DELIVERY_ISSUES}:
        assignments["shipment-agent"] = ["get_shipment_summary"]
    if claim_topic in PAYMENT_ISSUES:
        assignments["payment-agent"].append("get_payment_timeline")
    if claim_topic in REFUND_ISSUES:
        assignments["payment-agent"].append("get_refund_timeline")

    evidence: dict[str, dict[str, Any]] = {}
    actor_refs: dict[str, list[str]] = {}
    for actor, tools in assignments.items():
        trace.emit(
            case_id=case_id,
            event_type="task_assigned",
            actor="coordinator",
            target=actor,
            decision_code="INVESTIGATE_DOMAIN",
            attributes={"tool_count": len(tools)},
        )
        actor_refs[actor] = []
        for tool_name in tools:
            arguments = (
                {"policy_version": policy_version}
                if tool_name == "get_policy"
                else {"order_id": order_id}
            )
            required = tool_name not in OPTIONAL_TIMELINE_TOOLS or (
                tool_name == "get_refund_timeline"
                and claim_topic in {"refund_pending", "refund_failed"}
            )
            envelope = await _call(
                gateway,
                tool_name,
                case_id=case_id,
                arguments=arguments,
                required=required,
            )
            if envelope is None:
                continue
            evidence[tool_name] = envelope
            evidence_ref = envelope["evidence_ref"]
            actor_refs[actor].append(evidence_ref)
            trace.emit(
                case_id=case_id,
                event_type="tool_result_consumed",
                actor=actor,
                tool_name=tool_name,
                evidence_refs=[evidence_ref],
            )
        trace.emit(
            case_id=case_id,
            event_type="handoff",
            actor=actor,
            target="verifier",
            decision_code="DOMAIN_REVIEW_COMPLETE" if actor_refs[actor] else "NO_DOMAIN_EVIDENCE",
            evidence_refs=actor_refs[actor],
        )

    issue, signal_confidence, captured, expected = _classify(evidence, claim_topic)
    conflicts = _data_conflicts(evidence)
    confidence = _calibrate_confidence(issue, evidence, conflicts, signal_confidence)
    relevant_tools = ISSUE_TOOLS.get(issue, set(evidence))
    relevant_refs = [
        envelope["evidence_ref"]
        for tool_name, envelope in evidence.items()
        if tool_name in relevant_tools
    ]
    default_cause, default_party, default_action = ISSUE_DETAILS[issue]
    cause, party_type, actions = _policy_details(
        evidence["get_policy"]["data"],
        issue,
        default_cause,
        default_party,
        default_action,
    )
    sellers = _ids(evidence, ("seller_id", "seller_ids"))
    party_id = sellers[0] if party_type == "seller" and sellers else None
    responsible_parties = [{"party_type": party_type, "party_id": party_id}] if party_type else []
    financial = _refund_resolution(
        issue,
        captured,
        expected,
        evidence.get("get_refund_timeline", {}).get("data", {}),
        order_id,
    )
    case_status = (
        "action_required"
        if issue in ACTION_ISSUES
        else "needs_investigation"
        if issue == "insufficient_evidence"
        else "no_action"
    )

    claim_assessments = []
    for claim in claims[:5]:
        if not isinstance(claim, dict) or not isinstance(claim.get("claim_id"), str):
            continue
        topic = claim.get("topic")
        if topic == "requested_full_refund":
            supported = financial["recommended_refund_brl"] > 0
        else:
            supported = topic == issue
        claim_refs = _claim_evidence_refs(topic, issue, evidence)
        claim_assessments.append(
            {
                "claim_id": claim["claim_id"],
                "verdict": "supported" if supported else "unsupported",
                "confidence": confidence,
                "evidence_refs": claim_refs,
            }
        )

    output = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "case_id": case_id,
        "assessment": {
            "primary_issue": issue,
            "case_status": case_status,
            "confidence": confidence,
        },
        "affected_entities": {
            "order_ids": [order_id],
            "item_ids": _ids(evidence, ("order_item_id", "item_id", "item_ids")),
            "seller_ids": sellers,
            "payment_references": _ids(
                evidence,
                (
                    "payment_reference",
                    "payment_references",
                    "payment_id",
                    "transaction_id",
                    "charge_id",
                ),
            ),
            "shipment_ids": _ids(
                evidence, ("shipment_id", "shipment_ids", "tracking_id", "tracking_code")
            ),
        },
        "claim_assessments": claim_assessments,
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": cause, "rank": 1}],
            "responsible_parties": responsible_parties,
        },
        "evidence_refs": relevant_refs,
        "data_conflicts": conflicts,
        "financial_resolution": financial,
        "resolution_actions": actions,
    }
    consumed_refs = {ref for refs in actor_refs.values() for ref in refs}
    if not relevant_refs or not set(relevant_refs) <= consumed_refs:
        raise ValueError(f"{case_id}: output evidence is missing or was not consumed")
    if (
        sum(line["amount_brl"] for line in financial["refund_lines"])
        != financial["recommended_refund_brl"]
    ):
        raise ValueError(f"{case_id}: refund lines do not match recommended total")
    if case_status == "no_action" and financial["recommended_refund_brl"] != 0:
        raise ValueError(f"{case_id}: no_action case cannot recommend a refund")

    policy_refs = actor_refs["policy-agent"]
    trace.emit(
        case_id=case_id,
        event_type="policy_decided",
        actor="policy-agent",
        target="verifier",
        decision_code=actions[0],
        evidence_refs=policy_refs,
        attributes={"primary_issue": issue, "case_status": case_status},
    )
    trace.contracts.validate_output(output, f"workflow output for {case_id}")
    trace.emit(
        case_id=case_id,
        event_type="verification_completed",
        actor="verifier",
        target="coordinator",
        decision_code=issue.upper(),
        evidence_refs=relevant_refs[:20],
        attributes={"confidence": confidence, "evidence_count": len(relevant_refs)},
    )
    return output
