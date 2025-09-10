import json
import re
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from config import GEMINI_API_KEY, GEMINI_MODEL

genai.configure(api_key=GEMINI_API_KEY)

LLM = genai.GenerativeModel(
    GEMINI_MODEL,
    generation_config=GenerationConfig(response_mime_type="application/json")
)

def safe_parse_llm_output(raw: str) -> dict:
    """
    Loại bỏ code block và escape backslash chưa hợp lệ,
    sau đó parse JSON an toàn.
    """
    # 1. Loại bỏ code block ```json ... ```
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()

    # 2. Escape backslash đơn không hợp lệ
    # (?<!\\)\\(?![\\"]) -> tìm \ không có \ trước và không đứng trước \ hoặc "
    cleaned = re.sub(r'(?<!\\)\\(?![\\"])', r'\\\\', cleaned)

    # 3. Parse JSON
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Cannot parse LLM output as JSON: {e}\nRaw output: {raw}")

def call_llm_json(prompt: str) -> dict:
    """
    Gọi LLM và parse JSON an toàn, giữ LaTeX và ký hiệu tập hợp.
    """
    resp = LLM.generate_content(prompt)
    raw = (resp.text or "").strip()
    return safe_parse_llm_output(raw)
