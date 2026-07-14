from flask import Flask, request, jsonify, session, redirect
from flask_cors import CORS
import requests
import os
import json
import re
import html
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "toktokstudy-write-secret-key-2026")
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "PATCH", "DELETE", "OPTIONS"]}}, supports_credentials=True)

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "ntn_v665215908877AbvamFl83HijgGZlsKc1mqfCpVgEK01M5")
DATABASE_ID = os.getenv("DATABASE_ID", "38f18c7fe47080199517c92d4a76093e")
BLOG_DATABASE_ID = os.getenv("BLOG_DATABASE_ID", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "coin486")
NOTION_BASE_URL = "https://api.notion.com/v1"

NAVER_BLOG_IDS = ["jini5663", "coin9355", "jini7663_"]

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# 이미지 업로드는 최신 Notion API 버전이 필요해서 별도 헤더로 분리
FILE_API_VERSION = "2026-03-11"
FILE_HEADERS_JSON = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": FILE_API_VERSION,
}
FILE_HEADERS_MULTIPART = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": FILE_API_VERSION,
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


def _slugify(title):
    slug = re.sub(r"[^0-9a-zA-Z가-힣]+", "-", title).strip("-")
    timestamp = datetime.now(KST).strftime("%y%m%d%H%M")
    return f"{slug}-{timestamp}" if slug else timestamp


def _upload_image_to_notion(file_storage):
    """업로드된 이미지 파일을 Notion File Upload API로 전송하고 file_upload_id를 반환한다."""
    create_res = requests.post(
        f"{NOTION_BASE_URL}/file_uploads",
        headers=FILE_HEADERS_JSON,
        json={
            "filename": file_storage.filename or "image.png",
            "content_type": file_storage.content_type or "image/png",
        },
    )
    create_res.raise_for_status()
    file_upload_id = create_res.json()["id"]

    send_res = requests.post(
        f"{NOTION_BASE_URL}/file_uploads/{file_upload_id}/send",
        headers=FILE_HEADERS_MULTIPART,
        files={"file": (file_storage.filename, file_storage.stream, file_storage.content_type)},
    )
    send_res.raise_for_status()
    return file_upload_id


def _query_blog_posts(limit=30):
    """공개된 블로그 글 목록을 최신순으로 가져온다."""
    payload = {
        "filter": {"property": "공개", "checkbox": {"equals": True}},
        "sorts": [{"property": "작성일", "direction": "descending"}],
        "page_size": limit,
    }
    res = requests.post(f"{NOTION_BASE_URL}/databases/{BLOG_DATABASE_ID}/query", headers=HEADERS, json=payload)
    if res.status_code >= 300:
        return []
    return res.json().get("results", [])


def _get_post_by_slug(slug):
    """슬러그로 공개된 글 하나를 찾는다."""
    payload = {
        "filter": {
            "and": [
                {"property": "슬러그", "rich_text": {"equals": slug}},
                {"property": "공개", "checkbox": {"equals": True}},
            ]
        },
    }
    res = requests.post(f"{NOTION_BASE_URL}/databases/{BLOG_DATABASE_ID}/query", headers=HEADERS, json=payload)
    if res.status_code >= 300:
        return None
    results = res.json().get("results", [])
    return results[0] if results else None


def _get_page_blocks(page_id):
    res = requests.get(f"{NOTION_BASE_URL}/blocks/{page_id}/children?page_size=100", headers=HEADERS)
    if res.status_code >= 300:
        return []
    return res.json().get("results", [])


def _post_title(post):
    try:
        return post["properties"]["제목"]["title"][0]["plain_text"]
    except (KeyError, IndexError):
        return "(제목 없음)"


def _post_slug(post):
    try:
        return post["properties"]["슬러그"]["rich_text"][0]["plain_text"]
    except (KeyError, IndexError):
        return post["id"]


def _post_date(post):
    try:
        return post["properties"]["작성일"]["date"]["start"]
    except (KeyError, TypeError):
        return ""


def _post_excerpt(blocks, max_len=80):
    for b in blocks:
        if b.get("type") == "paragraph":
            text = "".join(rt.get("plain_text", "") for rt in b["paragraph"].get("rich_text", []))
            text = text.strip()
            if text:
                return text[:max_len] + ("…" if len(text) > max_len else "")
    return ""


