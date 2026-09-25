from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from student_agent.cases import CaseSet
from student_agent.contracts import Contracts
from student_agent.submission import validate_artifacts
from student_agent.trace import TraceWriter
from student_agent.workflow import _classify, solve_case


class FakeGateway:
    def __init__(self, order_status: str = "canceled") -> None:
        self.calls: list[str] = []
        self.order_status = order_status

    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        self.calls.append(tool_name)
        data: Any = {
            "get_order": {"order_status": self.order_status},
            "get_order_items": {
                "items": [
                    {
                        "order_item_id": 1,
                        "seller_id": "SELLER_1",
                        "price": 90,
                        "freight_value": 10,
                    }
                ]
            },
            "get_order_payments": {
                "payments": [
                    {
                        "payment_reference": "PAY_1",
                        "payment_value": 100,
                    }
                ]
            },
            "get_shipment_summary": {"shipment_id": "SHIP_1"},
            "get_sellers": {"sellers": [{"seller_id": "SELLER_1"}]},
            "get_refund_timeline": {"refund_status": "not_started"},
            "get_policy": {"policy_version": "EC_POLICY_V1"},
        }[tool_name]
        return {
            "schema_version": "day09-mcp-evidence-v1",
            "evidence_ref": f"ev_{tool_name.replace('_', ''):0<24}",
            "result_hash": f"sha256:{'0' * 64}",
            "domain": "policy" if tool_name == "get_policy" else "order",
            "data": data,
        }


class FailingGateway:
    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        del tool_name, case_id, arguments
        raise RuntimeError("gateway unavailable")


class OptionalTimelineFailureGateway(FakeGateway):
    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        if tool_name == "get_refund_timeline":
            raise RuntimeError("timeline unavailable")
        return await super().call(tool_name, case_id=case_id, **arguments)


def evidence(**tools: Any) -> dict[str, dict[str, Any]]:
    return {tool: {"data": data} for tool, data in tools.items()}


def test_classifier_prefers_structured_duplicate_verdict_over_numeric_mismatch() -> None:
    result = _classify(
        evidence(
            get_order_items={"items": [{"price": 90, "freight_value": 10}]},
            get_order_payments={"payments": [{"payment_value": 178}]},
            get_payment_timeline={"verdict": "duplicate_capture"},
        ),
        "duplicate_charge",
    )

    assert result[0] == "duplicate_charge"


def test_classifier_recognizes_reconciled_split_payment() -> None:
    result = _classify(
        evidence(
            get_order_items={"items": [{"price": 90, "freight_value": 10}]},
            get_order_payments={"payments": [{"payment_value": 40}, {"payment_value": 60}]},
            get_payment_timeline={"verdict": "reconciled"},
        ),
        "valid_split_payment",
    )

    assert result[0] == "valid_split_payment"


def test_classifier_compares_seller_deadline_with_shipment_handoff() -> None:
    result = _classify(
        evidence(
            get_order={"order_status": "delivered"},
            get_order_items={"items": [{"shipping_limit_date": "2018-01-02T00:00:00Z"}]},
            get_order_payments={"payments": [{"payment_value": 100}]},
            get_shipment_summary={"carrier_handoff_at": "2018-01-03T00:00:00Z"},
        ),
        "late_delivery_seller",
    )

    assert result[0] == "late_delivery_seller"


def test_classifier_does_not_use_ambiguous_nested_total_as_order_total() -> None:
    result = _classify(
        evidence(
            get_order={"order_status": "delivered"},
            get_order_items={
                "total_brl": 90,
                "items": [{"price": 90, "freight_value": 10}],
            },
            get_order_payments={"payments": [{"payment_value": 100}]},
            get_shipment_summary={"verdict": "on_time"},
        ),
        "unsupported_claim",
    )

    assert result[0] == "unsupported_claim"


