from pymongo import MongoClient
from config import MONGO_URI, MONGO_DB_NAME

mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB_NAME]