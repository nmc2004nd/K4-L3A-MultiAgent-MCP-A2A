# Hướng dẫn đóng góp cho kho mã nguồn

## Cấu trúc dự án và tổ chức mô-đun

Mã nguồn ứng dụng nằm trong `src/student_agent/`. Điểm mở rộng chính là
`workflow.py`; các mô-đun hỗ trợ phụ trách CLI, tải case, truy cập MCP, kiểm tra
contract, ghi trace và đóng gói bài nộp. Các bài kiểm thử nằm trong `tests/`.
JSON Schema công khai, chính sách chấm điểm và metadata của variant nằm trong
`contracts/`. Ghi lại các quyết định thiết kế trong `ARCHITECTURE.md`.

`inputs/`, `outputs/` và `traces/` chứa dữ liệu cục bộ của cuộc thi và đều bị Git
bỏ qua, ngoại trừ `.gitkeep`. Không commit `case-set.json`, tệp ZIP đã tải xuống,
bài nộp được tạo tự động hoặc `.env`.

## Lệnh build, kiểm thử và phát triển

Sử dụng Python 3.11 trở lên:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
pytest -q
ruff check .
```

`day09 --help` xác nhận gói đã được cài ở chế độ editable. Chạy
`day09 validate-inputs` sau khi nạp case, `day09 run` để tạo kết quả và
`day09 validate` để kiểm tra output cùng trace. Lệnh
`day09 package --output dist/submission.zip` tạo tệp bài nộp. Dùng
`day09 mcp-tools` để khám phá các công cụ gateway thay vì đoán tên công cụ.

## Phong cách lập trình và quy ước đặt tên

Tuân theo quy ước Python hiện đại: thụt lề bốn dấu cách, sử dụng type annotation
và giữ mỗi mô-đun có trách nhiệm rõ ràng. Ruff áp dụng giới hạn 100 ký tự mỗi dòng
cùng các nhóm luật `E`, `F`, `I`, `UP`, `B` và `SIM`. Dùng `snake_case` cho mô-đun,
hàm và biến; `PascalCase` cho lớp; `UPPER_SNAKE_CASE` cho hằng số. Giữ ranh giới
async rõ ràng đối với các thao tác MCP.

## Hướng dẫn kiểm thử

Dự án dùng pytest. Tệp kiểm thử có dạng `tests/test_*.py`, còn hàm kiểm thử có tên
`test_*`. Bổ sung các test tập trung vào kiểm tra contract, xử lý lỗi và phạm vi
evidence. Dùng `tmp_path` cho các tình huống liên quan đến hệ thống tệp và
`pytest.raises` cho lỗi mong đợi. Chạy `pytest -q` và `ruff check .` trước khi mở
pull request. Test an toàn phát hành yêu cầu không có dữ liệu cuộc thi trong bản
checkout dùng để phân phối.

## Quy ước commit và pull request

Lịch sử dự án dùng tiêu đề Conventional Commit ngắn như `feat:`, `docs:` và
`chore:`. Mỗi commit nên có phạm vi rõ ràng và dùng câu mệnh lệnh. Pull request cần
mô tả thay đổi hành vi, liệt kê các lệnh đã dùng để xác minh, liên kết issue hoặc
nhiệm vụ liên quan, đồng thời cập nhật `ARCHITECTURE.md` khi vai trò agent, quá
trình bàn giao hoặc luồng evidence thay đổi. Chỉ đính kèm output mẫu đã loại bỏ
thông tin nhạy cảm khi cần làm rõ hành vi.

## Bảo mật và tính toàn vẹn của evidence

Chỉ lưu API key thật trong `.env`. Luôn truyền đúng `case_id`, giữ nguyên giá trị
`evidence_ref` từ MCP và không tái sử dụng evidence giữa các case. Chỉ ghi vào
trace các sự kiện quan sát được và mã quyết định; không ghi bí mật hoặc nội dung
suy luận riêng tư.
