import datetime
import json
import uuid
from typing import List, Dict
import random

from flask import Blueprint, request, jsonify
from pymongo import MongoClient

from config import MONGO_URI, MONGO_DB_NAME
from extensions.llm import call_llm_json
from utils.summarize import summarize

# ==== MongoDB setup ====
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB_NAME]
revisions_col = db["revisions"]
revision_session_col = db["revision_session"]
users_col = db["users"]

# ==== Blueprint ====
revision_bp = Blueprint("revision", __name__)

# ==== In-memory cache ====
REVISION_CACHE: Dict[str, dict] = {}

# ==== Utils ====
def now_utc():
    return datetime.datetime.utcnow()

def to_iso(dt):
    if isinstance(dt, datetime.datetime):
        return dt.replace(tzinfo=datetime.timezone.utc).isoformat()
    return dt

def prompt_generate_general_knowledge_question(
    summary: str,
    subject: str,
    recent_qa: List[Dict],
    difficulty: str,
    types: List[str],
    additional: str
) -> str:
    type_str = " hoặc ".join(types)
    recent_qa_str = json.dumps(recent_qa, ensure_ascii=False, indent=2)

    return f"""
Bạn là giảng viên đang luyện tập kiến thức tổng quát môn {subject} với học sinh.

[Session Summary - tóm tắt tiến trình tới hiện tại]
{summary}

[Recent Q&A - vài lượt gần nhất]
{recent_qa_str}

Nhiệm vụ:
Sinh ra 1 câu hỏi mới dạng {type_str}, độ khó Bloom: {difficulty}, phù hợp với diễn tiến trong summary + recent Q&A.
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
  "reason" : "..."  # Lí do cho câu trả lời trên
}}

Quy tắc:
- Không lặp lại ý/câu hỏi đã hỏi gần đây trừ khi follow-up có chủ đích.
- Nếu question_type != "multiple_choice" thì bỏ trường "options".
- Chỉ trả JSON thuần, không thêm markdown hay Latex.
- Câu hỏi dựa trên kiến thức tổng quát, không dựa trên văn bản hay tài liệu cụ thể.
- Ngôn ngữ thân thiện, giống người phỏng vấn nói trực tiếp với học sinh.
""".strip()


# ==== Routes ====

@revision_bp.route("/create", methods=["POST"])
def create_revision():
    data = request.get_json(force=True)
    revision_id = str(uuid.uuid4())

    title = data.get("title")
    creator_id = data.get("creator_id")
    duration = data.get("duration_by_minutes")
    difficulty = data.get("difficulty")
    question_type = data.get("question_type")
    additional = data.get("additional")
    available_at = data.get("available_at")
    if available_at:
        if isinstance(available_at, str):
            available_at = datetime.datetime.fromisoformat(available_at)
        elif isinstance(available_at, datetime.datetime):
            available_at = available_at

    missing_fields = []
    for field in ["creator_id", "title", "duration_by_minutes", "difficulty", "question_type", "additional"]:
        if data.get(field) is None:
            missing_fields.append(field)

    if missing_fields:
        return jsonify({"error": f"Missing fields: {', '.join(missing_fields)}"}), 400

    revision_doc = {
        "_id": revision_id,
        "title": title,
        "creator_id": creator_id,
        "participant_ids": [],
        "duration": duration,
        "difficulty": difficulty,
        "questionType": question_type,
        "additional": additional,
        "status": "Unavailable",
        "available_at": available_at,
        "created_at": now_utc()
    }
    revisions_col.insert_one(revision_doc)

    users_col.update_one(
        {"_id": creator_id},
        {
            "$setOnInsert": {"_id": creator_id},
            "$addToSet": {"revisions": revision_id}
        },
        upsert=True,
    )

    return jsonify({"revision_id": revision_id}), 200


@revision_bp.route("/start", methods=["POST"])
def start_revision():
    data = request.get_json(force=True)
    revision_id = data.get("revision_id")
    participant_id = data.get("participant_id")

    session_id = str(uuid.uuid4())

    revision_session_col.insert_one({
        "_id": session_id,
        "revision_id": revision_id,
        "participant_id": participant_id,
        "questions": [],
        "answers": [],
        "feedback": "",
        "point": 0.0,
        "start_time": now_utc(),
        "end_time": None,
    })

    REVISION_CACHE[session_id] = {
        "id": session_id,
        "revision_id": revision_id,
        "participant_id": participant_id,
        "questions": [],
        "answers": [],
        "qa_log": [],
        "summary": "",
    }

    return jsonify({"session_id": session_id}), 200


