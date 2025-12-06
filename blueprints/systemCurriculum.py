from flask import Blueprint, request, jsonify
import pandas as pd
import uuid
import io
from bson import ObjectId
import math
import time
from flask import jsonify

import requests


from utils.chunking import chunk_syllabus
from extensions.mongo import db
curriculum_bp = Blueprint('curriculum', __name__)

system_curriculum_col = db['systemCurriculum']
book_embeddings_col = db['bookEmbeddings']
system_book_chunks_col = db['system_book_chunks']
batch_results_col = db["batchEmbeddingResults"]


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

STATIC_BOOK_IDS = [
    "b198a0d8-c4b1-4308-9f50-3146deaf3e2d", "a9646581-e533-4249-9dc1-73f546fabf25",
    "640a04de-add9-431c-aefe-df7aff0994e8", "9fb1e9c5-14a9-46e6-aa73-4ea57157d87a",
    "1fd60539-1dc7-4e54-a289-974561cfb051", "5930e163-fe91-4358-91cb-e8079ce8e0a8",
    "82d639e5-688a-46ca-ae20-db21d8e68265", "1249f679-97e5-4fb8-8d22-b22bd9e371df",
    "386a797c-196a-4d96-865f-b44d22ae03cb", "12dc4bd2-dcbc-4774-954f-b845cc4787ec",
    "2a9e40c1-c80c-4c30-8667-1879ddc45f34", "48eff1ae-9c74-42bd-ae63-41729a114e4c",
    "3ef4aeaf-35a2-403a-a2ad-4c6ec4241710", "d2708749-3ff3-4df6-a489-19f706896238",
    "b78ba2da-7ddc-40c1-bb0e-64d1b0d2efb0", "763d5291-a050-473d-87fa-99916e090e72",
    "27b9b74a-3d4f-403e-b5b3-1542d98e34be", "c121aff2-23ea-4c67-a965-76e333aeb143",
    "5fb46d50-c9d5-4bec-b415-212d66c727fb", "b47aafce-e32a-4dcc-bb46-2ccd35c4ee41",
    "3cac79b3-99cb-4aee-8b7a-f21890e1cd73", "b704af9c-b546-400d-a215-0dd0ead6ccb3",
    "782e1dda-f5bc-4f2b-8e75-11e6af007f87", "bf720230-58fa-49f4-9528-39f29204e463",
    "373e9d37-37fa-46e0-bc82-4354fce4c8b2", "e9764eda-270b-4552-9971-f535e7ebd7d5",
    "5052e661-5118-4eef-8a97-92353fbf3886", "ad027f9b-dbbf-4f8f-a0b8-33b575ee0988",
    "cafc2ac9-7ac1-4b4e-8271-5a80a07b9c06", "d47c6383-05fe-436d-8698-3711cca68dec",
    "11e2aee1-b6e5-4bdf-bd33-29f311b849f7", "5a0c7005-976f-4136-b61a-a3762d7e8993",
    "ee5bbba8-c101-4182-9a1f-0bb64a6b9ab7", "87b4228a-3aa0-43af-bc12-2c07163fd57c",
    "60b54ab2-aef3-4b89-88b6-9e46027d1201", "49de727a-05f9-4cc7-bc64-49bf1e109d18",
    "cc0909c4-837a-4860-ac3a-fec71210ea63", "a49dd591-74ab-4251-b05c-1cb520ce502b",
    "6e94f419-b366-4c74-b343-73e4913b2652", "c79e8138-a475-44e3-b277-5626d2ca2c78",
    "a240df96-9091-4a4e-ae51-26d7683a5339", "ca86cb3e-94c3-4450-82cd-a3c778042d6d",
    "98116f3a-cdf1-4b72-b21f-8f6999098799", "710fd334-7c16-4700-a41c-a6bbdec8c710",
    "acc22718-ac43-441d-9dbe-e71700ea3acd", "24722ee3-9b81-4678-8f68-027d22857a24",
    "0d2bc5c5-0511-4d6b-afd6-f3c7738d5a98", "d9304255-98b5-48c6-aa4b-58eb512cc903",
    "b8c2b947-02d5-477f-b544-0a8b54852db5", "81832450-2ed9-4e42-9f28-ff6872c0498a",
    "2135a7a3-9601-4d49-88b0-3775e8231408", "baf101e7-1941-4cc1-9ccf-951162ceb6de",
    "6e6e649c-c1f5-459b-9da5-347d2374e377", "663cbf4f-21a6-4aa9-a889-05e7a22527e1",
    "f22e7539-3ede-4b14-b77b-9143b0e7b95f", "a95e3f6d-df41-47f6-b7c2-1bd780c79921",
    "0a9734fb-6cfd-4eeb-aab5-0a91e1fe13c5", "2006db6b-3a53-4296-905b-de199ed474ec",
    "8ddd9b0d-6395-43f2-956d-6fd7da0992de", "7692a4e0-8829-4a79-a30b-086788c96e4c",
    "a433cb0c-a6fe-4a2f-a965-e4762fb1e602", "5f5587bb-73f5-4fd7-b8fe-46899c183a62",
    "d5ee45a5-dda3-4134-944b-500f3bdf80da", "cb14a396-bf90-445b-8ae3-d432ff6cab12",
    "d776fe8e-6762-4315-a6c8-0df2a81a6948", "8b8082c8-01ba-4bda-b85c-f74a954eee95",
    "65684359-4591-4c0e-a47f-79fab2b072fd", "36fcbdd7-649d-47c0-b722-d7023a35f885",
    "e71ea053-6fd1-47c9-afa5-968b7627b8b7", "af74312c-82ac-420c-9315-3acbbf4a972a",
    "b97bfe78-4b4d-4838-b4c5-bf7d23579de7", "f7a7325a-e56b-4efc-ac9f-f181d64f31d5",
    "0ac5c812-64d6-42aa-a3ee-40d44a51d2cd", "929598b9-e589-4861-96af-1b2151cf7ac2",
    "f1c465c8-590a-4e24-86fe-972ff5aa64e4", "42dea51e-4254-48d6-8e6d-47c0a0123842",
    "115b6e2e-6d87-4530-a60c-dabf96d1d98f", "fcdc7443-54b4-4ecc-aeb3-12c075434b8a",
    "03e946c3-a604-4c1d-96b4-de6016cf17c9", "077343a7-af32-4913-93c9-b87b2a9a5113",
    "1d5483c6-b64a-4922-8af6-90d214efcd14", "73a51a63-bcb2-4633-8b1b-1a676d9b0931",
    "f2e69f0d-c3ce-4605-b92e-75c2655da8c4", "e7549372-5367-4d6b-b392-ea9ada3b9bb6",
    "8c557751-d57d-4fbb-a02b-c1fcf9860238", "02a47f76-5668-4572-8c6d-ecbc1d83a7a0",
    "4eb356ca-a150-498f-a205-c70ef5503581", "7b1c6c2c-ba28-4630-8173-d496f3dcf2b4",
    "845f893e-d5a1-48cb-9880-061b5b751209", "0a795767-32c5-4841-84b1-28423d8abf21",
    "6ac83317-06c4-4708-b977-1dc2aa3d87aa", "685f5ccf-7620-41f3-82df-d79d04729bbd",
    "1e3bebbd-6874-45dc-b880-5d2742fab89a", "1402286b-66a4-424c-b24e-459b30c50c49",
    "9277dccf-4510-468d-a10d-2f08095a3b33", "bde038cb-45aa-4bf6-b058-e462f8b5a239",
    "8c79b0fa-34dc-4010-88c5-3ad743484b1a", "1b5f429c-b7f5-42bf-af3e-b523cad42662",
    "d09d8e8f-3f4d-4e28-8bfc-cc7dd55606e3", "e8473416-98ad-49fa-99d8-001a1d19d76d",
    "dae59db7-7afa-4922-8c0a-ffafddda3780", "30edde8e-5539-4627-a82d-8a4b0613f06c",
    "ea719000-00a8-4024-b7c4-9592002242ed", "68fc5617-7a54-426c-b44f-72f39d00ca02",
    "59f548ed-7d21-449b-b566-bd0006c4e3cf", "302b2f35-6a21-429d-a187-ef564198c8db",
    "70d8a25a-cf9c-4602-a41b-42616573e4d1", "752e78d2-d1b7-4842-9e1e-8a581ac5f0f7",
    "1c5230b9-23fb-428a-8a68-9db49dc275f1", "58a74f2a-bed9-4888-b127-e65a4d2714d5",
    "5071aafe-cf55-4b7f-8940-7e23b5a18d09", "66bfd534-0380-48a0-a770-d32cf5062e20",
    "7a020ec7-57bc-4008-b496-cb20d3514fe1", "6833a84d-d323-4971-8d01-3a69f3d3c1b3",
    "971ad862-5fd7-40ae-ab98-6fc5f7bcd6e2", "1af25064-e84d-47bd-b5cc-4f26dadd60d9",
    "ead27288-9559-4591-9317-78ee02526d96", "34049331-fb63-4917-aece-7fda738cf0e9",
    "f5e98f33-0d76-4605-a029-ebb255b57091", "8382b251-d205-417a-a4b4-c2430cb2249e",
    "9131eb7f-62a5-41db-bac2-e52fe1abda44", "a853979c-ce60-4d8d-9910-9b7a024b6695",
    "f0e6881a-5b8a-4ae8-83c1-5db9c276da16", "d3ba3b66-80ad-42f1-9f3e-3e92042f9e1b",
    "56e91f35-f85e-4cb2-ad1e-c55df6683207", "1a14a80d-d75d-4fd5-b2b5-9fa891e7f204",
    "38d6a917-009b-48d2-ba35-c95815c432bc", "9241a198-f2f5-4d03-8cf7-e85902e49fad",
    "ffbd6e4a-2e44-42a2-812f-6c5907d3bb0c", "99e68ff4-e1f1-4b71-8c73-b23e8db3709a",
    "38b54428-88e0-4123-8d57-45702d97c4b8", "f4f9dbab-1a1d-44bc-b508-b9fbb0951515",
    "b1f75d50-f420-4f95-8efc-710d04276b43", "2dac174b-60e1-4ae1-8ade-32be82bd8435",
    "8b0f9909-ee5c-4cbe-8d9e-da1085104926", "52b283e5-faf2-4dce-9b53-742be45836fd",
    "40d14f96-c95e-4c6d-bed1-4185d1590c1c", "ab571755-8f9a-4eaa-8091-fe17791b0149",
    "6a3fde3e-5eae-4ba9-86a8-b46f375e05de", "a00a4084-e817-44a0-9cf8-7d7604775d9f",
    "77ba4948-bb1c-41f2-925b-4264d1a9c05e", "da8ad151-2330-4590-8fb1-4827fd65f97a",
    "676724b5-15ca-434c-a7c6-4a9e1d74093d", "0206166f-2ee8-4059-b012-af1e62ded6ac",
    "38abd18f-3525-40e6-ae65-a882e9f25cc4", "90686567-e2a0-42bf-9ff9-72be34c976f5",
    "9fce8af3-c825-4a00-a3ec-b08f4fbd17b5", "4cd1414f-566a-4ea1-adc5-bf3b28f00012",
    "78f2d220-19f2-45a1-9737-68bce5f17707", "832fd280-479d-4f33-9f27-8eb28435213b",
    "10a2dc10-8122-4639-bee0-162f73c0b6ee", "a4492a0f-2d52-4ab4-9d38-0754faa0adf2",
    "653e599a-7e3a-4c13-967f-4f28cdb758b7", "473ef13f-d732-47ed-8b02-2a6aa080336f",
    "e3e7e15f-1e39-444b-8865-9c5cda9a9933", "fda97b91-e5f8-46b1-8b29-358a60d9d302",
    "ad923fdf-d912-4af7-b62f-871cefcafdd2", "7ae14e47-f575-4b1c-bee8-51a85ee3b814",
    "d4704def-b4ca-495c-a241-e083c08a7460", "92766cec-1944-4b89-86a0-2d70d8ed161e",
    "b39807ae-be4a-4804-912a-0e7dda85a9c2", "8f56e6d3-0e34-409f-9a5d-32ca56db9c09",
    "a408fccf-cc47-4439-953a-7ecb13bd81bb", "06343d9f-4139-4f2c-b514-03c8f96e0be7",
    "5070a9ee-b959-4e7a-8b8f-9bb5f1de5abd", "c4fc41d5-f2ba-48d4-a915-af4956361f28",
    "718aff31-c157-4e4f-bc33-b8e077c4631c", "be0fbf4a-207d-4753-9719-346816258414",
    "241a414f-6ca8-4419-9824-c0334d858c2e", "da1bbf03-4900-4aaf-aae7-46d4b2a1707d",
    "e97017fe-3502-4220-90db-7949b4550b7b", "9d438300-de8d-43a3-9c5b-418458217555",
    "b65d5f52-90f8-4853-9d01-f0aafa3e82ff", "058fc5da-b401-410e-be2c-234644cf090d",
    "49c526f0-0d9b-4dee-9e4b-2a7f0bdc0b77", "10162cde-690c-4112-97f2-b3db6e14a9ce",
    "e5fba21c-791e-4930-a861-3c72dbb1972d", "840b7a57-d9d9-4405-be6f-a0d780b66573",
    "bf166eee-a504-4198-91d7-a8a3bb4809a7", "c780b3be-5efa-496b-82c4-ef8716e51e14",
    "dd9aa5fb-8f1c-4122-9278-27cdb100ab0b", "c9a66ae1-45ec-478e-b7a0-d4801b049719",
    "6c5cc598-83bc-492f-a43c-814e07811630", "63e2d756-b852-409b-b041-82b56b975786",
    "26ab21dd-98c5-4f6a-a131-e6f2bc0e4d44", "1614ac05-fab7-4a63-8ac3-6ad9567db02f",
    "63c59cae-9833-4a60-a390-f72f3d5b8996", "f043bb44-aa19-4a99-aaa8-2503b67f90ed",
    "73971090-8f51-4065-a5fb-1a0e9b590e8b", "3bd73f43-f8e2-4f08-9183-b87f222ebd63",
    "7475d474-b190-4ca7-af8f-32bcede325e5", "ca9d9c16-0659-4a2d-8a66-1f49331a0de5",
    "18529237-e1f9-4518-8007-e890a9a95825", "f2a55fb0-07d8-4878-90ce-a41c97ee2def",
    "5167d57e-55b9-433c-b760-8f2a6f398ed3", "4d946e04-b276-4b4e-92bd-49cd83bdc042",
    "1a556e07-6ddb-4876-8a0e-39737296bee3", "b02c08bf-a788-4c05-a8e2-2f1f5ef43e2f",
    "c16b9ed6-cf9c-41bc-a6a2-afe0e4fc7a10", "96f7cc68-845a-4381-9508-212c48796139",
    "4566230b-fd9e-49b2-8f1f-5c930ce66473", "94b037f4-828f-465b-a299-bd3e3ad09851",
    "b8359d96-e764-4593-9110-8b91379dc874", "24578662-21da-45fe-808a-85bc564524c6",
    "84445b9f-04e5-4bed-97ce-17164a06b0b1", "b6d719d6-e539-499a-8dbd-9ad501750d4d",
    "ee87c61a-ae93-47cb-8809-a391da62c632", "f80e0d87-ca76-45e9-809f-a22b7f202501",
    "af5965a5-fe30-4aed-811d-075a5fb8693b", "132e67b3-72e3-45a8-99af-a35b8c81540a",
    "f20b7eec-96f3-4c11-9cff-19391f72e471", "14ffce81-41bc-4964-913d-de4d42b2be67",
    "ffdd4fb7-b8d4-45ba-93cf-925566ea689c", "af72151f-3175-4026-9655-0d54486abe05",
    "9fc654d8-f2f3-4cb7-a197-84ed02d0dddc", "db853fd6-0fe1-4979-974b-b0ef650e04f3",
    "0ea99a58-006c-4f5b-afa6-9d6594653154", "47e00216-f45b-4efb-8914-b93a5e72b529",
    "ba66d44f-6601-4059-b748-5e7a5430078a", "64dc5ae6-96b5-46e6-bc7e-7519db6fed52",
    "b0dde90e-2962-44bf-9c44-a4c4176597c0", "773106e8-a8ed-4f14-81d9-7ab05ce4c53d",
    "01f55191-d273-41e9-940e-2a783a768a92", "c90a1006-57a6-4bbc-a90f-a1b8ae1d4f11",
    "cfe5bbca-339e-45d3-a6cd-71da09ba7589", "0a0376cf-cdaf-46df-81d8-3e76ef25af5f",
    "53840ffe-6af4-4e92-96b7-a9def83b1eab", "7002847d-8726-4822-bf22-995fd289d849",
    "81382d43-7f5a-4078-afa4-ef770e2aeb30", "84e7b392-ce62-420b-824e-4030f438c103",
    "5f31f4de-04d1-4ec1-bac6-be5d24d507d0", "630e190b-4c2b-4ade-93fb-8ff06dc13917",
    "0f1312e3-d849-4a3f-b35d-f71e92c640f6", "e262b17b-e890-4b36-872e-afed264583e3",
    "775ff328-33f8-44be-99cd-e6bc7d04734f", "f233923a-4635-44e6-b3e0-271667fb9543",
    "7d0e4c8f-15f0-4cfa-b6c6-74139428ce1c", "e3b73642-be1c-4105-95f7-92237e10861b",
    "01af81a4-0024-4156-b483-737755202667", "1a1090b7-c720-4df2-851c-5d5d7454d115",
    "bb580d6b-8889-4afa-a589-d7d493bccec3", "75ef488d-51ec-42ce-b237-e80a76a0d410",
    "5f5d8b99-2dc0-43b8-a332-cfcc71067839"
]

