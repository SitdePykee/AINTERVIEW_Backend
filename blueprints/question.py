from flask import Blueprint, request, jsonify
from bson import ObjectId

from extensions.mongo import db
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

