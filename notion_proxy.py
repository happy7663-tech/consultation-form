from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
import json
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
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

KST = timezone(timedelta(hours=9))
COUNTER_FILE = os.path.join(os.path.dirname(__file__), "visitor_counter.json")
_counter_lock = threading.Lock()


def _load_counter():
    if os.path.exists(COUNTER_FILE):
        try:
            with open(COUNTER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"total": 0, "today": 0, "date": ""}


def _save_counter(data):
    with open(COUNTER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


@app.route('/')
def serve_index():
    try:
        with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'r', encoding='utf-8') as f:
            return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
    except Exception as e:
        return f"Error: {str(e)}", 500


@app.route("/db/filter", methods=["POST"])
def query_database_filtered():
    payload = request.get_json(silent=True) or {}
    res = requests.post(
        f"{NOTION_BASE_URL}/databases/{DATABASE_ID}/query",
        headers=HEADERS,
        json=payload,
    )
    return jsonify(res.json()), res.status_code


@app.route("/page", methods=["POST"])
def create_page():
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
    res = requests.get(
        f"{NOTION_BASE_URL}/pages/{page_id}",
        headers=HEADERS,
    )
    return jsonify(res.json()), res.status_code


@app.route("/page/<page_id>", methods=["PATCH"])
def update_page(page_id):
    data = request.get_json(silent=True) or {}
    res = requests.patch(
        f"{NOTION_BASE_URL}/pages/{page_id}",
        headers=HEADERS,
        json=data,
    )
    return jsonify(res.json()), res.status_code


@app.route("/page/<page_id>", methods=["DELETE"])
def archive_page(page_id):
    res = requests.patch(
        f"{NOTION_BASE_URL}/pages/{page_id}",
        headers=HEADERS,
        json={"archived": True},
    )
    return jsonify(res.json()), res.status_code


@app.route("/visit", methods=["POST"])
def visit_hit():
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    with _counter_lock:
        data = _load_counter()
        if data.get("date") != today_str:
            data["date"] = today_str
            data["today"] = 0
        data["total"] = data.get("total", 0) + 1
        data["today"] = data.get("today", 0) + 1
        _save_counter(data)
        result = {"total": data["total"], "today": data["today"]}
    return jsonify(result)


@app.route("/visit", methods=["GET"])
def visit_count():
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    with _counter_lock:
        data = _load_counter()
        today_count = data.get("today", 0) if data.get("date") == today_str else 0
    return jsonify({"total": data.get("total", 0), "today": today_count})


@app.route("/blog-feed", methods=["GET"])
def blog_feed():
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
    app.run(port=5000, debug=True)
