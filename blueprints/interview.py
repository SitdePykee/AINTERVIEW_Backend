import datetime
import json
import uuid
import time
from typing import List, Dict

from flask import Blueprint, request, jsonify
from bson import ObjectId
from extensions.llm import call_llm_json
from utils.summarize import summarize

# ==== MongoDB setup ====
from extensions.mongo import db
chunks_col = db["chunks"]
system_chunks_col = db["system_book_chunks"]
interviews_col = db["interviews"]
interview_session_col = db["interview_session"]
users_col = db["users"]

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

def load_texts_by_system_chunk_ids(chunk_ids):
    texts = []
    for id in chunk_ids:
        try:
            doc = system_chunks_col.find_one({"_id": id})
            if doc:
                texts.append({"cid": id, "text": doc.get("text", "")})
        except Exception:
            continue
    return texts


def to_iso(dt):
    if isinstance(dt, datetime.datetime):
        return dt.replace(tzinfo=datetime.timezone.utc).isoformat()
    return dt

def parse_iso_to_utc(dt_str: str):
    try:
        if dt_str.endswith("Z"):
            dt_str = dt_str[:-1] + "+00:00"

        dt = datetime.datetime.fromisoformat(dt_str)

        # ép về UTC
        if dt.tzinfo:
            dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return dt
    except Exception as e:
        print("parse_iso_to_utc error:", e, dt_str)
        return None


def select_chunks_randomly_by_syllabus(syllabus_id: str, k: int = 3) -> list[str]:
    """
    Lấy ngẫu nhiên k chunk khác nhau từ collection chunks theo syllabus_id.
    """
    all_chunks = list(chunks_col.find(
        {"metadata.syllabus_id": syllabus_id},
        {"_id": 1}
    ))

    if not all_chunks:
        return []

    random.seed(time.time_ns())
    sampled = random.sample(all_chunks, min(k, len(all_chunks)))
    return [str(c["_id"]) for c in sampled]


from typing import List

def select_chunks_randomly_by_system_syllabus(book_id: str, k: int = 3) -> list[str]:
    """
    Lấy ngẫu nhiên k chunk khác nhau từ collection system_book_chunks theo book_id.
    """
    all_chunks = list(system_chunks_col.find(
        {"bookId": book_id},
        {"_id": 1}
    ))

    if not all_chunks:
        return []
    random.seed(time.time_ns())
    sampled = random.sample(all_chunks, min(k, len(all_chunks)))
    return [c["_id"] for c in sampled]


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
Sinh ra 1 câu hỏi phỏng vấn mới dạng {type_str}, độ khó Bloom: {difficulty}
- Câu hỏi phải hoàn toàn dựa trên nội dung trong [Content] và không dùng kiến thức bên ngoài.
- Không tạo câu hỏi tổng quát hay kiến thức phổ biến nếu chunk không nhắc tới.
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
- Ngôn ngữ thân thiện, giống người phỏng vấn nói trực tiếp với người được phỏng vấn
- Người phỏng vấn không được đọc tài liệu mà AI được nhận, không sinh ra những câu hỏi dựa trên ví dụ cụ thể trong văn bản được nhận
- Không hỏi liên tục về một nội dung quá 3 câu
""".strip()

def prompt_generate_question_from_system_curriculum_with_session(
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

[Content]
\"\"\"{context_formatted}\"\"\"

Nhiệm vụ:
Sinh ra 1 câu hỏi phỏng vấn mới dạng {type_str}, độ khó Bloom: {difficulty}
- Câu hỏi phải hoàn toàn dựa trên nội dung trong [Content] và không dùng kiến thức bên ngoài.
- Không tạo câu hỏi tổng quát hay kiến thức phổ biến nếu chunk không nhắc tới.
Yêu cầu bổ sung (nếu có): {additional}

Trả về JSON object:
{{
  "question": "...",
  "question_type": "...",
  "answer": "...",
  "options": [...],  # chỉ nếu question_type = "multiple_choice"
  "source": {{
    "chunk_id": "None",
    "start": "None",  
    "end": "None"     
  }}
}}

Quy tắc:
- Không lặp lại ý/câu hỏi đã hỏi gần đây trừ khi follow-up có chủ đích.
- Nếu question_type != "multiple_choice" thì bỏ trường "options".
- Không sinh những câu hỏi "Theo tài liệu nhận được", "Dựa trên ví dụ" hoặc tương tự
- Chỉ trả JSON thuần, không thêm bất kì gì khác, đặc biệt là không markdown code block (```json ... ```), không sử dụng Latex.
- Câu hỏi phải hỏi người dùng về kiến thức / áp dụng / lý giải, có thể tạo các câu hỏi tính toán dựa trên lý thuyết nhận được.
- Ngôn ngữ thân thiện, giống người phỏng vấn nói trực tiếp với người được phỏng vấn
- Người phỏng vấn không được đọc tài liệu mà AI được nhận, không sinh ra những câu hỏi dựa trên ví dụ cụ thể trong văn bản được nhận
- Không hỏi liên tục về một nội dung quá 3 câu
""".strip()

