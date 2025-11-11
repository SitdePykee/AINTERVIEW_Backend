from flask import Blueprint, request, jsonify, send_file
from pdfminer.high_level import extract_text
from utils.chunking import chunk_syllabus
import os, datetime
import uuid
from extensions.mongo import db
syllabus_bp = Blueprint('syllabus', __name__)
chunks_col = db['chunks']
syllabus_col = db['syllabus']
users_col = db['users']


@syllabus_bp.route('/upload-syllabus-pdf', methods=['POST'])
def upload_syllabus_pdf():
    file = request.files.get('file')
    user_id = request.form.get("user_id")
    name = request.form.get("name")

    if not file:
        return jsonify({"error": "Require pdf file"}), 400
    if not user_id:
        return jsonify({"error": "Require user_id"}), 400

    original_filename = file.filename
    syllabus_name = original_filename.rsplit('.', 1)[0]

    # Tạo syllabus_id duy nhất
    syllabus_id = str(uuid.uuid4())

    # Lưu file vật lý bằng syllabus_id.pdf
    os.makedirs("uploads", exist_ok=True)
    stored_filename = f"{syllabus_id}.pdf"
    save_path = os.path.join("uploads", stored_filename)
    file.save(save_path)

    # Trích xuất text từ PDF
    try:
        text = extract_text(save_path)
    except Exception as e:
        return jsonify({"error": f"Cannot extract text from PDF: {str(e)}"}), 500

    # Chunk PDF
    chunked = chunk_syllabus(text, chunk_size=5000)

    # Insert vào bảng syllabus
    syllabus_doc = {
        "_id": syllabus_id,
        "user_id": user_id,
        "name": name,
        "original_filename": original_filename,
        "stored_filename": stored_filename,
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

    # Cập nhật syllabus vào documents của user
    users_col.update_one(
        {"_id": user_id},
        {
            "$setOnInsert": {"_id": user_id},
            "$push": {
                "documents": {
                    "syllabus_id": syllabus_id,
                    "name": name,
                    "original_filename": original_filename,
                    "uploaded_at": datetime.datetime.utcnow()
                }
            }
        },
        upsert=True
    )

    return jsonify({
        "syllabus_id": syllabus_id,
        "syllabus_name": syllabus_name,
        "num_chunks": len(to_insert),
        "user_id": user_id
    }), 200


@syllabus_bp.route('/get-user-documents/<user_id>', methods=['GET'])
def get_user_documents(user_id):
    user = users_col.find_one({"_id": user_id}, {"documents": 1, "_id": 0})

    if not user:
        return jsonify({"error": "User not found"}), 404

    documents = user.get("documents", [])
    return jsonify(documents), 200


@syllabus_bp.route('/download-syllabus-pdf/<syllabus_id>', methods=['GET'])
def download_syllabus_pdf(syllabus_id):
    doc = syllabus_col.find_one({"_id": syllabus_id})
    if not doc:
        return jsonify({"error": "Syllabus not found"}), 404

    stored_filename = doc.get("stored_filename")
    original_filename = doc.get("original_filename", "file.pdf")
    file_path = os.path.join("uploads", stored_filename)

    if not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404

    return send_file(
        file_path,
        as_attachment=True,
        download_name=original_filename,
        mimetype="application/pdf"
    )
