from flask import Blueprint, request, jsonify
from bson import ObjectId
from pymongo import MongoClient

from config import MONGO_URI, MONGO_DB_NAME

mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB_NAME]
chunks_col = db["chunks"]

question_bp = Blueprint("question", __name__)

def load_texts_by_chunk_ids(chunk_ids):
    texts = []
    for cid in chunk_ids:
        try:
            oid = ObjectId(cid)
        except Exception:
            continue
        doc = chunks_col.find_one({"_id": oid})
        if doc:
            texts.append({"cid": cid, "text": doc.get("text", "")})
    return texts

