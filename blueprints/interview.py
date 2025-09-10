import datetime
import json
import uuid
from typing import List, Dict

from flask import Blueprint, request, jsonify
from bson import ObjectId
from pymongo import MongoClient

from config import MONGO_URI, MONGO_DB_NAME
from extensions.llm import call_llm_json
from utils.summarize import summarize

# ==== MongoDB setup ====
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB_NAME]
chunks_col = db["chunks"]
interviews_col = db["interviews"]
interview_session_col = db["interview_session"]

# ==== Blueprint ====
interview_bp = Blueprint("interview", __name__)

# ==== In-memory cache ====
INTERVIEW_CACHE: Dict[str, dict] = {}

# ==== Utils ====
def now_utc():
    return datetime.datetime.utcnow()

def load_texts_by_chunk_ids(chunk_ids):
    texts = []
    for cid in chunk_ids:
        try:
            oid = ObjectId(cid)
            doc = chunks_col.find_one({"_id": oid})
            if doc:
                texts.append({"cid": cid, "text": doc.get("text", "")})
        except Exception:
            continue
    return texts

def select_chunks_round_robin(interview: dict, k: int = 3) -> List[str]:
    """Chọn vòng tròn trong RAM, không lưu DB"""
    chunk_ids = interview.get("chunk_ids", [])
    if not chunk_ids:
        return []

    cursor = interview.get("cursor", 0)
    n = len(chunk_ids)
    selected = [chunk_ids[(cursor + i) % n] for i in range(k)]
    new_cursor = (cursor + k) % n
    interview["cursor"] = new_cursor
    return selected

def prompt_generate_question_with_session(
    summary: str,
    recent_qa: List[Dict],
    context_formatted: str,
    difficulty: str,
    types: List[str],
    additional: str
) -> str:
    type_str = " hoặc ".join(types)
    recent_qa_str = json.dumps(recent_qa, ensure_ascii=False, indent=2)

    return f"""
Bạn là giảng viên đang phỏng vấn sinh viên để kiểm tra. Hãy đọc thông tin buổi phỏng vấn sau:

[Session Summary - tóm tắt tiến trình tới hiện tại]
{summary}

[Recent Q&A - vài lượt gần nhất]
{recent_qa_str}

[Context chunks (kèm chunk_id)]
\"\"\"{context_formatted}\"\"\"

Nhiệm vụ:
Sinh ra 1 câu hỏi phỏng vấn mới (dạng {type_str}), độ khó Bloom: {difficulty}, phù hợp với diễn tiến trong summary + recent Q&A.
Yêu cầu bổ sung (nếu có): {additional}

Trả về JSON object:
{{
  "question": "...",
  "question_type": "...",
  "answer": "...",
  "options": [...],  # chỉ nếu question_type = "multiple_choice"
  "source": {{
    "chunk_id": "...",
    "start": "...",  # offset của kí tự đầu tiên dùng làm nguồn câu hỏi trong chunk
    "end": "..."     # offset của kí tự cuối cùng dùng làm nguồn câu hỏi trong chunk
  }}
}}

Quy tắc:
- Không lặp lại ý/câu hỏi đã hỏi gần đây trừ khi follow-up có chủ đích.
- Nếu question_type != "multiple_choice" thì bỏ trường "options".
- Không sinh những câu hỏi "Theo tài liệu nhận được", "Dựa trên ví dụ" hoặc tương tự
- Chỉ trả JSON thuần, không thêm bất kì gì khác, đặc biệt là không markdown code block (```json ... ```), không sử dụng Latex.
- Câu hỏi phải hỏi người dùng về kiến thức / áp dụng / lý giải, có thể tạo các câu hỏi tính toán dựa trên lý thuyết nhận được.
- Không copy toàn bộ ví dụ, dữ liệu, hay lời giải có sẵn trong chunk.
- Ngôn ngữ thân thiện, giống người phỏng vấn nói trực tiếp với người được phỏng vấn
- Người phỏng vấn không được đọc tài liệu mà AI được nhận, không sinh ra những câu hỏi dựa trên ví dụ cụ thể trong văn bản được nhận
""".strip()

# ==== Routes ====

@interview_bp.route("/create", methods=["POST"])
def create_interview():
    data = request.get_json(force=True)
    interview_id = str(uuid.uuid4())
    creator_id = data.get("creator_id")
    syllabus_id = data.get("syllabus_id")
    duration = data.get("duration_by_minutes")
    difficulty = data.get("difficulty")
    question_type = data.get("question_type")
    additional = data.get("additional")

    interviews_col.insert_one({
        "_id": interview_id,
        "creator_id": creator_id,
        "participant_ids": [],
        "syllabus_id": syllabus_id,
        "duration": duration,
        "difficulty": difficulty,
        "questionType": question_type,
        "additional": additional,
        "status": "Unavailable"
    })

    return jsonify({"interview_id": interview_id}), 200