# ==== Routes ====

@interview_bp.route("/create", methods=["POST"])
def create_interview():
    data = request.get_json(force=True)
    interview_id = str(uuid.uuid4())

    title = data.get("title")
    creator_id = data.get("creator_id")
    syllabus_id = data.get("syllabus_id")
    duration = data.get("duration_by_minutes")
    difficulty = data.get("difficulty")
    question_type = data.get("question_type")
    additional = data.get("additional")
    available_at = data.get("available_at")
    isSystemCurriculum = data.get("is_system_curriculum")
    if available_at:
        if isinstance(available_at, str):
            available_at = parse_iso_to_utc(available_at)
        elif isinstance(available_at, datetime.datetime):
            available_at = available_at

    # validate input
    missing_fields = []
    if creator_id is None:
        missing_fields.append("creator_id")
    if title is None:
        missing_fields.append("title")
    if syllabus_id is None:
        missing_fields.append("syllabus_id")
    if duration is None:
        missing_fields.append("duration_by_minutes")
    if difficulty is None:
        missing_fields.append("difficulty")
    if question_type is None:
        missing_fields.append("question_type")
    if additional is None:
        missing_fields.append("additional")
    if isSystemCurriculum is None:
        missing_fields.append("isSystemCurriculum")

    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400

    # insert interview vào bảng chính
    interview_doc = {
        "_id": interview_id,
        "title": title,
        "creator_id": creator_id,
        "participant_ids": [],
        "syllabus_id": syllabus_id,
        "duration": duration,
        "difficulty": difficulty,
        "questionType": question_type,
        "additional": additional,
        "status": "Unavailable",
        "available_at": available_at,
        "isSystemCurriculum": isSystemCurriculum,
        "created_at": now_utc()
    }
    interviews_col.insert_one(interview_doc)

    users_col.update_one(
        {"_id": creator_id},
        {
            "$setOnInsert": {"_id": creator_id},
            "$addToSet": {"interviews": interview_id}
        },
        upsert=True,
    )

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

    interviews_col.update_one(
        {"_id": interview_id},
        {"$addToSet": {"participant_ids": session_id}}
    )


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
    }), 200


import random

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

    # --------------------
    # Chọn ngẫu nhiên 1 loại question_type
    if isinstance(question_type, list) and question_type:
        types = [random.choice(question_type)]
    elif isinstance(question_type, str):
        types = [question_type]
    else:
        types = []

    # --------------------

    syllabus_id = db_interview.get("syllabus_id")
    selected_chunk_ids = select_chunks_randomly_by_syllabus(syllabus_id, 3)
    if not selected_chunk_ids:
        return jsonify({"error": "No valid chunks found"}), 404

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
        types=types,
        additional=additional,
    )

    try:
        obj = call_llm_json(prompt)
    except Exception as e:
        if "429" in str(e) or "RATE_LIMIT_EXCEEDED" in str(e):
            return jsonify({"error": "Rate limit exceeded. Please try again later."}), 429
        else:
            return jsonify({"error": "LLM error", "detail": str(e)}), 500

    return jsonify(obj), 200

@interview_bp.route("/next_question_system", methods=["POST"])
def next_question_system():
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

    # --------------------
    # Chọn ngẫu nhiên 1 loại question_type
    if isinstance(question_type, list) and question_type:
        types = [random.choice(question_type)]
    elif isinstance(question_type, str):
        types = [question_type]
    else:
        types = []

    # --------------------

    syllabus_id = db_interview.get("syllabus_id")
    selected_chunk_ids = select_chunks_randomly_by_system_syllabus(syllabus_id, 3)
    if not selected_chunk_ids:
        return jsonify({"error": "No valid chunks found"}), 404

    texts = load_texts_by_system_chunk_ids(selected_chunk_ids)
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
        types=types,
        additional=additional,
    )

    try:
        obj = call_llm_json(prompt)
    except Exception as e:
        if "429" in str(e) or "RATE_LIMIT_EXCEEDED" in str(e):
            return jsonify({"error": "Rate limit exceeded. Please try again later."}), 429
        else:
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

