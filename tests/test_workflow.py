from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from student_agent.cases import load_case_set
from student_agent.contracts import Contracts
from student_agent.submission import validate_artifacts
from student_agent.trace import TraceWriter
from student_agent.workflow import solve_case


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        self.calls.append((tool_name, case_id, arguments))
        index = len(self.calls)
        return {
            "schema_version": "day09-mcp-evidence-v1",
            "evidence_ref": f"ev_{'a' * 20}{index}",
            "result_hash": "sha256:" + "b" * 64,
            "domain": "payment" if "payment" in tool_name else "order",
            "data": {
                "order_id": arguments.get("order_id"),
                "order_status": "canceled",
                "payment_total": 42.0,
            },
        }


class ConflictingPaymentGateway(FakeGateway):
    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        evidence = await super().call(tool_name, case_id=case_id, **arguments)
        if tool_name == "get_order":
            evidence["data"]["order_total"] = 99.0
        return evidence


def test_workflow_produces_contract_valid_evidence_backed_output(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace_path = tmp_path / "trace.jsonl"
    gateway = FakeGateway()
    case = {
        "case_id": "CASE_001",
        "policy_version": "EC_POLICY_V1",
        "customer_request": {
            "claimed_order_id": "order-1",
            "claims": [{"claim_id": "claim-1", "topic": "canceled_order_paid"}],
        },
    }

    output = asyncio.run(solve_case(case, gateway, TraceWriter(trace_path, contracts)))

    contracts.validate_output(output, "test output")
    assert output["assessment"]["primary_issue"] == "canceled_order_paid"
    assert gateway.calls and all(case_id == "CASE_001" for _, case_id, _ in gateway.calls)
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert {event["event_type"] for event in events} >= {
        "task_assigned", "tool_result_consumed", "handoff", "policy_decided", "verification_completed"
    }


def test_verifier_blocks_refund_and_reduces_confidence_for_conflicting_amounts(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    case = {
        "case_id": "CASE_002", "policy_version": "EC_POLICY_V1",
        "customer_request": {"claimed_order_id": "order-2", "claims": [{"claim_id": "claim-2", "topic": "canceled_order_paid"}]},
    }

    output = asyncio.run(solve_case(case, ConflictingPaymentGateway(), TraceWriter(tmp_path / "trace.jsonl", contracts)))

    assert output["root_cause_analysis"]["responsible_parties"] == [{"party_type": "platform", "party_id": None}]
    assert output["financial_resolution"] == {"currency": "BRL", "recommended_refund_brl": 0.0, "refund_lines": []}
    assert output["data_conflicts"]
    assert output["assessment"]["confidence"] < 0.9


def test_all_l3a_cases_produce_valid_outputs_and_observable_trace(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "l3a-inputs-v1"
    case_set = load_case_set(source)
    contracts = Contracts(root / "contracts" / "schemas")
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    trace = TraceWriter(tmp_path / "traces" / "trace.jsonl", contracts)
    gateway = FakeGateway()

    for case_id in case_set.case_ids:
        trace.emit(case_id=case_id, event_type="case_received", actor="coordinator")
        output = asyncio.run(solve_case(case_set.cases[case_id], gateway, trace))
        (output_root / f"{case_id}.json").write_text(json.dumps(output), encoding="utf-8")
        trace.emit(case_id=case_id, event_type="case_finalized", actor="coordinator")

    (tmp_path / "case-set.json").write_text((source / "case-set.json").read_text(encoding="utf-8"), encoding="utf-8")
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    for case_id in case_set.case_ids:
        (input_root / f"{case_id}.json").write_text(
            json.dumps(case_set.cases[case_id]), encoding="utf-8"
        )

    outputs, trace_lines = validate_artifacts(tmp_path, load_case_set(tmp_path), contracts)
    assert len(outputs) == 100
    assert len(trace_lines) >= 400
    assert all(output["evidence_refs"] for output in outputs.values())