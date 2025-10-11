from flask import Blueprint, request, jsonify
from pymongo import MongoClient
from config import MONGO_URI, MONGO_DB_NAME
import uuid
import bcrypt

auth_bp = Blueprint("auth", __name__)
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB_NAME]
users_col = db["users"]

# Mock session lưu token đơn giản (thực tế nên dùng Redis hoặc JWT)
SESSIONS = {}


# -------------------- Đăng ký --------------------
@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")
    role = data.get("role")

    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400

    existing_user = users_col.find_one({"email": email})
    if existing_user:
        return jsonify({"error": "Email already registered"}), 409

    hashed_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())

    new_user = {
        "_id": str(uuid.uuid4()),
        "email": email,
        "password": hashed_pw.decode("utf-8"),
        "role": role or "user",
    }

    users_col.insert_one(new_user)

    return jsonify({
        "message": "User registered successfully",
        "user_id": new_user["_id"]
    }), 201


# -------------------- Đăng nhập --------------------
@auth_bp.route("/signin", methods=["POST"])
def signin():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400

    user = users_col.find_one({"email": email})
    if not user:
        return jsonify({"error": "Invalid email or password"}), 401

    # So sánh mật khẩu bcrypt
    if not bcrypt.checkpw(password.encode("utf-8"), user["password"].encode("utf-8")):
        return jsonify({"error": "Invalid email or password"}), 401

    token = str(uuid.uuid4())
    SESSIONS[token] = str(user["_id"])

    return jsonify({
        "message": "Signed in successfully",
        "token": token,
        "user_id": str(user["_id"]),
        "role": user.get("role", "user")
    }), 200


# -------------------- Đăng xuất --------------------
@auth_bp.route("/signout", methods=["POST"])
def signout():
    data = request.get_json()
    token = data.get("token")

    if not token or token not in SESSIONS:
        return jsonify({"error": "Invalid token"}), 400

    SESSIONS.pop(token, None)
    return jsonify({"message": "Signed out successfully"}), 200


# -------------------- Lấy thông tin người dùng --------------------
@auth_bp.route("/user/<user_id>", methods=["GET"])
def get_user_by_id(user_id):
    user = users_col.find_one({"_id": user_id}, {"password": 0})
    if not user:
        return jsonify({"error": "User not found"}), 404

    user["_id"] = str(user["_id"])
    return jsonify(user), 200