MAX_RETRY = 5
DELAY_BETWEEN_REQUEST = 0.15

@curriculum_bp.route('/run-static-embedding-task', methods=['POST'])
def run_static_embedding_task():
    try:
        # === 1. Login Neureader 1 lần ===
        login_url = "https://qc.neureader.net/v2/auth/login"
        login_body = {
            "email": "11223735",
            "password": "000000"
        }

        login_res = requests.post(login_url, json=login_body)
        if login_res.status_code != 200:
            return jsonify({
                "error": f"Login failed: {login_res.status_code}",
                "details": login_res.text
            }), 500

        token_data = login_res.json()
        access_token = token_data.get("data", {}).get("accessToken")

        if not access_token:
            return jsonify({"error": "Missing accessToken in login response"}), 500

        # === 2. Call API cho từng BOOK ID ===
        embedding_url = "https://qc.neureader.net/v2/readie/embedding"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        results = []

        for book_id in STATIC_BOOK_IDS:
            body = {
                "bookId": book_id,
                "pageNumber": 0,
                "pageSize": 10000000
            }

            retry = 0
            success = False
            response_json = None

            while retry < MAX_RETRY:
                try:
                    resp = requests.post(embedding_url, json=body, headers=headers)

                    if resp.status_code == 200:
                        response_json = resp.json()
                        success = True
                        break

                    # Nếu 503 → backoff
                    if resp.status_code == 503:
                        time.sleep(1 * (2 ** retry))  # 1s → 2s → 4s → 8s...
                    else:
                        # lỗi khác: bỏ qua luôn
                        break

                except Exception:
                    # backoff nhẹ
                    time.sleep(1 * (2 ** retry))

                retry += 1

            # === Không thành công sau retry ===
            if not success:
                results.append({
                    "bookId": book_id,
                    "length": None,
                    "error": f"API Error after {MAX_RETRY} retries"
                })
                continue

            # === Thành công ===
            embeddings = response_json.get("data", {}).get("embeddings", [])
            length = len(embeddings)

            batch_results_col.update_one(
                {"bookId": book_id},
                {
                    "$set": {
                        "bookId": book_id,
                        "length": length
                    }
                },
                upsert=True
            )

            results.append({
                "bookId": book_id,
                "length": length
            })

            # === Delay nhẹ để tránh 503 tiếp theo ===
            time.sleep(DELAY_BETWEEN_REQUEST)

        return jsonify({
            "static_book_ids": STATIC_BOOK_IDS,
            "result_count": len(results),
            "results": results
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

