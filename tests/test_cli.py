from __future__ import annotations

from student_agent.cli import _contains_transport_error


def test_transport_error_detection_handles_exception_groups() -> None:
    error = ExceptionGroup("mcp connection closed", [OSError("connection reset")])

    assert _contains_transport_error(error)


def test_transport_error_detection_does_not_retry_business_errors() -> None:
    assert not _contains_transport_error(ValueError("invalid MCP evidence"))