@revision_bp.route("/next_question", methods=["POST"])
def next_revision_question():
    data = request.get_json(force=True)
    session_id = data.get("session_id")
    subject = data.get("subject")

    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    revision = REVISION_CACHE.get(session_id)
    if not revision:
        return jsonify({"error": "Revision not started or not in cache"}), 404

    revision_id = revision.get("revision_id")
    db_revision = revisions_col.find_one({"_id": revision_id})
    if not db_revision:
        return jsonify({"error": "Revision not found in DB"}), 404

    difficulty = db_revision.get("difficulty")
    question_type = db_revision.get("questionType")
    additional = db_revision.get("additional", "")

    # chọn 1 type ngẫu nhiên
    if isinstance(question_type, list) and question_type:
        types = [random.choice(question_type)]
    elif isinstance(question_type, str):
        types = [question_type]
    else:
        types = []

    summary = revision.get("summary", "")
    recent_qa = revision.get("qa_log", [])[-4:]

    prompt = prompt_generate_general_knowledge_question(
        summary=summary,
        subject=subject,
        recent_qa=recent_qa,
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


@revision_bp.route("/answer", methods=["POST"])
def answer_revision():
    data = request.get_json(force=True)
    session_id = data.get("session_id")
    q = data.get("question")
    a = data.get("answer")

    if not session_id or not q:
        return jsonify({"error": "session_id and question are required"}), 400

    revision = REVISION_CACHE.get(session_id)
    if not revision:
        return jsonify({"error": "Revision not found"}), 404

    qa_item = {"question": q, "answer": a}
    revision["qa_log"].append(qa_item)
    revision["questions"].append(q)
    revision["answers"].append(a)

    summary_updated = False
    qa_log = revision.get("qa_log", [])
    summary = revision.get("summary", "")

    if len(qa_log) > 6:
        new_pairs = qa_log[-6:]
        try:
            new_summary = summarize(summary, new_pairs)
            revision["summary"] = new_summary
            revision["qa_log"] = qa_log[-4:]
            summary_updated = True
        except Exception as e:
            print("Summarize error:", e)

    updated = {"status": "saved"}
    if summary_updated:
        updated["summary_updated"] = True

    return jsonify(updated), 200


@revision_bp.route("/end", methods=["POST"])
def end_revision():
    data = request.get_json(force=True)
    session_id = data.get("session_id")

    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    revision = REVISION_CACHE.get(session_id)
    if not revision:
        return jsonify({"error": "Revision not found"}), 404

    revision_session_col.update_one(
        {"_id": session_id},
        {"$set": {
            "questions": revision.get("questions", []),
            "answers": revision.get("answers", []),
            "point": revision.get("point", 0.0),
            "feedback": revision.get("feedback", ""),
            "end_time": now_utc(),
        }}
    )

    REVISION_CACHE.pop(session_id, None)

    return jsonify({
        "status": "finished",
        "end_time": now_utc().isoformat() + "Z",
    }), 200


@revision_bp.route("/user_revisions/<user_id>", methods=["GET"])
def get_user_revisions(user_id):
    try:
        user = users_col.find_one({"_id": user_id})
        if not user:
            return jsonify({"error": "User not found"}), 404

        revision_ids = user.get("revisions", [])
        if not revision_ids:
            return jsonify([]), 200

        revisions = list(revisions_col.find({"_id": {"$in": revision_ids}}))

        now = now_utc()
        for rv in revisions:
            available_at = rv.get("available_at")
            status = rv.get("status", "Unavailable")
            if available_at and isinstance(available_at, datetime.datetime):
                if now >= available_at and status == "Unavailable":
                    revisions_col.update_one({"_id": rv["_id"]}, {"$set": {"status": "Available"}})
                    rv["status"] = "Available"
            rv["available_at"] = to_iso(available_at)
            rv["created_at"] = to_iso(rv.get("created_at"))

        return jsonify(revisions), 200

    except Exception as e:
        return jsonify({"error": "Server error", "detail": str(e)}), 500


@revision_bp.route("/all_revision", methods=["GET"])
def get_all_revisions():
    try:
        revisions = list(revisions_col.find({}))
        now = now_utc()
        for rv in revisions:
            available_at = rv.get("available_at")
            status = rv.get("status", "Unavailable")
            if available_at and isinstance(available_at, datetime.datetime):
                if now >= available_at and status == "Unavailable":
                    revisions_col.update_one({"_id": rv["_id"]}, {"$set": {"status": "Available"}})
                    rv["status"] = "Available"
            rv["available_at"] = to_iso(available_at)
            rv["created_at"] = to_iso(rv.get("created_at"))

        return jsonify(revisions), 200
    except Exception as e:
        return jsonify({"error": "Server error", "detail": str(e)}), 500
