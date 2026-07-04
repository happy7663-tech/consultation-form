from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "PATCH", "DELETE", "OPTIONS"]}})

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "ntn_v665215908877AbvamFl83HijgGZlsKc1mqfCpVgEK01M5")
DATABASE_ID = os.getenv("DATABASE_ID", "38f18c7fe47080199517c92d4a76093e")
NOTION_BASE_URL = "https://api.notion.com/v1"

NAVER_BLOG_IDS = ["jini5663", "coin9355", "jini7663_"]

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# 정적 파일 제공
@app.route('/')
def serve_index():
    try:
        with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'r', encoding='utf-8') as f:
            return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
    except Exception as e:
        return f"Error: {str(e)}", 500


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


@app.route("/blog-feed", methods=["GET"])
def blog_feed():
    """등록된 네이버 블로그 여러 개의 RSS를 합쳐 최신순으로 반환"""
    limit = request.args.get("limit", default=10, type=int)
    items = []
    for blog_id in NAVER_BLOG_IDS:
        try:
            rss_url = f"https://rss.blog.naver.com/{blog_id}.xml"
            res = requests.get(rss_url, timeout=5)
            res.encoding = "utf-8"
            root = ET.fromstring(res.content)
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                pub_date = (item.findtext("pubDate") or "").strip()
                try:
                    sort_key = parsedate_to_datetime(pub_date).timestamp()
                except Exception:
                    sort_key = 0
                items.append({
                    "title": title,
                    "link": link,
                    "pubDate": pub_date,
                    "blogId": blog_id,
                    "_sort": sort_key,
                })
        except Exception:
            continue
    items.sort(key=lambda x: x["_sort"], reverse=True)
    for it in items:
        it.pop("_sort", None)
    return jsonify(items[:limit])


@app.route("/proxy/<path:notion_path>", methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"])
def generic_proxy(notion_path):
    """Notion API 범용 프록시"""
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
    print("  GET    /blog-feed       - 네이버 블로그 최신글 목록")
    print("  *      /proxy/<path>    - Notion API 직접 프록시")
    app.run(port=5000, debug=True)
