from flask import Blueprint, request, jsonify
from pymongo import MongoClient
from config import MONGO_URI, MONGO_DB_NAME
import pandas as pd
import uuid
import io
from bson import ObjectId
import math

curriculum_bp = Blueprint('curriculum', __name__)
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB_NAME]
system_curriculum_col = db['systemCurriculum']


@curriculum_bp.route('/upload-curriculum-excel', methods=['POST'])
def upload_curriculum_excel():
    file = request.files.get('file')
    user_id = request.form.get('user_id')

    if not file:
        return jsonify({"error": "Require Excel file"}), 400
    if not user_id:
        return jsonify({"error": "Require user_id"}), 400

    # Đọc Excel từ bộ nhớ (không lưu ra ổ đĩa)
    try:
        file_bytes = io.BytesIO(file.read())

        df = pd.read_excel(file_bytes, engine='openpyxl')

    except Exception as e:
        return jsonify({"error": f"Cannot read Excel file: {str(e)}"}), 500

    # Duyệt từng dòng và chuyển thành document Mongo
    documents = []
    for _, row in df.iterrows():
        doc = {
            "_id": str(uuid.uuid4()),
            "system_id": str(row.get("_doc_id", "")),
            "id": str(row.get("id", "")),
            "uuid": str(row.get("uuid", "")),
            "title": row.get("title", ""),
            "author": row.get("author", ""),
            "publisher": row.get("publisher", ""),
            "publish_year": row.get("publish-year", ""),
            "category": row.get("category", ""),
            "type": row.get("type", ""),
            "major": row.get("major", ""),
            "faculty": row.get("faculty", ""),
            "subject": row.get("subject", ""),
            "status": row.get("status", ""),
            "readie": row.get("readie", ""),
            "price": row.get("price", ""),
            "pages": row.get("pages", ""),
            "file_size": row.get("file-size", ""),
            "isbn": row.get("isbn", ""),
            "upload_date": row.get("upload-date", ""),
            "description": row.get("description", ""),
            "uploaded_by": user_id,
        }
        documents.append(doc)

    if not documents:
        return jsonify({"error": "No valid rows found"}), 400

    system_curriculum_col.insert_many(documents)

    return jsonify({
        "inserted_count": len(documents),
        "user_id": user_id
    }), 200

@curriculum_bp.route('/get-curriculum', methods=['GET'])
def get_curriculum():
    try:
        curriculums = list(system_curriculum_col.find({}))

        clean_curriculums = []
        for c in curriculums:
            clean_doc = {}
            for k, v in c.items():
                # Chuyển ObjectId -> str
                if isinstance(v, ObjectId):
                    clean_doc[k] = str(v)
                # Chuyển NaN -> None
                elif isinstance(v, float) and math.isnan(v):
                    clean_doc[k] = None
                else:
                    clean_doc[k] = v
            clean_curriculums.append(clean_doc)

        return jsonify(clean_curriculums), 200

    except Exception as e:
        return jsonify({"error": f"Cannot fetch data: {str(e)}"}), 500

import requests

book_embeddings_col = db['bookEmbeddings']

@curriculum_bp.route('/save-book-embedding', methods=['POST'])
def save_book_embedding():
    try:
        data = request.get_json()
        book_id = data.get("bookId")

        if not book_id:
            return jsonify({"error": "Missing bookId"}), 400

        # Gọi API embedding
        url = "https://qc.neureader.net/v2/readie/embedding"
        headers = {
            "Authorization": "Bearer 9xjN6qbrA4givxIMf6OydzUZiFRXy06leV5pLFAGApWsHNoQ",
            "Content-Type": "application/json"
        }
        body = {
            "bookId": book_id,
            "pageNumber": 0,
            "pageSize": 10000000
        }

        response = requests.post(url, headers=headers, json=body)
        if response.status_code != 200:
            return jsonify({"error": f"Embedding API failed: {response.status_code}"}), 500

        json_data = response.json()
        embeddings = json_data.get("data", {}).get("embeddings", [])

        if not embeddings:
            return jsonify({"error": "No embeddings returned from API"}), 404

        # Gộp tất cả text từ các embeddings
        full_text = "\n\n".join([e.get("text", "") for e in embeddings]).strip()

        # Lưu vào MongoDB (nếu bookId đã tồn tại thì cập nhật)
        existing = book_embeddings_col.find_one({"bookId": book_id})
        if existing:
            book_embeddings_col.update_one(
                {"bookId": book_id},
                {"$set": {"text": full_text}}
            )
            action = "updated"
        else:
            book_embeddings_col.insert_one({
                "_id": str(uuid.uuid4()),
                "bookId": book_id,
                "text": full_text
            })
            action = "inserted"

        return jsonify({
            "message": f"Book embedding {action} successfully",
            "bookId": book_id,
            "text_length": len(full_text)
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
