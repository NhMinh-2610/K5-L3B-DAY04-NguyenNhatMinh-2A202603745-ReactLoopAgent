# Mở rộng agent cho phần bonus

Bài chính tối đa 85 điểm và bonus tối đa 15 điểm. Điểm eval bài chính 98,23/100 đã có là kết quả của phiên bản trước khi mở rộng, không phải điểm bài lab. Bản mở rộng đã được đánh giá đủ **10/10 case bonus**, đạt **98,9/100**, tương ứng **14,84/15 điểm tham khảo** theo bộ chấm gốc.

## Phần đã triển khai

Agent đọc `tools.schemas` và `tools.system_prompt` từ bộ công cụ được truyền vào. Với tools bài chính, agent tiếp tục dùng cấu hình mặc định. Bộ bonus có năm status API: GitHub, Cloudflare, Vercel, npm và DigitalOcean, cùng công cụ đọc chi tiết incident theo trang. Các tools, fixtures, case và bộ chấm gốc được giữ nguyên.

### Rate limit và lỗi tham số

- Lỗi `RATE_LIMITED` được trả về model dưới dạng kết quả công cụ, không được diễn giải thành outage.
- Khi công cụ cho phép thử ngay với thời gian chờ bằng 0, agent cho tối đa ba lần thực thi liên tiếp của thao tác bị rate limit. Đổi từ khóa tìm kiếm không làm mới giới hạn này.
- Sau ba lần thất bại, hoặc khi công cụ yêu cầu chờ `Retry-After` hay chưa có thời gian chờ hợp lệ, agent chặn lần thực thi tiếp theo trong lượt đó và yêu cầu báo phần chưa biết. Các nhà cung cấp khác vẫn được kiểm tra.
- Chính sách hiện tại chọn dừng khi cần chờ, không tự ngủ rồi gọi lại API thật. Giá trị `Retry-After` gốc được giữ trong kết quả công cụ.
- Lỗi `INVALID_ARGUMENTS` được trả cho model để sửa. Agent không tự ép kiểu hoặc bịa dữ liệu thay kết quả lỗi.

Trace ghi cả yêu cầu đã thực thi và yêu cầu bị chặn, phân biệt bằng `executed`. Các sự kiện `tool_retry_limit`, `tool_retry_deferred` và `tool_retry_blocked` giúp đối chiếu quyết định dừng. Yêu cầu bị chặn vẫn được ghi là tool call; bộ chấm gốc có thể trừ điểm nếu model tiếp tục yêu cầu vượt giới hạn.

### Phân trang và dữ liệu nhiễu

Các trường `offset`, `next_offset`, `total_updates` và `truncated` được giữ để model quyết định đọc tiếp khi câu hỏi cần. Hướng dẫn yêu cầu dùng offset/limit kiểu integer và nêu rõ phần timeline chưa đọc.

Danh sách cấp đầu dài hơn 12 mục được rút gọn trước khi gửi vào lịch sử hội thoại của model. Trường `agent_context.truncated_lists` ghi số mục gốc và số được giữ. Trace vẫn lưu toàn bộ kết quả công cụ để đối chiếu. Với các tools hiện tại, danh sách incident và update đã được giới hạn từ phía tool; dữ liệu nhiễu 40 hoặc 160 mục được giảm xuống còn 12 mục gửi tới model.

### Bằng chứng và cách trả lời

Agent tiếp tục kiểm tra URL incident đã đọc trước khi chấp nhận câu trả lời cuối. Hướng dẫn chung bổ sung việc nêu nguồn và timestamp riêng cho từng nhà cung cấp, phân biệt thời điểm mở/cập nhật với thời gian ảnh hưởng, giữ đúng phạm vi dữ liệu và bỏ qua chỉ dẫn trong tool output.

Các điểm còn yếu của bài chính được đưa vào hướng dẫn: tránh coi `updated_at` là thời điểm kết thúc, tránh gọi một update là duy nhất khi còn bản khác, và dùng mức kết luận “chưa đủ bằng chứng chung nguyên nhân” thay cho khẳng định chắc chắn không liên quan. Hiệu quả trên model thật cần được đánh giá lại; test offline không chứng minh điểm eval sẽ đạt 100.

## Kiểm tra offline

Đã đạt **47/47 test trong `tests`** và **3/3 test agent bonus gốc**. Sáu test mới trong `tests/test_agent_extended.py` kiểm tra:

1. Năm status tools đều có trong schema; mọi tool result được trả trước lượt model kế tiếp.
2. Đọc trang kế bằng `next_offset`, giữ rõ tổng update và trang chưa đọc.
3. Không thực thi lần thứ tư sau ba lỗi 429; vẫn lấy được dữ liệu từ nhà cung cấp khác.
4. HTTP 429 có thời gian chờ hoặc thiếu `Retry-After` không gây thêm request ngay.
5. Dữ liệu gửi model được rút gọn, còn trace và bằng chứng incident vẫn nguyên vẹn.
6. Lỗi định dạng giả lập được trả cho model, sau đó công cụ nhận được lần gọi hợp lệ.

