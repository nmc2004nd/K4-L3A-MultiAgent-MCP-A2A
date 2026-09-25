# L3A — Minimum Completion Checklist

Mục tiêu: hoàn thiện một submission hợp lệ cho bài Multi-Agent MCP + A2A. Chỉ đánh dấu khi hạng mục đã được kiểm tra thực tế.

## 1. Môi trường và dữ liệu

- [x] Cài dependencies: `python -m pip install -e ".[dev]"`.
- [x] Tạo `.env` từ `.env.example` và điền `COMPETITION_API_URL`, `COMPETITION_TEAM_API_KEY`, `MCP_ENDPOINT` hợp lệ. Không commit API key.
- [x] Tải và giải nén bộ input L3A tại root repo, gồm `case-set.json` và thư mục `inputs/`.
- [x] Chạy `day09 validate-inputs` thành công.
- [x] Chạy `day09 mcp-tools` để discovery tên tool MCP thật trước khi code workflow.

## 2. Workflow bắt buộc

- [x] Hoàn thiện `src/student_agent/workflow.py`, đặc biệt `async def solve_case(case, gateway, trace) -> dict`.
- [x] Có coordinator phân loại case và handoff theo `case_id` đến các specialist cần thiết: order/item, payment, shipment và policy.
- [x] Specialist chỉ gọi các MCP tool cần thiết; luôn gửi đúng `case_id` và ID entity từ case/evidence.
- [x] Validate MCP response trước khi sử dụng; chỉ lưu và trích dẫn `evidence_ref` do MCP trả về.
- [x] Emit trace `tool_result_consumed` khi evidence được dùng để tạo claim/kết luận.
- [x] Có verifier kiểm tra evidence cùng case, claim được evidence hỗ trợ, consistency (money/responsibility/action) và confidence trước khi trả output.
- [x] Không suy đoán dữ liệu khi MCP thiếu dữ liệu, lỗi hoặc không tìm thấy entity; trả kết quả/fallback phù hợp với contract và trace sự kiện lỗi.

## 3. Output và trace

- [x] Mỗi case sinh được `outputs/<case_id>.json` đúng schema/public contract (được kiểm tra trên toàn bộ 100 input bằng artifact-validation test).
- [x] Sinh `traces/trace.jsonl` có actor, event type, tool name (khi có) và evidence refs đã tiêu thụ (được kiểm tra bằng artifact-validation test).
- [x] Không dùng `evidence_ref` tự tạo, không dùng evidence từ case/run/team khác, và không trích evidence không hỗ trợ kết luận (workflow chỉ đưa evidence ref nguyên gốc từ gateway vào output/trace; provenance MCP thật cần xác nhận lại qua `day09 validate` sau `day09 run`).

## 4. Tài liệu thiết kế

- [ ] Thay toàn bộ `TODO` trong `ARCHITECTURE.md` bằng mô tả đúng với implementation.
- [ ] Ghi rõ system flow, ownership/quyền tool của từng agent, A2A handoff, evidence lifecycle, failure policy, invariants và reproducibility.
- [ ] Không đưa prompt bí mật, chain-of-thought hoặc API key vào tài liệu.

## 5. Kiểm tra và nộp bài

- [ ] Chạy `pytest -q` thành công.
- [ ] Chạy `day09 run` thành công trên toàn bộ input được cung cấp.
- [ ] Chạy `day09 validate` thành công.
- [ ] Đọc ngẫu nhiên một số output và trace để kiểm tra kết luận có evidence thật và đúng case.
- [ ] Tạo package: `day09 package --output dist/submission.zip`.
- [ ] Kiểm tra ZIP chỉ chứa `manifest.json`, `trace.jsonl` và `outputs/<case_id>.json`; không chứa source, inputs, `.env`, API key hoặc debug log.