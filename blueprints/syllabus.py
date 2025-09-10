from flask import Blueprint, request, jsonify
from pymongo import MongoClient
from config import MONGO_URI, MONGO_DB_NAME
from pdfminer.high_level import extract_text
from utils.chunking import chunk_syllabus
import os, datetime
import uuid

syllabus_bp = Blueprint('syllabus', __name__)
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB_NAME]
chunks_col = db['chunks']
syllabus_col = db['syllabus']

@syllabus_bp.route('/upload-syllabus-pdf', methods=['POST'])
def upload_syllabus_pdf():
    file = request.files.get('file')
    user_id = request.form.get("user_id")  # lấy user_id từ form-data

    if not file:
        return jsonify({"error": "Require pdf file"}), 400
    if not user_id:
        return jsonify({"error": "Require user_id"}), 400

    filename = file.filename
    syllabus_name = filename.rsplit('.', 1)[0]

    # Lưu file tạm
    os.makedirs("uploads", exist_ok=True)
    save_path = os.path.join("uploads", filename)
    file.save(save_path)

    # Trích xuất text từ PDF
    try:
        text = extract_text(save_path)
    except Exception as e:
        return jsonify({"error": f"Cannot extract text from PDF: {str(e)}"}), 500

    # Chunk PDF
    chunked = chunk_syllabus(text, chunk_size=5000)

    # Tạo syllabus_id duy nhất
    syllabus_id = str(uuid.uuid4())

    # Insert vào bảng syllabus
    syllabus_doc = {
        "_id": syllabus_id,
        "user_id": user_id,
        "name": syllabus_name,
        "filename": filename,
        "num_chunks": len(chunked),
        "created_at": datetime.datetime.utcnow()
    }
    syllabus_col.insert_one(syllabus_doc)

    # Insert vào bảng chunks
    to_insert = [{
        "text": item["content"],
        "metadata": {
            "syllabus_id": syllabus_id,
            "chapter": item["chapter"],
            "start_offset": item["start_offset"],
            "end_offset": item["end_offset"]
        }
    } for item in chunked]

    if to_insert:
        chunks_col.insert_many(to_insert)

    return jsonify({
        "syllabus_id": syllabus_id,
        "syllabus_name": syllabus_name,
        "num_chunks": len(to_insert),
        "user_id": user_id
    }), 200
