import json
from extensions.llm import call_llm_json

def prompt_summarize_history(old_summary: str, new_pairs: list) -> str:
    new_pairs_str = json.dumps(new_pairs, ensure_ascii=False, indent=2)
    return f"""
Bạn là trợ lý tóm tắt phỏng vấn. Hãy cập nhật bản tóm tắt buổi phỏng vấn dựa trên
summary hiện tại và các cặp Q&A mới.

[Current Summary]
{old_summary}

[New Q&A Pairs]
{new_pairs_str}

Yêu cầu:
- Tóm gọn (<= 200 từ), có trọng tâm, nêu rõ điểm mạnh/yếu, chủ đề đã phủ, độ khó đã đi qua,
  và gợi ý hướng hỏi tiếp theo (nếu có).
- Chỉ trả JSON object: {{ "summary": "..." }}
""".strip()

def summarize(old_summary: str, new_pairs: list) -> str:
    prompt = prompt_summarize_history(old_summary, new_pairs)
    obj = call_llm_json(prompt)
    return obj.get("summary", old_summary or "")
