from __future__ import annotations

"""Evidence-first, observable multi-agent workflow for L3A."""

from collections.abc import Iterable
from typing import Any

from .mcp_gateway import EvidenceGateway
from .trace import TraceWriter

PAYMENT_TOPICS = {"canceled_order_paid", "valid_split_payment", "payment_mismatch", "duplicate_charge"}
SHIPMENT_TOPICS = {"late_delivery_seller", "late_delivery_logistics"}
REFUND_TOPICS = {"refund_pending", "refund_failed", "requested_full_refund"}
REFUNDABLE_TOPICS = {"canceled_order_paid", "unavailable_order_paid", "payment_mismatch", "duplicate_charge"}
RESPONSIBLE_PARTIES = {
    "canceled_order_paid": "platform",
    "unavailable_order_paid": "seller",
    "late_delivery_seller": "seller",
    "late_delivery_logistics": "logistics_provider",
    "payment_mismatch": "payment_provider",
    "duplicate_charge": "payment_provider",
    "refund_pending": "payment_provider",
    "refund_failed": "payment_provider",
    "valid_split_payment": "unknown",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first(data: Any, *keys: str) -> Any:
    source = _as_dict(data)
    for key in keys:
        if source.get(key) is not None:
            return source[key]
    for value in source.values():
        nested = _as_dict(value)
        for key in keys:
            if nested.get(key) is not None:
                return nested[key]
    return None


def _records(data: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [_as_dict(item) for item in data if isinstance(item, dict)]
    source = _as_dict(data)
    for key in keys:
        if isinstance(source.get(key), list):
            return [_as_dict(item) for item in source[key] if isinstance(item, dict)]
    return []


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value and value not in result:
            result.append(value)
    return result[:20]


async def _collect(
    gateway: EvidenceGateway, trace: TraceWriter, *, case_id: str, actor: str,
    tool_name: str, **arguments: str,
) -> dict[str, Any] | None:
    """Call MCP once, consuming returned evidence or recording a bounded failure."""
    try:
        evidence = await gateway.call(tool_name, case_id=case_id, **arguments)
    except (RuntimeError, ValueError, OSError):
        trace.emit(case_id=case_id, event_type="handoff", actor=actor, target="verifier",
                   decision_code="EVIDENCE_UNAVAILABLE", tool_name=tool_name)
        return None
    reference = evidence.get("evidence_ref")
    if not isinstance(reference, str):
        return None
    trace.emit(case_id=case_id, event_type="tool_result_consumed", actor=actor,
               tool_name=tool_name, evidence_refs=[reference])
    return evidence


def _status(data: Any) -> str:
    value = _first(data, "order_status", "status")
    return value.lower() if isinstance(value, str) else ""


def _payment_total(data: Any) -> float | None:
    direct = _number(_first(data, "payment_total", "total_paid", "amount_paid", "paid_amount"))
    if direct is not None:
        return direct
    amounts = [_number(_first(item, "payment_value", "amount", "value", "paid_amount"))
               for item in _records(data, "payments", "payment_records", "data")]
    known = [amount for amount in amounts if amount is not None]
    return sum(known) if known else None


def _amount(data: Any, *keys: str) -> float | None:
    value = _number(_first(data, *keys))
    return round(value, 2) if value is not None and value >= 0 else None


def _evidence_data(evidence: dict[str, dict[str, Any]], *tool_names: str) -> Any:
    """Return the first collected payload for a tool group, without cross-domain guessing."""
    for tool_name in tool_names:
        data = _as_dict(evidence.get(tool_name, {})).get("data")
        if data is not None:
            return data
    return {}


def _claim_supported(topic: str, evidence: dict[str, dict[str, Any]]) -> bool | None:
    order = _evidence_data(evidence, "get_order")
    payment = _evidence_data(evidence, "get_order_payments", "get_payment_timeline")
    shipment = _evidence_data(evidence, "get_shipment_summary")
    refund = _evidence_data(evidence, "get_refund_timeline")
    status, paid = _status(order), _payment_total(payment)
    if topic == "canceled_order_paid":
        return status == "canceled" and paid is not None and paid > 0
    if topic == "unavailable_order_paid":
        return status == "unavailable" and paid is not None and paid > 0
    if topic == "valid_split_payment":
        count = _first(payment, "payment_count", "installments", "payment_installments")
        return isinstance(count, int) and count > 1
    if topic in {"duplicate_charge", "payment_mismatch"}:
        flag = _first(payment, "duplicate_charge" if topic == "duplicate_charge" else "payment_mismatch",
                      "is_duplicate" if topic == "duplicate_charge" else "is_mismatch")
        return flag if isinstance(flag, bool) else None
    if topic in SHIPMENT_TOPICS:
        owner = _first(shipment, "delay_responsibility", "responsible_party", "delay_owner")
        expected = "seller" if topic == "late_delivery_seller" else "logistics_provider"
        return owner.lower() == expected if isinstance(owner, str) else None
    if topic in {"refund_pending", "refund_failed"}:
        value = _first(refund, "refund_status", "status")
        return value.lower() == topic.removeprefix("refund_") if isinstance(value, str) else None
    return None


def _entities(order_data: Any, item_data: Any, payment_data: Any, shipment_data: Any) -> dict[str, list[str]]:
    items = _records(item_data, "items", "order_items")
    return {
        "order_ids": _unique_strings([_first(order_data, "order_id")]),
        "item_ids": _unique_strings(_first(item, "order_item_id", "item_id", "id") for item in items),
        "seller_ids": _unique_strings(_first(item, "seller_id") for item in items),
        "payment_references": _unique_strings(_first(row, "payment_id", "payment_reference", "transaction_id") for row in _records(payment_data, "payments", "payment_records")),
        "shipment_ids": _unique_strings(_first(row, "shipment_id", "tracking_id", "id") for row in _records(shipment_data, "shipments")),
    }


def _claim_refs(topic: str, evidence: dict[str, dict[str, Any]]) -> list[str]:
    tools = {
        "canceled_order_paid": ("get_order", "get_order_payments"),
        "unavailable_order_paid": ("get_order", "get_order_payments", "get_order_items"),
        "valid_split_payment": ("get_order_payments", "get_payment_timeline"),
        "payment_mismatch": ("get_order_payments", "get_payment_timeline"),
        "duplicate_charge": ("get_order_payments", "get_payment_timeline"),
        "late_delivery_seller": ("get_order", "get_order_items", "get_shipment_summary"),
        "late_delivery_logistics": ("get_order", "get_shipment_summary"),
        "refund_pending": ("get_refund_timeline", "get_policy"),
        "refund_failed": ("get_refund_timeline", "get_policy"),
        "requested_full_refund": ("get_policy", "get_order_payments"),
    }.get(topic, ("get_order",))
    return _unique_strings(_as_dict(evidence.get(tool, {})).get("evidence_ref") for tool in tools)


def _conflicts(evidence: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Report material disagreement rather than selecting an unsupported amount."""
    payment_total = _payment_total(_evidence_data(evidence, "get_order_payments", "get_payment_timeline"))
    order_total = _amount(_evidence_data(evidence, "get_order"), "order_total", "total_amount", "total_paid", "payment_total")
    if payment_total is None or order_total is None or abs(payment_total - order_total) < 0.01:
        return []
    return [{
        "field": "paid_amount_brl",
        "sources": ["get_order", "get_order_payments"],
        "selected_source": None,
        "resolution_code": "MANUAL_RECONCILIATION_REQUIRED",
    }]


def _refund_amount(primary: str, evidence: dict[str, dict[str, Any]], conflicts: list[dict[str, Any]]) -> float:
    if primary not in REFUNDABLE_TOPICS or conflicts:
        return 0.0
    payment = _evidence_data(evidence, "get_order_payments", "get_payment_timeline")
    amount = _amount(payment, "refund_amount", "duplicate_amount", "mismatch_amount", "amount_to_refund")
    paid = _payment_total(payment)
    if primary in {"canceled_order_paid", "unavailable_order_paid"}:
        amount = paid
    if amount is None or paid is None:
        return 0.0
    return round(min(amount, paid), 2)


def _calibrated_confidence(
    primary: str, claim_refs: list[str], all_refs: list[str], conflicts: list[dict[str, Any]],
) -> float:
    """Conservative calibration: incomplete or contradictory evidence cannot be high confidence."""
    if primary == "insufficient_evidence":
        return 0.3 if all_refs else 0.15
    coverage = min(len(claim_refs), 3) / 3
    confidence = 0.55 + 0.3 * coverage + (0.05 if all_refs else 0.0) - 0.25 * len(conflicts)
    return round(max(0.1, min(confidence, 0.95)), 2)


def _policy_resolution(
    primary: str, evidence: dict[str, dict[str, Any]], refs: list[str], conflicts: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    party_type = RESPONSIBLE_PARTIES.get(primary, "unknown")
    entities = _entities(
        _evidence_data(evidence, "get_order"), _evidence_data(evidence, "get_order_items"),
        _evidence_data(evidence, "get_order_payments"), _evidence_data(evidence, "get_shipment_summary"),
    )
    party_id = entities["seller_ids"][0] if party_type == "seller" and entities["seller_ids"] else None
    refund = _refund_amount(primary, evidence, conflicts)
    lines = ([{"reason_code": primary.upper(), "amount_brl": refund, "entity_id": entities["order_ids"][0] if entities["order_ids"] else None}]
             if refund > 0 else [])
    if conflicts:
        actions = ["manual_financial_reconciliation"]
    elif refund > 0:
        actions = ["issue_refund", "notify_customer"]
    elif primary == "valid_split_payment":
        actions = ["explain_valid_payment_schedule"]
    elif primary == "insufficient_evidence":
        actions = ["manual_evidence_review"]
    else:
        actions = ["investigate_responsible_party", "notify_customer"]
    return (
        {"currency": "BRL", "recommended_refund_brl": refund, "refund_lines": lines},
        actions,
        {"party_type": party_type, "party_id": party_id},
    )


async def solve_case(case: dict[str, Any], gateway: EvidenceGateway, trace: TraceWriter) -> dict[str, Any]:
    """Coordinate scoped specialists and return only evidence-backed conclusions."""
    case_id = case.get("case_id")
    request = _as_dict(case.get("customer_request"))
    order_id = request.get("claimed_order_id")
    claims = [claim for claim in _as_list(request.get("claims")) if isinstance(claim, dict)]
    topics = {claim.get("topic") for claim in claims if isinstance(claim.get("topic"), str)}
    if not isinstance(case_id, str) or not isinstance(order_id, str):
        raise ValueError("case must contain case_id and customer_request.claimed_order_id")

    evidence: dict[str, dict[str, Any]] = {}
    trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="order-agent", decision_code="VERIFY_ORDER")
    order = await _collect(gateway, trace, case_id=case_id, actor="order-agent", tool_name="get_order", order_id=order_id)
    if order:
        evidence["get_order"] = order

    assignments: list[tuple[str, str, list[str]]] = []
    if topics & (PAYMENT_TOPICS | REFUND_TOPICS):
        assignments.append(("payment-agent", "payment", ["get_order_payments", "get_payment_timeline"]))
    if topics & SHIPMENT_TOPICS:
        assignments.append(("shipment-agent", "shipment", ["get_shipment_summary"]))
    if topics & {"unavailable_order_paid", "late_delivery_seller"}:
        assignments.append(("order-agent", "item", ["get_order_items"]))
    if topics & REFUND_TOPICS:
        assignments.append(("payment-agent", "refund", ["get_refund_timeline"]))
    for actor, domain, tools in assignments:
        trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target=actor, decision_code=f"VERIFY_{domain.upper()}")
        for tool in tools:
            result = await _collect(gateway, trace, case_id=case_id, actor=actor, tool_name=tool, order_id=order_id)
            if result:
                evidence[tool] = result
        trace.emit(case_id=case_id, event_type="handoff", actor=actor, target="verifier", decision_code=f"{domain.upper()}_EVIDENCE_READY")

    policy_version = case.get("policy_version")
    if isinstance(policy_version, str):
        trace.emit(case_id=case_id, event_type="task_assigned", actor="coordinator", target="policy-agent", decision_code="CHECK_POLICY")
        policy = await _collect(gateway, trace, case_id=case_id, actor="policy-agent", tool_name="get_policy", policy_version=policy_version)
        if policy:
            evidence["get_policy"] = policy

    refs = _unique_strings(item.get("evidence_ref") for item in evidence.values())
    claim_assessments, supported_topics = [], []
    for claim in claims[:5]:
        result = _claim_supported(claim.get("topic", ""), evidence)
        verdict = "supported" if result is True else "unsupported" if result is False else "insufficient_evidence"
        if result is True:
            supported_topics.append(claim["topic"])
        claim_assessments.append({"claim_id": claim.get("claim_id", "unknown"), "verdict": verdict,
                                  "confidence": 0.8 if result is not None else 0.35,
                                  "evidence_refs": _claim_refs(claim.get("topic", ""), evidence)})
    primary = supported_topics[0] if supported_topics else "insufficient_evidence"
    conflicts = _conflicts(evidence)
    financial_resolution, actions, responsibility = _policy_resolution(primary, evidence, refs, conflicts)
    primary_refs = _claim_refs(primary, evidence)
    confidence = _calibrated_confidence(primary, primary_refs, refs, conflicts)
    trace.emit(
        case_id=case_id, event_type="policy_decided", actor="policy-agent",
        decision_code="POLICY_RESOLUTION_APPLIED" if "get_policy" in evidence else "POLICY_FALLBACK_APPLIED",
        evidence_refs=_unique_strings([*_claim_refs(primary, evidence), _as_dict(evidence.get("get_policy", {})).get("evidence_ref")]),
    )
    trace.emit(case_id=case_id, event_type="handoff", actor="policy-agent", target="verifier", decision_code="POLICY_DECISION_READY")
    trace.emit(
        case_id=case_id, event_type="verification_completed", actor="verifier",
        decision_code="CONSISTENCY_CONFLICT" if conflicts else "CONSISTENCY_CONFIRMED", evidence_refs=refs,
        attributes={"confidence": confidence, "conflict_count": len(conflicts)},
    )
    return {
        "schema_version": "day09-l3a-output-v2", "case_id": case_id,
        "assessment": {"primary_issue": primary, "case_status": "action_required" if supported_topics else "needs_investigation", "confidence": confidence},
        "affected_entities": _entities(_evidence_data(evidence, "get_order"), _evidence_data(evidence, "get_order_items"), _evidence_data(evidence, "get_order_payments"), _evidence_data(evidence, "get_shipment_summary")),
        "claim_assessments": claim_assessments,
        "root_cause_analysis": {"ranked_causes": [{"cause_code": primary.upper() if supported_topics else "EVIDENCE_INCOMPLETE", "rank": 1}], "responsible_parties": [responsibility]},
        "evidence_refs": refs, "data_conflicts": conflicts,
        "financial_resolution": financial_resolution,
        "resolution_actions": actions,
    }