@interview_bp.route("/start", methods=["POST"])
def start_interview():
    data = request.get_json(force=True)
    interview_id = data.get("interview_id")
    participant_id = data.get("participant_id")

    session_id = str(uuid.uuid4())

    interview_session_col.insert_one({
        "_id": session_id,
        "interview_id": interview_id,
        "participant_id": participant_id,
        "questions": [],
        "answers": [],
        "feedback": "",
        "point": 0.0,
        "start_time": now_utc(),
        "end_time": None,
    })

    INTERVIEW_CACHE[session_id] = {
        "id": session_id,
        "interview_id": interview_id,
        "participant_id": participant_id,
        "questions": [],
        "answers": [],
        "qa_log": [],
        "summary": "",
        "cursor": 0,
        "chunk_ids": []
    }

    return jsonify({
        "session_id": session_id,
        "interview_id": interview_id,
        "participant_id": participant_id,
    }), 200


@interview_bp.route("/next_question", methods=["POST"])
def next_question():
    data = request.get_json(force=True)
    session_id = data.get("session_id")

    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    interview = INTERVIEW_CACHE.get(session_id)
    if not interview:
        return jsonify({"error": "Interview not started or not in cache"}), 404

    interview_id = interview.get("interview_id")

    db_interview = interviews_col.find_one({"_id": interview_id})
    if not db_interview:
        return jsonify({"error": "Interview not found in DB"}), 404

    difficulty = db_interview.get("difficulty")
    question_type = db_interview.get("questionType")
    additional = db_interview.get("additional", "")

    selected_chunk_ids = select_chunks_round_robin(interview, k=3)
    texts = load_texts_by_chunk_ids(selected_chunk_ids)
    if not texts:
        return jsonify({"error": "No valid chunks found"}), 404

    context_formatted = "\n\n".join([f"[{t['cid']}]: {t['text']}" for t in texts])
    summary = interview.get("summary", "")
    recent_qa = interview.get("qa_log", [])[-4:]

    prompt = prompt_generate_question_with_session(
        summary=summary,
        recent_qa=recent_qa,
        context_formatted=context_formatted,
        difficulty=difficulty,
        types=[question_type] if question_type else [],
        additional=additional,
    )

    try:
        obj = call_llm_json(prompt)
    except Exception as e:
        return jsonify({"error": "LLM error", "detail": str(e)}), 500

    return jsonify(obj), 200


@interview_bp.route("/answer", methods=["POST"])
def answer():
    data = request.get_json(force=True)
    session_id = data.get("session_id")
    q = data.get("question")
    a = data.get("answer")

    if not session_id or not q:
        return jsonify({"error": "session_id and question are required"}), 400

    interview = INTERVIEW_CACHE.get(session_id)
    if not interview:
        return jsonify({"error": "Interview not found"}), 404

    qa_item = {
        "question": q,
        "answer": a,
    }

    interview["qa_log"].append(qa_item)
    interview["questions"].append(q)
    interview["answers"].append(a)


    qa_log = interview.get("qa_log", [])
    summary = interview.get("summary", "")
    summary_updated = False

    if len(qa_log) > 6:
        new_pairs = qa_log[-6:]
        try:
            new_summary = summarize(summary, new_pairs)
            keep_tail = qa_log[-4:]
            interview["summary"] = new_summary
            interview["qa_log"] = keep_tail
            summary_updated = True
        except Exception as e:
            print("Summarize error:", e)

    updated = {"status": "saved"}
    if summary_updated:
        updated["summary_updated"] = True

    return jsonify(updated), 200

@interview_bp.route("/end", methods=["POST"])
def end():
    data = request.get_json(force=True)
    session_id = data.get("session_id")

    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    interview = INTERVIEW_CACHE.get(session_id)
    if not interview:
        return jsonify({"error": "Interview not found"}), 404

    interview_session_col.update_one(
        {"_id": session_id},
        {"$set": {
            "questions": interview.get("questions", []),
            "answers": interview.get("answers", []),
            "point": interview.get("point", 0.0),
            "feedback": interview.get("feedback", ""),
            "end_time": now_utc(),
        }}
    )

    INTERVIEW_CACHE.pop(session_id, None)

    result = {
        "status": "finished",
        "end_time": now_utc().isoformat() + "Z",
    }
    return jsonify(result), 200