@interview_bp.route("/user_interviews/<user_id>", methods=["GET"])
def get_user_interviews(user_id):
    try:
        # lấy user
        user = users_col.find_one({"_id": user_id})
        if not user:
            return jsonify({"error": "User not found"}), 404

        # lấy danh sách id interview
        interview_ids = user.get("interviews", [])
        if not interview_ids:
            return jsonify([]), 200

        # query trực tiếp từ bảng interviews
        interviews = list(interviews_col.find({"_id": {"$in": interview_ids}}))

        now = now_utc()
        updated_ids = []

        for iv in interviews:
            available_at = iv.get("available_at")
            status = iv.get("status", "Unavailable")

            # parse datetime
            if isinstance(available_at, str):
                available_at_dt = parse_iso_to_utc(available_at)
            elif isinstance(available_at, datetime.datetime):
                available_at_dt = available_at
            else:
                available_at_dt = None

            # update status nếu tới giờ
            if available_at_dt and now >= available_at_dt and status == "Unavailable":
                interviews_col.update_one(
                    {"_id": iv["_id"]},
                    {"$set": {"status": "Available"}}
                )
                iv["status"] = "Available"
                updated_ids.append(iv["_id"])

            iv["available_at"] = to_iso(available_at_dt)
            iv["created_at"] = to_iso(iv.get("created_at"))

        return jsonify(interviews), 200

    except Exception as e:
        return jsonify({"error": "Server error", "detail": str(e)}), 500

@interview_bp.route("/all_interview", methods=["GET"])
def get_all_interviews():
    try:
        now = now_utc()
        interviews = list(interviews_col.find({}))
        updated_ids = []

        for iv in interviews:
            available_at = iv.get("available_at")
            status = iv.get("status", "Unavailable")

            # parse datetime
            if isinstance(available_at, str):
                available_at_dt = parse_iso_to_utc(available_at)
            elif isinstance(available_at, datetime.datetime):
                available_at_dt = available_at
            else:
                available_at_dt = None

            # cập nhật trạng thái nếu đến giờ
            if available_at_dt and now >= available_at_dt and status == "Unavailable":
                interviews_col.update_one(
                    {"_id": iv["_id"]},
                    {"$set": {"status": "Available"}}
                )
                iv["status"] = "Available"
                updated_ids.append(iv["_id"])

            iv["available_at"] = to_iso(available_at_dt)
            iv["created_at"] = to_iso(iv.get("created_at"))

        return jsonify(interviews), 200

    except Exception as e:
        return jsonify({"error": "Server error", "detail": str(e)}), 500

@interview_bp.route("/get_session/<session_id>", methods=["GET"])
def get_session(session_id):
    """
    Lấy thông tin chi tiết của 1 phiên phỏng vấn (session)
    """
    try:
        # tìm trong DB
        session = interview_session_col.find_one({"_id": session_id})
        if not session:
            return jsonify({"error": "Session not found"}), 404

        # format datetime nếu có
        if "start_time" in session and isinstance(session["start_time"], datetime.datetime):
            session["start_time"] = to_iso(session["start_time"])
        if "end_time" in session and isinstance(session["end_time"], datetime.datetime):
            session["end_time"] = to_iso(session["end_time"])

        # convert ObjectId (nếu có trường nào chứa)
        session["_id"] = str(session["_id"])
        session["interview_id"] = str(session["interview_id"])

        return jsonify(session), 200

    except Exception as e:
        return jsonify({"error": "Server error", "detail": str(e)}), 500

@interview_bp.route("/sessions_by_interview/<interview_id>", methods=["GET"])
def get_sessions_by_interview(interview_id):
    """
    Lấy tất cả các session thuộc về 1 interview cụ thể
    """
    try:
        sessions = list(interview_session_col.find({"interview_id": interview_id}))
        if not sessions:
            return jsonify([]), 200

        for s in sessions:
            s["_id"] = str(s["_id"])
            s["interview_id"] = str(s["interview_id"])
            if "start_time" in s and isinstance(s["start_time"], datetime.datetime):
                s["start_time"] = to_iso(s["start_time"])
            if "end_time" in s and isinstance(s["end_time"], datetime.datetime):
                s["end_time"] = to_iso(s["end_time"])

        return jsonify(sessions), 200

    except Exception as e:
        return jsonify({"error": "Server error", "detail": str(e)}), 500
