from flask import Blueprint, request, jsonify
from pymongo import MongoClient
from config import MONGO_URI, MONGO_DB_NAME
import pandas as pd
import uuid
import io
from bson import ObjectId
import math
import requests

from utils.chunking import chunk_syllabus

curriculum_bp = Blueprint('curriculum', __name__)
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB_NAME]
system_curriculum_col = db['systemCurriculum']
book_embeddings_col = db['bookEmbeddings']
system_book_chunks_col = db['system_book_chunks']


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

@curriculum_bp.route('/save-book-embedding', methods=['POST'])
def save_book_embedding():
    try:
        data = request.get_json()
        book_id = data.get("bookId")

        if not book_id:
            return jsonify({"error": "Missing bookId"}), 400

        login_url = "https://qc.neureader.net/v2/auth/login"
        login_body = {
            "email": "11223735",
            "password": "000000"
        }

        login_response = requests.post(login_url, json=login_body)
        if login_response.status_code != 200:
            return jsonify({
                "error": f"Login failed: {login_response.status_code}",
                "details": login_response.text
            }), 500

        token_data = login_response.json()
        access_token = token_data.get("data", {}).get("accessToken")

        if not access_token:
            return jsonify({
                "error": "No accessToken found in login response",
                "login_response": token_data
            }), 500

        embedding_url = "https://qc.neureader.net/v2/readie/embedding"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        body = {
            "bookId": book_id,
            "pageNumber": 0,
            "pageSize": 10000000
        }

        embed_response = requests.post(embedding_url, headers=headers, json=body)
        if embed_response.status_code != 200:
            return jsonify({
                "error": f"Embedding API failed: {embed_response.status_code}",
                "details": embed_response.text
            }), embed_response.status_code

        json_data = embed_response.json()
        embeddings = json_data.get("data", {}).get("embeddings", [])

        if not embeddings:
            return jsonify({"error": "No embeddings returned from API"}), 404

        full_text = "\n\n".join([e.get("text", "") for e in embeddings]).strip()

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

@curriculum_bp.route('/get-book-text/<book_id>', methods=['GET'])
def get_book_text(book_id):
    try:
        doc = book_embeddings_col.find_one({"bookId": book_id})
        if not doc:
            return jsonify({"error": "Book not found"}), 404
        return jsonify({
            "bookId": book_id,
            "text": doc.get("text", "")
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@curriculum_bp.route('/chunk-book/<book_id>', methods=['POST'])
def chunk_book(book_id):
    try:
        doc = book_embeddings_col.find_one({"bookId": book_id})
        if not doc:
            return jsonify({"error": "Book not found"}), 404

        text = doc.get("text", "")
        if not text.strip():
            return jsonify({"error": "Empty text"}), 400

        chunks = chunk_syllabus(text)

        system_book_chunks_col.delete_many({"bookId": book_id})
        for c in chunks:
            system_book_chunks_col.insert_one({
                "_id": str(uuid.uuid4()),
                "bookId": book_id,
                "chapter": c.get("chapter"),
                "content": c.get("content"),
                "start_offset": c.get("start_offset"),
                "end_offset": c.get("end_offset")
            })

        return jsonify({
            "bookId": book_id,
            "chunk_count": len(chunks)
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
