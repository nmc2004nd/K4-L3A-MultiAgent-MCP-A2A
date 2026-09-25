from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from student_agent.contracts import Contracts
from student_agent.mcp_gateway import EvidenceGateway


class FakeSession:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def call_tool(self, tool_name: str, *, arguments: dict[str, str]) -> Any:
        self.calls.append((tool_name, arguments))
        return self.result


def test_gateway_supports_current_mcp_snake_case_result_fields() -> None:
    root = Path(__file__).resolve().parents[1]
    evidence = {
        "schema_version": "day09-mcp-evidence-v1",
        "evidence_ref": "ev_" + "a" * 20,
        "result_hash": "sha256:" + "b" * 64,
        "domain": "order",
        "data": {"order_id": "order-1"},
    }
    session = FakeSession(
        SimpleNamespace(is_error=False, structured_content=evidence, content=[])
    )
    gateway = EvidenceGateway(session, Contracts(root / "contracts" / "schemas"))

    actual = asyncio.run(gateway.call("get_order", case_id="CASE_001", order_id="order-1"))

    assert actual == evidence
    assert session.calls == [("get_order", {"case_id": "CASE_001", "order_id": "order-1"})]