# Hồ sơ kiến trúc L3A

Tài liệu mô tả các quyết định có thể kiểm chứng của workflow. Hệ thống không ghi
prompt, API key hoặc nội dung suy luận riêng tư.

## 1. Tổng quan hệ thống

```text
Input → Coordinator → Order / Payment / Shipment / Policy agents
                            │
                            └── MCP Evidence Gateway
                                      │
                            Verifier → Output + Trace
```

`solve_case()` xác thực định danh đầu vào, giao nhiệm vụ theo domain, thu thập
evidence và chuyển các kết quả cho verifier. Verifier áp dụng các quy tắc xác định
trạng thái order, độ trễ, đối soát tiền và refund, sau đó kiểm tra output theo JSON
Schema trước khi trả về CLI.

`contracts/scoring/scoring-policy-v2.json` chỉ quy định cách chấm và hard gate.
Quyền lợi nghiệp vụ được lấy từ evidence `get_policy` theo `policy_version`; nếu
payload policy có cause, responsible party hoặc action cho issue, các giá trị đó
được ưu tiên sau khi kiểm tra đúng enum/giới hạn của output schema.

## 2. Quyền sở hữu của agent

| Actor | Đầu vào | Trách nhiệm | Kết quả bàn giao |
| --- | --- | --- | --- |
| Coordinator | Case và claim | Xác thực, phân phạm vi, tổng hợp | Nhiệm vụ theo domain |
| Order agent | Order ID | Order, item và seller | Entity, trạng thái, deadline |
| Payment agent | Order ID | Payment và refund timeline | Tổng tiền, trạng thái refund |
| Shipment agent | Order ID của claim giao nhận/unsupported | Mốc giao nhận | Phân loại seller/logistics delay |
| Policy agent | Policy version | Đọc policy MCP, chọn quyền lợi và hành động | Policy evidence |
| Verifier | Các handoff | Kiểm tra chéo, chọn issue và refund | Output hợp lệ |

Mỗi specialist chỉ gọi tool thuộc domain đã giao. Shipment agent không được tạo
cho canceled, unavailable, payment hoặc refund case vì các đơn đó có thể không có
shipment. Customer history và product context không được gọi vì L3A đã cung cấp
order ID và hai nguồn này không cần cho kết luận hiện tại.

## 3. Giao thức A2A

Correlation key là `case_id`; order và policy ID luôn lấy từ chính case đó. Một
nhiệm vụ gồm actor, danh sách tool và mục tiêu `INVESTIGATE_DOMAIN`. Handoff chỉ
chứa decision code cùng evidence refs quan sát được. Mỗi tool có timeout 90 giây;
tool cốt lõi có tối đa hai retry với backoff 1/2 giây, timeline bổ trợ có một
retry. Lỗi ở tool cốt lõi làm cả run dừng; timeline bổ trợ có thể bỏ qua sau retry
và làm giảm confidence. Refund timeline vẫn là bắt buộc với
`refund_pending` và `refund_failed`. Không có vòng lặp giữa các agent. Các event
`task_assigned`, `tool_result_consumed`, `handoff`, `policy_decided` và
`verification_completed` thể hiện vòng đời công việc.

## 4. Vòng đời evidence

Gateway kiểm tra mọi response bằng `mcp-evidence-response-v1`. Workflow giữ
`evidence_ref` nguyên trạng, gắn nó vào event tiêu thụ và chỉ đưa các domain hỗ
trợ kết luận vào output. Evidence được lưu trong phạm vi lời gọi `solve_case()`,
không có cache liên case. Entity ID chỉ được trích từ evidence của case hiện tại.

## 5. Chính sách lỗi

| Lỗi | Retry | Fallback | Decision code |
| --- | --- | --- | --- |
| Tool cốt lõi timeout/lỗi | Hai lần, có backoff | Dừng case và run | Không tạo output |
| Timeline bổ trợ lỗi | Một lần | Dùng evidence cốt lõi, giảm confidence | Không trích dẫn timeline |
| Không tìm thấy | Một lần | Dừng case và run | Không tạo output |
| Nguồn mâu thuẫn | Không | Ưu tiên timeline chuyên biệt | Issue đã xác minh |
| Response sai schema | Không | Dừng ngay | Không ghi evidence ref |

Không tạo evidence ref, số tiền hoặc trạng thái thay thế khi nguồn bị thiếu.

## 6. Bất biến xác minh

Trước khi finalize, verifier kiểm tra schema, `case_id`, phạm vi entity, liên kết
claim–evidence, mọi output ref đã có `tool_result_consumed`, tổng payment so với
giá item và freight, tổng refund line bằng số tiền hoàn, trách nhiệm phù hợp
nguyên nhân và action nhất quán với trạng thái case. Split payment chỉ hợp lệ khi
nhiều payment cộng lại khớp tổng đơn; nhiều payment không tự động bị coi là
duplicate. Payment verdict/boolean flag có thẩm quyền được ưu tiên trước phép đối
soát số học. Seller delay so sánh `shipping_limit_date` của item với carrier
handoff từ shipment/order. Confidence bắt đầu từ sức mạnh tín hiệu và độ đầy đủ
evidence rồi giảm theo conflict và warning; suy luận mismatch thuần số học bị giới
hạn ở 0,70, còn giá trị tối đa toàn cục là 0,95.

## 7. Khả năng tái lập

Workflow là rule-based, không dùng model, random seed hay trạng thái toàn cục.
Runtime yêu cầu Python 3.11+, dependency theo `pyproject.toml`, xử lý tuần tự từng
case và từng domain. Chạy `day09 run`, sau đó `day09 validate`; dùng
`ruff check .` và `pytest -q` để kiểm tra mã nguồn.