def _render_blocks_html(blocks):
    parts = []
    for b in blocks:
        t = b.get("type")
        if t == "paragraph":
            text = "".join(rt.get("plain_text", "") for rt in b["paragraph"].get("rich_text", []))
            if text.strip():
                parts.append(f"<p>{html.escape(text)}</p>")
        elif t == "image":
            img = b.get("image", {})
            url = None
            if img.get("type") == "file":
                url = img.get("file", {}).get("url")
            elif img.get("type") == "external":
                url = img.get("external", {}).get("url")
            if url:
                parts.append(f'<img src="{html.escape(url)}" alt="" class="post-img" />')
    return "\n".join(parts)


LOGIN_FORM_HTML = """
<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>글쓰기 로그인 - 톡톡스터디</title>
<style>
  body{font-family:-apple-system,"Pretendard",sans-serif;background:#EEF3EF;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}
  form{background:#fff;padding:36px 32px;border-radius:14px;box-shadow:0 10px 30px -14px rgba(18,63,60,.28);width:280px;}
  h1{font-size:18px;margin:0 0 20px;color:#123F3C;}
  input{width:100%;padding:12px;border:1px solid #CBD9D4;border-radius:8px;box-sizing:border-box;font-size:15px;}
  button{width:100%;margin-top:14px;padding:12px;border:none;border-radius:8px;background:#F2B705;color:#123F3C;font-weight:700;font-size:15px;cursor:pointer;}
  .err{color:#EF6F53;font-size:13px;margin-top:10px;}
</style></head>
<body>
  <form method="POST" action="/write/login">
    <h1>톡톡스터디 블로그 글쓰기</h1>
    <input type="password" name="password" placeholder="비밀번호" autofocus required />
    <button type="submit">로그인</button>
    __ERROR_HTML__
  </form>
</body></html>
"""

WRITE_FORM_HTML = """
<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>새 글 작성 - 톡톡스터디</title>
<style>
  body{font-family:-apple-system,"Pretendard",sans-serif;background:#EEF3EF;margin:0;padding:40px 20px;}
  .wrap{max-width:640px;margin:0 auto;background:#fff;padding:32px;border-radius:14px;box-shadow:0 10px 30px -14px rgba(18,63,60,.28);}
  h1{font-size:20px;color:#123F3C;margin:0 0 24px;}
  label{display:block;font-size:13.5px;font-weight:700;margin:18px 0 8px;color:#16231F;}
  .hint{font-weight:400;color:#3D4E48;font-size:12.5px;}
  input,textarea{width:100%;padding:12px;border:1px solid #CBD9D4;border-radius:8px;box-sizing:border-box;font-size:15px;font-family:inherit;}
  input[type=file]{padding:8px;background:#F8FAF9;}
  textarea{min-height:280px;resize:vertical;line-height:1.6;}
  button{margin-top:22px;padding:12px 22px;border:none;border-radius:8px;background:#F2B705;color:#123F3C;font-weight:700;font-size:15px;cursor:pointer;}
  .logout{float:right;font-size:13px;color:#3D4E48;}
  .msg{margin-top:14px;font-size:13.5px;color:#1F6F6B;}
  .img-row{margin-bottom:8px;}
  .submitting{opacity:.6;pointer-events:none;}
</style></head>
<body>
  <div class="wrap">
    <a class="logout" href="/write/logout">로그아웃</a>
    <h1>새 글 작성</h1>
    <form method="POST" action="/write/submit" enctype="multipart/form-data" id="writeForm">
      <label>제목</label>
      <input type="text" name="title" required />
      <label>본문 <span class="hint">(문단 구분은 빈 줄로 — 이미지는 같은 순서의 문단 뒤에 삽입됩니다)</span></label>
      <textarea name="content" required></textarea>
      <label>이미지 <span class="hint">(최대 5장, 순서대로 문단 사이에 삽입됩니다 — 안 넣어도 됩니다)</span></label>
      <div class="img-row"><input type="file" name="image1" accept="image/*" /></div>
      <div class="img-row"><input type="file" name="image2" accept="image/*" /></div>
      <div class="img-row"><input type="file" name="image3" accept="image/*" /></div>
      <div class="img-row"><input type="file" name="image4" accept="image/*" /></div>
      <div class="img-row"><input type="file" name="image5" accept="image/*" /></div>
      <button type="submit" id="submitBtn">글 저장하기</button>
    </form>
    __MSG_HTML__
  </div>
  <script>
    document.getElementById("writeForm").addEventListener("submit", function(){
      document.getElementById("submitBtn").textContent = "저장 중... (이미지가 있으면 시간이 좀 걸려요)";
      document.getElementById("writeForm").classList.add("submitting");
    });
  </script>
</body></html>
"""