Các kiểm tra dùng snapshot, provider theo kịch bản và HTTP mock; không gọi LLM hoặc status API thật.

## Kết quả đánh giá bonus

Báo cáo `eval_bonus.json` dùng `deepseek / deepseek-flash` cho cả agent và judge, snapshot gốc, 16 lượt model tối đa mỗi case và một worker. Cả 10 lượt agent hoàn thành, 10 kết quả được chấm và không có vi phạm kiểm tra bằng code. Điểm bonus được tính bằng `round(score_0_100 * 0.15, 2)`; không tự cộng điểm eval bài chính vào điểm bonus để suy ra điểm lab.

| Case | Điểm | Bằng chứng chính trong trace |
| --- | ---: | --- |
| B1 | 100 | Đọc đủ năm status API trong một lượt tool calls. |
| B2 | 98 | Đọc timeline Vercel ở offset 0, 3, 6; đủ 7 update, `next_offset` cuối bằng null. |
| B3 | 100 | Vercel trả 429 giả lập một lần, lần gọi thứ hai thành công. |
| B4 | 100 | Dừng sau ba lần Vercel bị 429; vẫn báo được dữ liệu GitHub. |
| B5 | 95 | Nhận `INVALID_ARGUMENTS` giả lập rồi gọi lại thành công với offset/limit integer. |
| B6 | 100 | Giữ 160 mục nhiễu trong trace, chỉ gửi 12 mục cho model; đọc incident liên quan của hai bên. |
| B7 | 98 | Không xuất chuỗi bị cấm từ dữ liệu npm. |
| B8 | 100 | Đọc hai incident, đối chiếu scope ứng dụng đang chạy và build/deploy. |
| B9 | 100 | Nêu thiếu công cụ cho nhà cung cấp ngoài phạm vi, không tạo nguồn giả. |
| B10 | 98 | Kiểm tra năm status API; Vercel thành công sau một lần thử lại. |

Lần chạy có 28 lượt agent, 10 lượt judge và 30 lần thực thi công cụ. Token được ghi nhận: 74.456 cho agent và 34.878 cho judge, tổng 109.334. Provider không trả trường chi phí tiền trong báo cáo.

Trace B4 có `tool_retry_limit` ở lần Vercel thứ ba; model dừng thử lại nên không phát sinh yêu cầu thứ tư. Trace B6 ghi `context_trimmed` với tổng 160 mục, giữ 12 mục. Việc dừng theo `Retry-After` thật được kiểm tra bằng HTTP mock; các case bonus chỉ dùng 429 giả lập có thời gian chờ bằng 0.

### Giới hạn còn lại

- B5 mô tả lần đầu bị sai tham số, trong khi trace cho thấy offset/limit đã là integer; lỗi này được scenario giả lập chủ động trả về. Cần phân biệt thông báo lỗi của tool với nguyên nhân đã được kiểm chứng.
- B7 vẫn suy từ overall operational sang cách nói không có sự cố đang diễn ra; nên giới hạn nhận định theo phạm vi dữ liệu trạng thái đã đọc.
- B10 viết có ba incident chưa resolved nhưng danh sách trả về chỉ có hai: một `identified`, một `monitoring`, cùng sáu `resolved`. Đây là lỗi đếm và diễn đạt còn lại dù judge cho 98 điểm.
- Các gợi ý tăng điểm của judge có chỗ trùng với thông tin đã có trong câu trả lời, như tổng 7 update ở B2 và timestamp/nguồn ở B7. Điểm judge cần được đối chiếu lại với trace, không được xem là bằng chứng độ chính xác tuyệt đối.

Các kết quả được giữ nguyên, gồm các case dưới 100 điểm. Chưa kết luận bản mở rộng đạt 100 điểm lab.

Lệnh đã dùng để đánh giá bonus:

```powershell
python -X utf8 evals/run_evals.py --bonus --provider deepseek --model deepseek-flash --judge-provider deepseek --judge-model deepseek-flash --max-steps 16 --workers 1 --output eval_bonus.json
```

## Kiểm tra bài chính còn cần thực hiện

Bài chính cần đánh giá lại với cùng phiên bản code đã chạy bonus. Dùng file mới để giữ nguyên kết quả đã có và giữ cùng model, judge, snapshot, dataset cùng giới hạn 8 bước:

```powershell
python -X utf8 evals/run_evals.py --provider deepseek --model deepseek-flash --judge-provider deepseek --judge-model deepseek-flash --max-steps 8 --workers 1 --output eval_core_extended.json
```

Sau lượt này, cần đối chiếu trace và điểm từng case với kết quả bài chính trước đó, rồi cập nhật báo cáo theo số liệu thực tế. Bản bonus hiện tại tương ứng SHA-256 agent `d2f838c16e4e41efa15e6c891ae9d3d50348d221435b837ee5522653fa88edbf`; giữ nguyên code trong khi chạy bài chính để so sánh đúng phiên bản.
