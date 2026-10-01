from flask import Flask, request, jsonify, session, redirect, Response
from flask_cors import CORS
import requests
import os
import json
import re
import html
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime, format_datetime
from urllib.parse import quote

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "toktokstudy-write-secret-key-2026")
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "PATCH", "DELETE", "OPTIONS"]}}, supports_credentials=True)

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("DATABASE_ID", "38f18c7fe47080199517c92d4a76093e")
BLOG_DATABASE_ID = os.getenv("BLOG_DATABASE_ID", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
NOTION_BASE_URL = "https://api.notion.com/v1"

NAVER_BLOG_IDS = ["jini5663", "coin9355", "jini7663_"]
TISTORY_BLOG_IDS = ["jini5663"]

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


# ============================================================
# 노션 호출 안정화
# 노션 API가 가끔 일시적으로 실패(5xx/429/연결 오류)하는데, 그때 블로그가
# "아직 작성된 글이 없습니다"로 보이는 문제가 있었다.
# 1) 실패하면 잠깐 쉬었다가 최대 3번까지 다시 시도하고
# 2) 마지막으로 성공한 결과를 메모리에 보관해 두었다가, 끝내 실패하면 그걸 보여준다.
# ============================================================
import time as _time

NOTION_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _notion_request(method, url, attempts=3, **kwargs):
    """노션 API 호출. 일시적인 오류면 재시도한다. 마지막 응답(또는 None)을 돌려준다."""
    kwargs.setdefault("timeout", 15)
    res = None
    for i in range(attempts):
        try:
            res = requests.request(method, url, **kwargs)
            if res.status_code not in NOTION_RETRY_STATUSES:
                return res
        except requests.RequestException:
            res = None
        if i < attempts - 1:
            _time.sleep(0.6 * (i + 1))
    return res


BLOG_POSTS_CACHE = {"posts": None, "time": 0}   # 마지막으로 성공한 전체 글 목록
BLOG_POSTS_FRESH_SECONDS = 60                    # 이 시간 안에는 노션을 다시 부르지 않음
POST_BLOCKS_CACHE = {}                           # 글 id -> 마지막으로 성공한 본문 블록

# ============================================================
# 방문자 카운터 - 노션 DB 기반 (Render 재배포에도 값이 유지됨)
# 예전 방식(visitor_counter.json 로컬 파일)은 재배포 시 초기화되는
# 문제가 있어서 노션 페이지 2개(total_visits / today_visits)를
# 대신 사용하도록 교체함.
# ============================================================
TOTAL_VISITS_PAGE_ID = "3d918c7f-e470-812c-aa94-ffbd81d316d7"
TODAY_VISITS_PAGE_ID = "3d918c7f-e470-8101-a46f-ff2c6caa46ac"


def _get_visit_page(page_id):
    res = _notion_request("GET", f"{NOTION_BASE_URL}/pages/{page_id}", headers=HEADERS)
    if res is None:
        raise RuntimeError("Notion unreachable")
    res.raise_for_status()
    props = res.json()["properties"]
    count = props["Count"]["number"] or 0
    date_obj = props["LastUpdatedDate"]["date"]
    last_date = date_obj["start"] if date_obj else None
    return count, last_date


def _set_visit_page(page_id, count, date_str):
    body = {"properties": {"Count": {"number": count}, "LastUpdatedDate": {"date": {"start": date_str}}}}
    res = _notion_request("PATCH", f"{NOTION_BASE_URL}/pages/{page_id}", headers=HEADERS, json=body)
    if res is None:
        raise RuntimeError("Notion unreachable")
    res.raise_for_status()


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


POSTS_PER_PAGE = 12


def _fetch_all_blog_posts():
    """노션에서 공개 글 전체를 최신순으로 가져온다. 실패하면 None."""
    results = []
    cursor = None
    while True:
        payload = {
            "filter": {"property": "공개", "checkbox": {"equals": True}},
            "sorts": [{"property": "작성일", "direction": "descending"}],
            "page_size": 100,
        }
        if cursor:
            payload["start_cursor"] = cursor
        res = _notion_request("POST", f"{NOTION_BASE_URL}/databases/{BLOG_DATABASE_ID}/query", headers=HEADERS, json=payload)
        if res is None or res.status_code >= 300:
            return None
        data = res.json()
        results.extend(data.get("results", []))
        if not data.get("has_more") or not data.get("next_cursor"):
            return results
        cursor = data["next_cursor"]


def _query_blog_posts(limit=None):
    """공개된 블로그 글 목록(최신순). 노션이 실패하면 마지막으로 성공한 목록을 쓴다."""
    now = _time.time()
    cached = BLOG_POSTS_CACHE["posts"]
    if cached is not None and now - BLOG_POSTS_CACHE["time"] < BLOG_POSTS_FRESH_SECONDS:
        posts = cached
    else:
        fresh = _fetch_all_blog_posts()
        if fresh is not None:
            BLOG_POSTS_CACHE["posts"] = fresh
            BLOG_POSTS_CACHE["time"] = now
            posts = fresh
        else:
            app.logger.warning("Notion blog query failed; serving cached list (%s posts)", len(cached or []))
            posts = cached or []
    return list(posts) if limit is None else posts[:limit]


def _blog_posts_unavailable():
    """노션도 실패하고 보관된 목록도 없는 상태인지."""
    return BLOG_POSTS_CACHE["posts"] is None


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
    res = _notion_request("POST", f"{NOTION_BASE_URL}/databases/{BLOG_DATABASE_ID}/query", headers=HEADERS, json=payload)
    if res is not None and res.status_code < 300:
        results = res.json().get("results", [])
        return results[0] if results else None
    # 노션 실패 시: 보관해 둔 글 목록에서 찾는다
    for post in _query_blog_posts():
        if _post_slug(post) == slug:
            return post
    return None


def _get_page_blocks(page_id):
    res = _notion_request("GET", f"{NOTION_BASE_URL}/blocks/{page_id}/children?page_size=100", headers=HEADERS)
    if res is None or res.status_code >= 300:
        return POST_BLOCKS_CACHE.get(page_id, [])
    blocks = res.json().get("results", [])
    POST_BLOCKS_CACHE[page_id] = blocks
    return blocks


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


def _post_views(post):
    try:
        return post["properties"]["조회수"]["number"] or 0
    except (KeyError, TypeError):
        return 0


def _increment_post_views(page_id, current_views):
    """조회수를 1 올려서 Notion에 저장. 실패해도 페이지 표시에는 영향 없도록 예외를 삼킨다."""
    try:
        new_count = (current_views or 0) + 1
        body = {"properties": {"조회수": {"number": new_count}}}
        requests.patch(f"{NOTION_BASE_URL}/pages/{page_id}", headers=HEADERS, json=body, timeout=5)
        return new_count
    except Exception:
        return current_views


def _post_excerpt(blocks, max_len=80):
    for b in blocks:
        if b.get("type") == "paragraph":
            text = "".join(rt.get("plain_text", "") for rt in b["paragraph"].get("rich_text", []))
            text = text.strip()
            if text:
                return text[:max_len] + ("…" if len(text) > max_len else "")
    return ""


def _first_image_url(blocks):
    """글 본문의 첫 번째 이미지 주소 (없으면 None)."""
    for b in blocks:
        if b.get("type") == "image":
            img = b.get("image", {})
            if img.get("type") == "file":
                return img.get("file", {}).get("url")
            if img.get("type") == "external":
                return img.get("external", {}).get("url")
    return None


_URL_RE = re.compile(r"(https?://[^\s)]+)")


def _render_rich_text_html(rich_text_list):
    """rich_text 배열을 굵게/링크까지 살려서 HTML로 변환한다."""
    parts = []
    for rt in rich_text_list:
        text = rt.get("plain_text", "") or rt.get("text", {}).get("content", "")
        if not text:
            continue
        escaped = html.escape(text)
        if rt.get("annotations", {}).get("bold"):
            escaped = f"<strong>{escaped}</strong>"
        href = rt.get("href") or ((rt.get("text") or {}).get("link") or {}).get("url")
        if href:
            escaped = f'<a href="{html.escape(href)}" target="_blank" rel="noopener">{escaped}</a>'
        parts.append(escaped)
    return "".join(parts)


def _render_blocks_html(blocks):
    parts = []
    i, n = 0, len(blocks)
    while i < n:
        b = blocks[i]
        t = b.get("type")
        if t == "bulleted_list_item":
            items = []
            while i < n and blocks[i].get("type") == "bulleted_list_item":
                inner = _render_rich_text_html(blocks[i]["bulleted_list_item"].get("rich_text", []))
                if inner.strip():
                    items.append(f"<li>{inner}</li>")
                i += 1
            if items:
                parts.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif t == "paragraph":
            inner = _render_rich_text_html(b["paragraph"].get("rich_text", []))
            if inner.strip():
                parts.append(f"<p>{inner}</p>")
        elif t in ("heading_1", "heading_2", "heading_3"):
            tag = {"heading_1": "h2", "heading_2": "h3", "heading_3": "h4"}[t]
            inner = _render_rich_text_html(b[t].get("rich_text", []))
            if inner.strip():
                parts.append(f"<{tag}>{inner}</{tag}>")
        elif t == "quote":
            inner = _render_rich_text_html(b["quote"].get("rich_text", []))
            if inner.strip():
                parts.append(f"<blockquote>{inner}</blockquote>")
        elif t == "image":
            img = b.get("image", {})
            url = None
            if img.get("type") == "file":
                url = img.get("file", {}).get("url")
            elif img.get("type") == "external":
                url = img.get("external", {}).get("url")
            if url:
                parts.append(f'<img src="{html.escape(url)}" alt="" class="post-img" />')
        i += 1
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
  textarea{min-height:120px;resize:vertical;line-height:1.6;}
  button{margin-top:22px;padding:12px 22px;border:none;border-radius:8px;background:#F2B705;color:#123F3C;font-weight:700;font-size:15px;cursor:pointer;}
  .logout{float:right;font-size:13px;color:#3D4E48;}
  .msg{margin-top:14px;font-size:13.5px;color:#1F6F6B;}
  .block{margin-top:22px;padding-top:18px;border-top:1px dashed #CBD9D4;}
  .block:first-of-type{border-top:none;padding-top:0;}
  .img-label{font-size:12.5px;color:#3D4E48;font-weight:400;margin:10px 0 6px;}
  .submitting{opacity:.6;pointer-events:none;}
</style></head>
<body>
  <div class="wrap">
    <a class="logout" href="/write/logout">로그아웃</a>
    <h1>새 글 작성</h1>
    <p class="hint">이미지 → 본문 순서로 글에 들어갑니다 (이미지1 → 본문1 → … → 이미지5 → 본문5 → 본문6 마무리). 이미지 없이 넘어가도 됩니다.</p>
    <form method="POST" action="/write/submit" enctype="multipart/form-data" id="writeForm">
      <label>제목</label>
      <input type="text" name="title" required />

      <div class="block">
        <label>이미지 1 (메인 이미지) <span class="hint">(선택)</span></label>
        <input type="file" name="image1" accept="image/*" />
        <div class="img-label">↓ 이미지 아래에 들어갈 본문</div>
        <label>본문 1</label>
        <textarea name="content1"></textarea>
      </div>

      <div class="block">
        <label>이미지 2 <span class="hint">(선택)</span></label>
        <input type="file" name="image2" accept="image/*" />
        <div class="img-label">↓ 이미지 아래에 들어갈 본문</div>
        <label>본문 2</label>
        <textarea name="content2"></textarea>
      </div>

      <div class="block">
        <label>이미지 3 <span class="hint">(선택)</span></label>
        <input type="file" name="image3" accept="image/*" />
        <div class="img-label">↓ 이미지 아래에 들어갈 본문</div>
        <label>본문 3</label>
        <textarea name="content3"></textarea>
      </div>

      <div class="block">
        <label>이미지 4 <span class="hint">(선택)</span></label>
        <input type="file" name="image4" accept="image/*" />
        <div class="img-label">↓ 이미지 아래에 들어갈 본문</div>
        <label>본문 4</label>
        <textarea name="content4"></textarea>
      </div>

      <div class="block">
        <label>이미지 5 <span class="hint">(선택)</span></label>
        <input type="file" name="image5" accept="image/*" />
        <div class="img-label">↓ 이미지 아래에 들어갈 본문</div>
        <label>본문 5</label>
        <textarea name="content5"></textarea>
      </div>

      <div class="block">
        <label>본문 6 <span class="hint">(마무리 글 — 이미지 없이 끝)</span></label>
        <textarea name="content6"></textarea>
      </div>

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
  .post-body h2{font-size:21px;color:#123F3C;margin:32px 0 14px;line-height:1.4;}
  .post-body h3{font-size:18.5px;color:#123F3C;margin:28px 0 12px;line-height:1.4;}
  .post-body h4{font-size:16.5px;color:#123F3C;margin:24px 0 10px;line-height:1.4;}
  .post-body blockquote{margin:0 0 18px;padding:14px 18px;background:#F8FAF9;border-left:3px solid #1F6F6B;border-radius:0 8px 8px 0;color:#3D4E48;}
  .post-body ul{margin:0 0 18px;padding-left:22px;}
  .post-body ul li{margin-bottom:6px;font-size:16px;}
  .post-body a{color:#1F6F6B;text-decoration:underline;word-break:break-all;}
  .post-img{width:100%;border-radius:12px;margin:8px 0 24px;display:block;}
  .cta{margin-top:48px;padding:24px;background:#fff;border-radius:14px;text-align:center;}
  .cta a{display:inline-block;margin-top:10px;background:#F2B705;color:#123F3C;font-weight:700;padding:12px 22px;border-radius:8px;text-decoration:none;}
  .list-card{display:block;background:#fff;border-radius:14px;padding:22px;margin-bottom:16px;text-decoration:none;color:inherit;box-shadow:0 6px 20px -12px rgba(18,63,60,.2);}
  .list-card h2{font-size:18px;margin:0 0 8px;color:#123F3C;}
  .list-card .date{margin:0;}
  .list-empty{color:#3D4E48;font-size:14px;}
  .posts-wrap{max-width:960px;}
  .posts-wrap{max-width:1080px;}
  .list-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;}
  .list-grid .list-card{margin-bottom:0;}
  .list-card h2{font-size:16.5px;line-height:1.5;word-break:keep-all;}
  .pager{display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin-top:32px;}
  .pg-btn{min-width:40px;padding:9px 14px;border-radius:8px;background:#fff;color:#123F3C;text-decoration:none;font-size:14px;font-weight:600;text-align:center;box-shadow:0 4px 14px -10px rgba(18,63,60,.3);}
  a.pg-btn:hover{background:#1F6F6B;color:#fff;}
  .pg-cur{background:#123F3C;color:#fff;}
  @media(max-width:900px){
    .list-grid{grid-template-columns:1fr 1fr;}
  }
  @media(max-width:600px){
    .list-grid{grid-template-columns:1fr;}
  }
</style>
"""


@app.route("/posts", methods=["GET"])
@app.route("/posts/list", methods=["GET"])
@app.route("/posts/all", methods=["GET"])
def posts_list():
    all_posts = _query_blog_posts()
    if not all_posts and _blog_posts_unavailable():
        return Response(
            '<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta http-equiv="refresh" content="5">'
            '<title>잠시 후 다시 시도해 주세요 | 톡톡스터디</title></head><body style="font-family:sans-serif;padding:40px;text-align:center">'
            '<p>글 목록을 불러오는 중입니다. 잠시 후 자동으로 다시 시도합니다.</p></body></html>',
            status=503, headers={"Retry-After": "30"}, mimetype="text/html")
    total_pages = max(1, -(-len(all_posts) // POSTS_PER_PAGE))
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    page = min(max(page, 1), total_pages)
    posts = all_posts[(page - 1) * POSTS_PER_PAGE: page * POSTS_PER_PAGE]
    page_url = "https://blog.toktokstudy.com/posts" + ("" if page == 1 else f"?page={page}")
    page_title = "블로그 | 톡톡스터디" if page == 1 else f"블로그 {page}페이지 | 톡톡스터디"

    pager_html = ""
    if total_pages > 1:
        links = []
        if page > 1:
            prev_href = "/posts" if page == 2 else f"/posts?page={page - 1}"
            links.append(f'<a class="pg-btn" href="{prev_href}" rel="prev">← 이전</a>')
        for n in range(1, total_pages + 1):
            href = "/posts" if n == 1 else f"/posts?page={n}"
            if n == page:
                links.append(f'<span class="pg-btn pg-cur">{n}</span>')
            else:
                links.append(f'<a class="pg-btn" href="{href}">{n}</a>')
        if page < total_pages:
            links.append(f'<a class="pg-btn" href="/posts?page={page + 1}" rel="next">다음 →</a>')
        pager_html = f'<nav class="pager">{"".join(links)}</nav>'

    if not posts:
        cards_html = '<p class="list-empty">아직 작성된 글이 없습니다.</p>'
    else:
        cards = []
        for p in posts:
            title = _post_title(p)
            slug = _post_slug(p)
            date = _post_date(p)
            views = _post_views(p)
            cards.append(
                f'<a class="list-card" href="/posts/{html.escape(slug)}">'
                f'<h2>{html.escape(title)}</h2>'
                f'<p class="date">{html.escape(date)} · 조회 {views}</p></a>'
            )
        cards_html = f'<div class="list-grid">\n{"".join(cards)}\n</div>'

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta name="naver-site-verification" content="a205c395081d92de1981faf577652125f32445cd" />
<link rel="alternate" type="application/rss+xml" title="톡톡스터디 블로그" href="https://blog.toktokstudy.com/rss.xml" />
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9716996159524167" crossorigin="anonymous"></script>
<title>{page_title}</title>
<meta name="description" content="톡톡스터디에서 직접 작성한 방문과외, 화상과외, 와와학원, 회화수업 소식과 이야기를 확인하세요." />
<link rel="canonical" href="{page_url}" />
<meta property="og:type" content="website" />
<meta property="og:site_name" content="톡톡스터디" />
<meta property="og:title" content="톡톡스터디 블로그" />
<meta property="og:description" content="톡톡스터디에서 직접 작성한 방문과외, 화상과외, 와와학원, 회화수업 소식과 이야기를 확인하세요." />
<meta property="og:url" content="{page_url}" />
<meta property="og:locale" content="ko_KR" />
{POST_PAGE_STYLE}
</head><body>
  <div class="wrap posts-wrap">
    <div class="top-nav"><a href="https://toktokstudy.com/">← 톡톡스터디 홈으로</a></div>
    <h1>톡톡스터디 블로그</h1>
    <div class="date">직접 작성한 소식들을 모았습니다.</div>
    {cards_html}
    {pager_html}
  </div>
</body></html>"""


@app.route("/posts/<slug>", methods=["GET"])
def post_detail(slug):
    post = _get_post_by_slug(slug)
    if not post:
        return "글을 찾을 수 없습니다.", 404

    title = _post_title(post)
    date = _post_date(post)
    views = _increment_post_views(post["id"], _post_views(post))
    blocks = _get_page_blocks(post["id"])
    body_html = _render_blocks_html(blocks)
    excerpt = _post_excerpt(blocks) or "톡톡스터디 블로그 글입니다."
    canonical = f"https://blog.toktokstudy.com/posts/{quote(slug)}"
    og_image_tags = ""
    if _first_image_url(blocks):
        # 노션 이미지 주소는 1시간 뒤 만료되므로, 항상 최신 주소로 연결해주는 고정 주소를 사용
        og_image = f"{canonical}/og-image"
        og_image_tags = (
            f'<meta property="og:image" content="{html.escape(og_image)}" />\n'
            f'<meta name="twitter:image" content="{html.escape(og_image)}" />\n'
        )

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta name="naver-site-verification" content="a205c395081d92de1981faf577652125f32445cd" />
<link rel="alternate" type="application/rss+xml" title="톡톡스터디 블로그" href="https://blog.toktokstudy.com/rss.xml" />
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9716996159524167" crossorigin="anonymous"></script>
<title>{html.escape(title)} | 톡톡스터디 블로그</title>
<meta name="description" content="{html.escape(excerpt)}" />
<link rel="canonical" href="{html.escape(canonical)}" />
<meta property="og:type" content="article" />
<meta property="og:site_name" content="톡톡스터디" />
<meta property="og:title" content="{html.escape(title)}" />
<meta property="og:description" content="{html.escape(excerpt)}" />
<meta property="og:url" content="{html.escape(canonical)}" />
<meta property="og:locale" content="ko_KR" />
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="{html.escape(title)}" />
<meta name="twitter:description" content="{html.escape(excerpt)}" />
{og_image_tags}{POST_PAGE_STYLE}
</head><body>
  <div class="wrap">
    <div class="top-nav"><a href="/posts">← 블로그 목록으로</a></div>
    <h1>{html.escape(title)}</h1>
    <div class="date">{html.escape(date)} · 조회 {views}</div>
    <div class="post-body">
      {body_html}
    </div>
    <div class="cta">
      <div>방문과외, 화상과외, 와와학원, 회화수업이 궁금하다면?</div>
      <a href="https://wawa-consultation-form.onrender.com/">상담 신청하기</a>
    </div>
  </div>
</body></html>"""


@app.route("/posts/<slug>/og-image", methods=["GET"])
def post_og_image(slug):
    """글의 첫 번째 이미지로 연결되는 고정 주소 (카카오톡·검색 미리보기용). 조회수는 올리지 않는다."""
    post = _get_post_by_slug(slug)
    if not post:
        return "이미지를 찾을 수 없습니다.", 404
    url = _first_image_url(_get_page_blocks(post["id"]))
    if not url:
        return "이미지를 찾을 수 없습니다.", 404
    return redirect(url, code=302)


@app.route("/sitemap.xml", methods=["GET"])
def sitemap():
    base = "https://blog.toktokstudy.com"
    urlset = ET.Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")

    def add_url(loc, lastmod=None):
        url_el = ET.SubElement(urlset, "url")
        ET.SubElement(url_el, "loc").text = loc
        if lastmod:
            ET.SubElement(url_el, "lastmod").text = lastmod

    posts = _query_blog_posts()
    if not posts and _blog_posts_unavailable():
        # 노션 장애로 글을 하나도 못 가져온 경우, 빈 사이트맵을 주지 않고 나중에 다시 오도록 한다
        return Response("Temporarily unavailable", status=503, headers={"Retry-After": "300"})

    add_url(f"{base}/posts")
    for post in posts:
        slug = _post_slug(post)
        # 글을 수정하면 구글이 알 수 있도록 노션의 마지막 수정일을 우선 사용
        edited = (post.get("last_edited_time") or "")[:10]
        date = edited or _post_date(post)
        add_url(f"{base}/posts/{slug}", date if date else None)

    xml_str = ET.tostring(urlset, encoding="utf-8", xml_declaration=True)
    return Response(xml_str, mimetype="application/xml")


# ============================================================
# RSS 피드 - 노션 블로그 DB에서 매번 새로 생성
# 글을 새로 올리면 별도 작업 없이 자동으로 반영된다.
# (노션 호출을 줄이기 위해 10분간 결과를 메모리에 캐시)
# ============================================================
RSS_CACHE = {"xml": None, "time": 0}
RSS_CACHE_SECONDS = 600
RSS_ITEM_LIMIT = 30


def _post_pubdate(post):
    """작성일(날짜 또는 날짜+시간)을 RSS용 RFC 822 형식으로 변환."""
    raw = _post_date(post)
    if not raw:
        raw = post.get("created_time", "")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
    except ValueError:
        dt = datetime.now(KST)
    return format_datetime(dt)


def _build_rss_xml():
    import time
    from concurrent.futures import ThreadPoolExecutor

    now = time.time()
    if RSS_CACHE["xml"] and now - RSS_CACHE["time"] < RSS_CACHE_SECONDS:
        return RSS_CACHE["xml"]

    base = "https://blog.toktokstudy.com"
    posts = _query_blog_posts(limit=RSS_ITEM_LIMIT)
    if not posts and RSS_CACHE["xml"]:
        return RSS_CACHE["xml"]

    # 각 글의 요약(첫 문단)을 병렬로 가져온다
    def excerpt_of(post):
        try:
            return _post_excerpt(_get_page_blocks(post["id"]), max_len=200)
        except Exception:
            return ""

    with ThreadPoolExecutor(max_workers=8) as ex:
        excerpts = list(ex.map(excerpt_of, posts))

    rss = ET.Element("rss", version="2.0")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "톡톡스터디 블로그"
    ET.SubElement(channel, "link").text = f"{base}/posts"
    ET.SubElement(channel, "description").text = (
        "톡톡스터디에서 직접 작성한 방문과외, 화상과외, 와와학원, 회화수업 소식과 학습 정보"
    )
    ET.SubElement(channel, "language").text = "ko"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(KST))
    atom_link = ET.SubElement(channel, "atom:link")
    atom_link.set("href", f"{base}/rss.xml")
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    for post, excerpt in zip(posts, excerpts):
        title = _post_title(post)
        url = f"{base}/posts/{quote(_post_slug(post))}"
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = title
        ET.SubElement(item, "link").text = url
        ET.SubElement(item, "guid", isPermaLink="true").text = url
        ET.SubElement(item, "description").text = excerpt or title
        ET.SubElement(item, "pubDate").text = _post_pubdate(post)

    xml_bytes = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    if posts:  # 노션 조회 실패 시에는 캐시하지 않음
        RSS_CACHE["xml"] = xml_bytes
        RSS_CACHE["time"] = now
    return xml_bytes


@app.route("/rss.xml", methods=["GET"])
@app.route("/rss", methods=["GET"])
@app.route("/feed", methods=["GET"])
def rss_feed():
    return Response(_build_rss_xml(), mimetype="application/rss+xml; charset=utf-8")


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


def _split_urls(text):
    """URL(http/https)이 섞인 텍스트를 (일반글, 링크) 조각으로 나눈다."""
    parts, last = [], 0
    for m in _URL_RE.finditer(text):
        if m.start() > last:
            parts.append(("text", text[last:m.start()]))
        parts.append(("link", m.group(1)))
        last = m.end()
    if last < len(text):
        parts.append(("text", text[last:]))
    return parts or [("text", text)]


def _inline_rich_text(text):
    """**굵게** 표기를 bold로, URL은 실제 링크로 변환해 Notion rich_text 배열을 만든다."""
    rich = []
    for part in re.split(r"(\*\*[^*]+\*\*)", text):
        if not part:
            continue
        bold = part.startswith("**") and part.endswith("**") and len(part) > 4
        content = part[2:-2] if bold else part
        for kind, seg in _split_urls(content):
            if not seg:
                continue
            obj = {"type": "text", "text": {"content": seg}}
            if kind == "link":
                obj["text"]["link"] = {"url": seg}
            if bold or kind == "link":
                obj["annotations"] = {"bold": bold, "italic": False, "strikethrough": False, "underline": False, "code": False, "color": "default"}
            rich.append(obj)
    return rich or [{"type": "text", "text": {"content": ""}}]


def _line_type(line):
    """줄 맨 앞 기호를 보고 (블록타입, 기호를 뗀 내용) 을 반환한다."""
    if line.startswith("### "):
        return "heading_3", line[4:]
    if line.startswith("## "):
        return "heading_2", line[3:]
    if line.startswith("# "):
        return "heading_1", line[2:]
    if line.startswith("> "):
        return "quote", line[2:]
    if line.startswith("- ") or line.startswith("* "):
        return "bulleted_list_item", line[2:]
    return "paragraph", line


def _chunk_to_blocks(chunk):
    """빈 줄로 구분된 한 덩어리(chunk)를 실제 Notion 블록 목록으로 변환한다."""
    blocks = []
    buf_type, buf_lines = None, []

    def flush():
        if not buf_lines:
            return
        if buf_type == "bulleted_list_item":
            for l in buf_lines:
                if l.strip():
                    blocks.append({
                        "object": "block", "type": "bulleted_list_item",
                        "bulleted_list_item": {"rich_text": _inline_rich_text(l.strip())},
                    })
        else:
            text = "\n".join(buf_lines).strip()
            if not text:
                return
            btype = buf_type or "paragraph"
            blocks.append({"object": "block", "type": btype, btype: {"rich_text": _inline_rich_text(text)}})

    for raw_line in chunk.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        t, text = _line_type(line)
        if t != buf_type:
            flush()
            buf_type, buf_lines = t, []
        buf_lines.append(text)
    flush()
    return blocks


def image_block(file_upload_id):
    return {
        "object": "block",
        "type": "image",
        "image": {"type": "file_upload", "file_upload": {"id": file_upload_id}},
    }


@app.route("/write/submit", methods=["POST"])
def write_submit():
    if not session.get("is_admin"):
        return redirect("/write")
    if not BLOG_DATABASE_ID:
        return "BLOG_DATABASE_ID 환경변수가 설정되지 않았습니다. Render 환경변수 설정을 먼저 완료해주세요.", 500

    title = (request.form.get("title") or "").strip()
    # content1~content6: 각 이미지 바로 아래에 들어가는 본문 조각 (content6은 마무리)
    content_parts = [(request.form.get(f"content{i}") or "").strip() for i in range(1, 7)]
    if not title or not any(content_parts):
        return "제목과 본문을 입력해주세요.", 400

    # 이미지 최대 5장 업로드 (image1~image5, 빈 칸은 건너뜀)
    image_ids = {}
    for i in range(1, 6):
        f = request.files.get(f"image{i}")
        if f and f.filename:
            try:
                image_ids[i] = _upload_image_to_notion(f)
            except Exception as e:
                return f"이미지 업로드 중 오류가 발생했습니다 (image{i}): {str(e)}", 500

    slug = _slugify(title)
    properties = {
        "제목": {"title": [{"text": {"content": title}}]},
        "슬러그": {"rich_text": [{"text": {"content": slug}}]},
        "작성일": {"date": {"start": datetime.now(KST).strftime("%Y-%m-%d")}},
        "공개": {"checkbox": True},
    }

    def _content_to_blocks(text):
        chunks = [c.strip() for c in re.split(r"(?:\r?\n){2,}", text) if c.strip()]
        result = []
        for chunk in chunks:
            result.extend(_chunk_to_blocks(chunk))
        return result

    # 이미지1 → 본문1 → 이미지2 → 본문2 → ... → 이미지5 → 본문5 → 본문6(마무리) 순서로 이어붙인다
    blocks = []
    for i in range(1, 7):
        if i <= 5 and i in image_ids:
            blocks.append(image_block(image_ids[i]))
        blocks.extend(_content_to_blocks(content_parts[i - 1]))

    payload = {
        "parent": {"database_id": BLOG_DATABASE_ID},
        "properties": properties,
        "children": blocks,
    }
    res = requests.post(f"{NOTION_BASE_URL}/pages", headers=FILE_HEADERS_JSON, json=payload)
    if res.status_code >= 300:
        return jsonify(res.json()), res.status_code

    return redirect("/write?success=1")


def _admin_only():
    """관리자(글쓰기 화면 로그인)만 쓸 수 있는 기능을 막는다. 로그인 안 됐으면 403 응답을 돌려준다."""
    if not session.get("is_admin"):
        return jsonify({"error": "forbidden"}), 403
    return None


@app.route("/db/filter", methods=["POST"])
def query_database_filtered():
    denied = _admin_only()
    if denied:
        return denied
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
    denied = _admin_only()
    if denied:
        return denied
    res = requests.get(
        f"{NOTION_BASE_URL}/pages/{page_id}",
        headers=HEADERS,
    )
    return jsonify(res.json()), res.status_code


@app.route("/page/<page_id>", methods=["PATCH"])
def update_page(page_id):
    denied = _admin_only()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    res = requests.patch(
        f"{NOTION_BASE_URL}/pages/{page_id}",
        headers=HEADERS,
        json=data,
    )
    return jsonify(res.json()), res.status_code


@app.route("/page/<page_id>", methods=["DELETE"])
def archive_page(page_id):
    denied = _admin_only()
    if denied:
        return denied
    res = requests.patch(
        f"{NOTION_BASE_URL}/pages/{page_id}",
        headers=HEADERS,
        json={"archived": True},
    )
    return jsonify(res.json()), res.status_code


@app.route("/visit", methods=["POST"])
def visit_hit():
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    try:
        total, _ = _get_visit_page(TOTAL_VISITS_PAGE_ID)
        total += 1
        _set_visit_page(TOTAL_VISITS_PAGE_ID, total, today_str)

        today_count, last_date = _get_visit_page(TODAY_VISITS_PAGE_ID)
        today_count = today_count + 1 if last_date == today_str else 1
        _set_visit_page(TODAY_VISITS_PAGE_ID, today_count, today_str)

        return jsonify({"total": total, "today": today_count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/visit", methods=["GET"])
def visit_count():
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    try:
        total, _ = _get_visit_page(TOTAL_VISITS_PAGE_ID)
        today_count, last_date = _get_visit_page(TODAY_VISITS_PAGE_ID)
        today_count = today_count if last_date == today_str else 0
        return jsonify({"total": total, "today": today_count})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    TISTORY_KEYWORDS = ["와와학원", "방문", "화상과외", "회화"]
    for blog_id in TISTORY_BLOG_IDS:
        try:
            rss_url = f"https://{blog_id}.tistory.com/rss"
            res = requests.get(rss_url, timeout=5)
            res.encoding = "utf-8"
            root = ET.fromstring(res.content)
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                if not any(kw in title for kw in TISTORY_KEYWORDS):
                    continue
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
                    "blogId": f"{blog_id}(티스토리)",
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
    denied = _admin_only()
    if denied:
        return denied
    # Notion API 주소로만 보낸다 (외부 주소로 Notion 키가 새어 나가지 않도록)
    if notion_path.startswith("http"):
        return jsonify({"error": "forbidden"}), 403
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