POST_PAGE_STYLE = """
<style>
  body{font-family:-apple-system,"Pretendard",sans-serif;background:#EEF3EF;color:#16231F;margin:0;line-height:1.7;}
  .wrap{max-width:720px;margin:0 auto;padding:40px 20px 80px;}
  a{color:#1F6F6B;}
  .top-nav{margin-bottom:28px;font-size:14px;}
  .top-nav a{text-decoration:none;color:#3D4E48;}
  h1{font-size:26px;color:#123F3C;margin:0 0 8px;line-height:1.4;}
  .date{color:#3D4E48;font-size:13px;margin-bottom:28px;}
  .post-body p{margin:0 0 18px;font-size:16px;}
  .post-img{width:100%;border-radius:12px;margin:8px 0 24px;display:block;}
  .cta{margin-top:48px;padding:24px;background:#fff;border-radius:14px;text-align:center;}
  .cta a{display:inline-block;margin-top:10px;background:#F2B705;color:#123F3C;font-weight:700;padding:12px 22px;border-radius:8px;text-decoration:none;}
  .list-card{display:block;background:#fff;border-radius:14px;padding:22px;margin-bottom:16px;text-decoration:none;color:inherit;box-shadow:0 6px 20px -12px rgba(18,63,60,.2);}
  .list-card h2{font-size:18px;margin:0 0 8px;color:#123F3C;}
  .list-card .date{margin:0;}
  .list-empty{color:#3D4E48;font-size:14px;}
</style>
"""


@app.route("/posts", methods=["GET"])
@app.route("/posts/list", methods=["GET"])
def posts_list():
    posts = _query_blog_posts()
    if not posts:
        cards_html = '<p class="list-empty">아직 작성된 글이 없습니다.</p>'
    else:
        cards = []
        for p in posts:
            title = _post_title(p)
            slug = _post_slug(p)
            date = _post_date(p)
            cards.append(
                f'<a class="list-card" href="/posts/{html.escape(slug)}">'
                f'<h2>{html.escape(title)}</h2><p class="date">{html.escape(date)}</p></a>'
            )
        cards_html = "\n".join(cards)

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>블로그 | 톡톡스터디</title>
<meta name="description" content="톡톡스터디에서 직접 작성한 방문과외, 화상과외, 와와학원, 회화수업 소식과 이야기를 확인하세요." />
{POST_PAGE_STYLE}
</head><body>
  <div class="wrap">
    <div class="top-nav"><a href="https://toktokstudy.com/">← 톡톡스터디 홈으로</a></div>
    <h1>톡톡스터디 블로그</h1>
    <div class="date">직접 작성한 소식들을 모았습니다.</div>
    {cards_html}
  </div>
</body></html>"""


@app.route("/posts/<slug>", methods=["GET"])
def post_detail(slug):
    post = _get_post_by_slug(slug)
    if not post:
        return "글을 찾을 수 없습니다.", 404

    title = _post_title(post)
    date = _post_date(post)
    blocks = _get_page_blocks(post["id"])
    body_html = _render_blocks_html(blocks)
    excerpt = _post_excerpt(blocks) or "톡톡스터디 블로그 글입니다."

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{html.escape(title)} | 톡톡스터디 블로그</title>
<meta name="description" content="{html.escape(excerpt)}" />
{POST_PAGE_STYLE}
</head><body>
  <div class="wrap">
    <div class="top-nav"><a href="/posts">← 블로그 목록으로</a></div>
    <h1>{html.escape(title)}</h1>
    <div class="date">{html.escape(date)}</div>
    <div class="post-body">
      {body_html}
    </div>
    <div class="cta">
      <div>방문과외, 화상과외, 와와학원, 회화수업이 궁금하다면?</div>
      <a href="https://wawa-consultation-form.onrender.com/">상담 신청하기</a>
    </div>
  </div>
</body></html>"""


@app.route('/')
def serve_index():
    try:
        with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'r', encoding='utf-8') as f:
            return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
    except Exception as e:
        return f"Error: {str(e)}", 500


