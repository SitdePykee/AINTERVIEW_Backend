from flask import Blueprint, request, jsonify
from pymongo import MongoClient
from config import MONGO_URI, MONGO_DB_NAME
import uuid

auth_bp = Blueprint("auth", __name__)
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB_NAME]
users_col = db["users"]

# Mock session lưu token đơn giản (thực tế nên dùng Redis hoặc JWT)
SESSIONS = {}

@auth_bp.route("/signin", methods=["POST"])
def signin():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400

    user = users_col.find_one({"email": email, "password": password})
    if not user:
        return jsonify({"error": "Invalid credentials"}), 401

    # Tạo token giả lập
    token = str(uuid.uuid4())
    SESSIONS[token] = str(user["_id"])

    return jsonify({
        "message": "Signed in successfully",
        "token": token,
        "user_id": str(user["_id"])
    }), 200


@auth_bp.route("/signout", methods=["POST"])
def signout():
    data = request.get_json()
    token = data.get("token")

    if not token or token not in SESSIONS:
        return jsonify({"error": "Invalid token"}), 400

    SESSIONS.pop(token, None)

    return jsonify({"message": "Signed out successfully"}), 200


@auth_bp.route("/user/<user_id>", methods=["GET"])
def get_user_by_id(user_id):
    user = users_col.find_one({"_id": user_id}, {"password": 0})  # bỏ password ra
    if not user:
        return jsonify({"error": "User not found"}), 404

    user["_id"] = str(user["_id"])
    return jsonify(user), 200
