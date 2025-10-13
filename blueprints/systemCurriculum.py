from flask import Blueprint, request, jsonify
from pymongo import MongoClient
from config import MONGO_URI, MONGO_DB_NAME
import pandas as pd
import uuid
import io

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

from bson import ObjectId
import math

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
