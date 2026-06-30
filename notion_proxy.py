from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

NOTION_TOKEN = "ntn_v665215908877AbvamFl83HijgGZlsKc1mqfCpVgEK01M5"
DATABASE_ID = "38f18c7fe47080199517c92d4a76093e"
NOTION_BASE_URL = "https://api.notion.com/v1"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    if path.endswith('.html') or path.endswith('.css') or path.endswith('.js'):
        return send_from_directory('.', path)
    return "Not Found", 404

@app.route("/db", methods=["GET"])
def query_database():
    """DB 전체 조회"""
    payload = request.get_json(silent=True) or {}
    res = requests.post(
        f"{NOTION_BASE_URL}/databases/{DATABASE_ID}/query",
        headers=HEADERS,
        json=payload,
    )
    return jsonify(res.json()), res.status_code


@app.route("/db/filter", methods=["POST"])
def query_database_filtered():
    """필터 조건으로 DB 조회"""
    payload = request.get_json(silent=True) or {}
    res = requests.post(
        f"{NOTION_BASE_URL}/databases/{DATABASE_ID}/query",
        headers=HEADERS,
        json=payload,
    )
    return jsonify(res.json()), res.status_code


@app.route("/page", methods=["POST"])
def create_page():
    """DB에 페이지(행) 생성"""
    data = request.get_json(silent=True) or {}
    payload = {
        "parent": {"database_id": DATABASE_ID},
        "properties": data.get("properties", {}),
    }
    if "children" in data:
        payload["children"] = data["children"]
    res = requests.post(
        f"{NOTION_BASE_URL}/pages",
        headers=HEADERS,
        json=payload,
    )
    return jsonify(res.json()), res.status_code


@app.route("/page/<page_id>", methods=["GET"])
def get_page(page_id):
    """페이지 조회"""
    res = requests.get(
        f"{NOTION_BASE_URL}/pages/{page_id}",
        headers=HEADERS,
    )
    return jsonify(res.json()), res.status_code


@app.route("/page/<page_id>", methods=["PATCH"])
def update_page(page_id):
    """페이지 속성 업데이트"""
    data = request.get_json(silent=True) or {}
    res = requests.patch(
        f"{NOTION_BASE_URL}/pages/{page_id}",
        headers=HEADERS,
        json=data,
    )
    return jsonify(res.json()), res.status_code


@app.route("/page/<page_id>", methods=["DELETE"])
def archive_page(page_id):
    """페이지 보관(삭제)"""
    res = requests.patch(
        f"{NOTION_BASE_URL}/pages/{page_id}",
        headers=HEADERS,
        json={"archived": True},
    )
    return jsonify(res.json()), res.status_code


@app.route("/proxy/<path:notion_path>", methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"])
def generic_proxy(notion_path):
    """Notion API 범용 프록시"""
    # Flask가 ://의 이중 슬래시를 단일 슬래시로 변환하므로 복원
    if notion_path.startswith("https:/") and not notion_path.startswith("https://"):
        notion_path = "https://" + notion_path[7:]
    elif notion_path.startswith("http:/") and not notion_path.startswith("http://"):
        notion_path = "http://" + notion_path[6:]

    if notion_path.startswith("http"):
        url = notion_path
    else:
        url = f"{NOTION_BASE_URL}/{notion_path}"
    res = requests.request(
        method=request.method,
        url=url,
        headers=HEADERS,
        json=request.get_json(silent=True),
        params=request.args,
    )
    return jsonify(res.json()), res.status_code


if __name__ == "__main__":
    print("Notion Proxy Server running on http://localhost:5000")
    print(f"  Database ID: {DATABASE_ID}")
    print()
    print("Endpoints:")
    print("  GET    /db              - DB 전체 조회")
    print("  POST   /db/filter       - 필터 조회")
    print("  POST   /page            - 페이지 생성")
    print("  GET    /page/<id>       - 페이지 조회")
    print("  PATCH  /page/<id>       - 페이지 수정")
    print("  DELETE /page/<id>       - 페이지 보관")
    print("  *      /proxy/<path>    - Notion API 직접 프록시")
    app.run(port=5000, debug=True)
