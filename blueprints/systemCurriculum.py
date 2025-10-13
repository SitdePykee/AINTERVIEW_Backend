from flask import Blueprint, request, jsonify
from pymongo import MongoClient
from config import MONGO_URI, MONGO_DB_NAME
import pandas as pd
import uuid
import datetime
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
            "created_at": datetime.datetime.utcnow()
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

@curriculum_bp.route('/get-curriculum', methods=['GET'])
def get_curriculum():
    try:
        curriculums = list(system_curriculum_col.find({}))

        for c in curriculums:
            # Chuyển ObjectId sang chuỗi
            if '_id' in c and isinstance(c['_id'], ObjectId):
                c['_id'] = str(c['_id'])
            # Chuyển datetime sang chuỗi
            if 'created_at' in c and hasattr(c['created_at'], 'isoformat'):
                c['created_at'] = c['created_at'].isoformat()

        return jsonify(curriculums), 200

    except Exception as e:
        return jsonify({"error": f"Cannot fetch data: {str(e)}"}), 500