def test_solve_case_builds_valid_evidence_linked_output(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace = TraceWriter(tmp_path / "traces" / "trace.jsonl", contracts)
    gateway = FakeGateway()
    case = {
        "case_id": "L3A_CASE_TEST",
        "policy_version": "EC_POLICY_V1",
        "customer_request": {
            "claimed_order_id": "ORDER_1",
            "claims": [
                {"claim_id": "claim-a", "topic": "canceled_order_paid"},
                {"claim_id": "claim-b", "topic": "requested_full_refund"},
            ],
        },
    }

    trace.emit(case_id=case["case_id"], event_type="case_received", actor="coordinator")
    output = asyncio.run(solve_case(case, gateway, trace))  # type: ignore[arg-type]
    trace.emit(case_id=case["case_id"], event_type="case_finalized", actor="coordinator")

    contracts.validate_output(output, "test output")
    assert output["assessment"]["primary_issue"] == "canceled_order_paid"
    assert output["financial_resolution"]["recommended_refund_brl"] == 100
    assert output["claim_assessments"][1]["verdict"] == "supported"
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    assert {event["event_type"] for event in events} >= {
        "task_assigned",
        "tool_result_consumed",
        "handoff",
        "policy_decided",
        "verification_completed",
    }
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / f"{case['case_id']}.json").write_text(json.dumps(output), encoding="utf-8")
    case_set = CaseSet("test-v1", "l3a", (case["case_id"],), {case["case_id"]: case})
    validate_artifacts(tmp_path, case_set, contracts)


def test_solve_case_fails_instead_of_emitting_evidence_free_output(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace = TraceWriter(tmp_path / "trace.jsonl", contracts)
    case = {
        "case_id": "L3A_CASE_TEST",
        "policy_version": "EC_POLICY_V1",
        "customer_request": {
            "claimed_order_id": "ORDER_1",
            "claims": [{"claim_id": "claim-a", "topic": "canceled_order_paid"}],
        },
    }

    with pytest.raises(RuntimeError, match="required MCP tool get_order failed"):
        asyncio.run(solve_case(case, FailingGateway(), trace))  # type: ignore[arg-type]


def test_optional_refund_timeline_failure_does_not_discard_core_evidence(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace = TraceWriter(tmp_path / "trace.jsonl", contracts)
    case = {
        "case_id": "L3A_CASE_TEST",
        "policy_version": "EC_POLICY_V1",
        "customer_request": {
            "claimed_order_id": "ORDER_1",
            "claims": [{"claim_id": "claim-a", "topic": "canceled_order_paid"}],
        },
    }

    output = asyncio.run(  # type: ignore[arg-type]
        solve_case(case, OptionalTimelineFailureGateway(), trace)
    )

    assert output["assessment"]["primary_issue"] == "canceled_order_paid"
    assert output["evidence_refs"]
    assert output["assessment"]["confidence"] < 0.95


def test_unavailable_order_does_not_query_out_of_scope_shipment(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace = TraceWriter(tmp_path / "trace.jsonl", contracts)
    gateway = FakeGateway(order_status="unavailable")
    case = {
        "case_id": "L3A_CASE_TEST",
        "policy_version": "EC_POLICY_V1",
        "customer_request": {
            "claimed_order_id": "ORDER_1",
            "claims": [{"claim_id": "claim-a", "topic": "unavailable_order_paid"}],
        },
    }

    output = asyncio.run(solve_case(case, gateway, trace))  # type: ignore[arg-type]

    assert output["assessment"]["primary_issue"] == "unavailable_order_paid"
    assert "get_shipment_summary" not in gateway.calls


def test_no_action_case_still_emits_policy_decision(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace = TraceWriter(tmp_path / "trace.jsonl", contracts)
    case = {
        "case_id": "L3A_CASE_TEST",
        "policy_version": "EC_POLICY_V1",
        "customer_request": {
            "claimed_order_id": "ORDER_1",
            "claims": [{"claim_id": "claim-a", "topic": "unsupported_claim"}],
        },
    }

    output = asyncio.run(  # type: ignore[arg-type]
        solve_case(case, FakeGateway(order_status="delivered"), trace)
    )

    assert output["assessment"]["case_status"] == "no_action"
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    assert sum(event["event_type"] == "policy_decided" for event in events) == 1