@app.route("/write", methods=["GET"])
def write_page():
    if not session.get("is_admin"):
        return LOGIN_FORM_HTML.replace("__ERROR_HTML__", "")
    success = request.args.get("success")
    msg_html = '<p class="msg">글이 저장되었습니다.</p>' if success else ""
    return WRITE_FORM_HTML.replace("__MSG_HTML__", msg_html)


@app.route("/write/login", methods=["POST"])
def write_login():
    password = request.form.get("password", "")
    if password == ADMIN_PASSWORD:
        session["is_admin"] = True
        session.permanent = True
        return redirect("/write")
    return LOGIN_FORM_HTML.replace("__ERROR_HTML__", '<p class="err">비밀번호가 올바르지 않습니다.</p>')


@app.route("/write/logout", methods=["GET"])
def write_logout():
    session.pop("is_admin", None)
    return redirect("/write")


@app.route("/write/submit", methods=["POST"])
def write_submit():
    if not session.get("is_admin"):
        return redirect("/write")
    if not BLOG_DATABASE_ID:
        return "BLOG_DATABASE_ID 환경변수가 설정되지 않았습니다. Render 환경변수 설정을 먼저 완료해주세요.", 500

    title = (request.form.get("title") or "").strip()
    content = (request.form.get("content") or "").strip()
    if not title or not content:
        return "제목과 본문을 모두 입력해주세요.", 400

    # 이미지 최대 5장 업로드 (image1~image5, 빈 칸은 건너뜀)
    uploaded_image_ids = []
    for i in range(1, 6):
        f = request.files.get(f"image{i}")
        if f and f.filename:
            try:
                uploaded_image_ids.append(_upload_image_to_notion(f))
            except Exception as e:
                return f"이미지 업로드 중 오류가 발생했습니다 (image{i}): {str(e)}", 500

    slug = _slugify(title)
    properties = {
        "제목": {"title": [{"text": {"content": title}}]},
        "슬러그": {"rich_text": [{"text": {"content": slug}}]},
        "작성일": {"date": {"start": datetime.now(KST).strftime("%Y-%m-%d")}},
        "공개": {"checkbox": True},
    }

    paragraphs = [p.strip() for p in re.split(r"(?:\r?\n){2,}", content) if p.strip()]

    def paragraph_block(text):
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
        }

    def image_block(file_upload_id):
        return {
            "object": "block",
            "type": "image",
            "image": {"type": "file_upload", "file_upload": {"id": file_upload_id}},
        }

    # 1단계: 문단(텍스트)만 먼저 페이지에 저장한다
    payload = {
        "parent": {"database_id": BLOG_DATABASE_ID},
        "properties": properties,
        "children": [paragraph_block(p) for p in paragraphs],
    }
    res = requests.post(f"{NOTION_BASE_URL}/pages", headers=FILE_HEADERS_JSON, json=payload)
    if res.status_code >= 300:
        return jsonify(res.json()), res.status_code
    page_id = res.json()["id"]

    # 2단계: 저장된 문단들의 블록 ID를 순서대로 가져온다
    block_ids = []
    if uploaded_image_ids:
        list_res = requests.get(f"{NOTION_BASE_URL}/blocks/{page_id}/children", headers=FILE_HEADERS_JSON)
        print(f"[DEBUG] blocks GET status={list_res.status_code} body={list_res.text[:500]}")
        if list_res.status_code < 300:
            block_ids = [b["id"] for b in list_res.json().get("results", [])]
        print(f"[DEBUG] block_ids={block_ids} uploaded_image_ids={uploaded_image_ids}")

    # 3단계: 이미지를 정확히 "몇 번째 문단 뒤"인지 지정해서 하나씩 끼워넣는다
    for idx, fid in enumerate(uploaded_image_ids):
        if idx < len(block_ids):
            insert_payload = {
                "children": [image_block(fid)],
                "position": {"type": "after_block", "after_block": {"id": block_ids[idx]}},
            }
        else:
            # 문단보다 이미지가 많으면 나머지는 맨 뒤에 순서대로 추가
            insert_payload = {"children": [image_block(fid)]}
        patch_res = requests.patch(
            f"{NOTION_BASE_URL}/blocks/{page_id}/children",
            headers=FILE_HEADERS_JSON,
            json=insert_payload,
        )
        print(f"[DEBUG] image insert idx={idx} status={patch_res.status_code} body={patch_res.text[:500]}")

    return redirect("/write?success=1")


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
