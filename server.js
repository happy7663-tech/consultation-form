const express = require('express');
const https = require('https');
const fs = require('fs');
const path = require('path');

const app = express();
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

const CONFIG_FILE = path.join(__dirname, 'config.json');
const NOTION_TOKEN = 'ntn_v665215908877AbvamFl83HijgGZlsKc1mqfCpVgEK01M5';
const NOTION_VERSION = '2022-06-28';

function loadConfig() {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
  } catch {
    return {};
  }
}

function saveConfig(data) {
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(data, null, 2));
}

function notionRequest(method, endpoint, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const options = {
      hostname: 'api.notion.com',
      path: `/v1/${endpoint}`,
      method,
      headers: {
        'Authorization': `Bearer ${NOTION_TOKEN}`,
        'Content-Type': 'application/json',
        'Notion-Version': NOTION_VERSION,
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {})
      }
    };
    const req = https.request(options, (res) => {
      let body = '';
      res.on('data', chunk => body += chunk);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(body) }); }
        catch { resolve({ status: res.statusCode, data: body }); }
      });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

// Notion 데이터베이스 생성
async function createDatabase(parentPageId) {
  const body = {
    parent: { type: 'page_id', page_id: parentPageId },
    icon: { type: 'emoji', emoji: '📋' },
    title: [{ type: 'text', text: { content: '교육 상담 신청 목록' } }],
    properties: {
      '이름': { title: {} },
      '연락처': { phone_number: {} },
      '이메일': { email: {} },
      '상담 분야': {
        select: {
          options: [
            { name: '입시 상담', color: 'blue' },
            { name: '학습 방법', color: 'green' },
            { name: '진로 탐색', color: 'yellow' },
            { name: '수강 문의', color: 'orange' },
            { name: '기타', color: 'gray' }
          ]
        }
      },
      '희망 상담 시간': {
        select: {
          options: [
            { name: '평일 오전 (9시~12시)', color: 'pink' },
            { name: '평일 오후 (13시~17시)', color: 'purple' },
            { name: '평일 저녁 (18시~21시)', color: 'red' },
            { name: '주말 오전 (9시~12시)', color: 'blue' },
            { name: '주말 오후 (13시~17시)', color: 'green' }
          ]
        }
      },
      '문의 내용': { rich_text: {} },
      '개인정보 동의': {
        select: {
          options: [
            { name: '동의', color: 'green' },
            { name: '미동의', color: 'red' }
          ]
        }
      },
      '신청 상태': {
        select: {
          options: [
            { name: '신규 접수', color: 'blue' },
            { name: '확인 중', color: 'yellow' },
            { name: '상담 완료', color: 'green' },
            { name: '취소', color: 'gray' }
          ]
        }
      },
      '신청일시': { created_time: {} }
    }
  };
  return notionRequest('POST', 'databases', body);
}

// 상담 신청 데이터 Notion에 저장
async function addConsultation(databaseId, formData) {
  const body = {
    parent: { database_id: databaseId },
    properties: {
      '이름': { title: [{ text: { content: formData.name || '' } }] },
      '연락처': { phone_number: formData.phone || null },
      '이메일': { email: formData.email || null },
      '상담 분야': formData.subject ? { select: { name: formData.subject } } : undefined,
      '희망 상담 시간': formData.time ? { select: { name: formData.time } } : undefined,
      '문의 내용': { rich_text: [{ text: { content: formData.message || '' } }] },
      '개인정보 동의': { select: { name: formData.privacy === 'on' ? '동의' : '미동의' } },
      '신청 상태': { select: { name: '신규 접수' } }
    }
  };
  // undefined 속성 제거
  Object.keys(body.properties).forEach(k => {
    if (body.properties[k] === undefined) delete body.properties[k];
  });
  return notionRequest('POST', 'pages', body);
}

// ==================== 라우터 ====================

// 설정 페이지 (최초 1회)
app.get('/setup', (req, res) => {
  res.send(getSetupPage(req.query.error));
});

app.post('/setup', async (req, res) => {
  const { pageUrl } = req.body;
  if (!pageUrl) return res.redirect('/setup?error=URL을 입력해주세요');

  // URL에서 page ID 추출 (32자리 hex 또는 하이픈 포함)
  const match = pageUrl.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})|([0-9a-f]{32})/i);
  if (!match) return res.redirect('/setup?error=올바른 Notion 페이지 URL이 아닙니다');

  let pageId = match[0].replace(/-/g, '');
  // 하이픈 형식으로 변환
  pageId = `${pageId.slice(0,8)}-${pageId.slice(8,12)}-${pageId.slice(12,16)}-${pageId.slice(16,20)}-${pageId.slice(20)}`;

  const result = await createDatabase(pageId);
  if (result.status !== 200 && result.status !== 201) {
    const msg = result.data?.message || 'Notion DB 생성에 실패했습니다. 페이지가 인테그레이션에 공유되었는지 확인해주세요.';
    return res.redirect(`/setup?error=${encodeURIComponent(msg)}`);
  }

  saveConfig({ databaseId: result.data.id });
  res.redirect('/');
});

// 메인 상담 신청 페이지
app.get('/', (req, res) => {
  const config = loadConfig();
  if (!config.databaseId) return res.redirect('/setup');
  res.send(getFormPage());
});

// 폼 제출
app.post('/submit', async (req, res) => {
  const config = loadConfig();
  if (!config.databaseId) return res.status(400).json({ success: false, message: '설정이 필요합니다.' });

  const { name, phone, email, subject, time, message, privacy } = req.body;
  if (!name || !phone || !email) {
    return res.status(400).json({ success: false, message: '필수 항목을 모두 입력해주세요.' });
  }
  if (!privacy) {
    return res.status(400).json({ success: false, message: '개인정보 수집·이용에 동의해주세요.' });
  }

  const result = await addConsultation(config.databaseId, req.body);
  if (result.status === 200 || result.status === 201) {
    res.json({ success: true, message: '상담 신청이 완료되었습니다!' });
  } else {
    res.status(500).json({ success: false, message: result.data?.message || '저장 중 오류가 발생했습니다.' });
  }
});

// ==================== HTML 템플릿 ====================

function getSetupPage(error) {
  return `<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>초기 설정 | 교육 상담 신청</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Pretendard', 'Apple SD Gothic Neo', sans-serif; background: #f0f4ff; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
  .card { background: white; border-radius: 20px; padding: 48px; max-width: 600px; width: 100%; box-shadow: 0 4px 40px rgba(0,0,0,0.08); }
  .icon { font-size: 48px; margin-bottom: 16px; }
  h1 { font-size: 24px; font-weight: 700; color: #1a1a2e; margin-bottom: 8px; }
  .subtitle { color: #666; font-size: 15px; line-height: 1.6; margin-bottom: 32px; }
  .steps { background: #f8f9ff; border-radius: 12px; padding: 24px; margin-bottom: 28px; }
  .steps h3 { font-size: 14px; font-weight: 700; color: #4361ee; margin-bottom: 14px; text-transform: uppercase; letter-spacing: 0.5px; }
  .step { display: flex; gap: 12px; margin-bottom: 12px; font-size: 14px; color: #444; line-height: 1.5; }
  .step:last-child { margin-bottom: 0; }
  .step-num { width: 22px; height: 22px; background: #4361ee; color: white; border-radius: 50%; display: flex; align-items: center; justify-content: font-size: 11px; justify-content: center; font-size: 11px; font-weight: 700; flex-shrink: 0; margin-top: 1px; }
  .form-group { margin-bottom: 16px; }
  label { display: block; font-size: 14px; font-weight: 600; color: #333; margin-bottom: 8px; }
  input[type="text"] { width: 100%; padding: 14px 16px; border: 2px solid #e0e0e0; border-radius: 10px; font-size: 14px; transition: border-color 0.2s; outline: none; }
  input[type="text"]:focus { border-color: #4361ee; }
  .btn { width: 100%; padding: 16px; background: linear-gradient(135deg, #4361ee, #7b2ff7); color: white; border: none; border-radius: 12px; font-size: 16px; font-weight: 700; cursor: pointer; transition: opacity 0.2s; }
  .btn:hover { opacity: 0.9; }
  .error { background: #fff0f0; border: 1px solid #ffcccc; color: #c00; border-radius: 10px; padding: 12px 16px; font-size: 14px; margin-bottom: 20px; }
  code { background: #f0f0f0; padding: 2px 6px; border-radius: 4px; font-family: monospace; font-size: 13px; }
</style>
</head>
<body>
<div class="card">
  <div class="icon">⚙️</div>
  <h1>Notion 연동 설정</h1>
  <p class="subtitle">최초 1회 설정 후 상담 신청 데이터가 Notion에 자동 저장됩니다.</p>

  ${error ? `<div class="error">⚠️ ${error}</div>` : ''}

  <div class="steps">
    <h3>📋 설정 방법</h3>
    <div class="step">
      <div class="step-num">1</div>
      <div>Notion에서 <strong>빈 페이지</strong>를 하나 만드세요 (예: "교육 상담 관리")</div>
    </div>
    <div class="step">
      <div class="step-num">2</div>
      <div>그 페이지 오른쪽 상단 <strong>···</strong> 메뉴 → <strong>연결 추가 (Add connections)</strong> → 인테그레이션 선택</div>
    </div>
    <div class="step">
      <div class="step-num">3</div>
      <div>해당 페이지의 URL을 복사하여 아래에 붙여넣기</div>
    </div>
  </div>

  <form action="/setup" method="POST">
    <div class="form-group">
      <label>Notion 페이지 URL</label>
      <input type="text" name="pageUrl" placeholder="https://www.notion.so/..." required>
    </div>
    <button type="submit" class="btn">데이터베이스 생성 및 시작하기 →</button>
  </form>
</div>
</body>
</html>`;
}

function getFormPage() {
  return `<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>교육 상담 신청</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: 'Pretendard', 'Apple SD Gothic Neo', -apple-system, BlinkMacSystemFont, sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    min-height: 100vh;
    padding: 40px 20px;
  }

  .container {
    max-width: 680px;
    margin: 0 auto;
  }

  /* 헤더 */
  .header {
    text-align: center;
    margin-bottom: 32px;
    color: white;
  }
  .header .badge {
    display: inline-block;
    background: rgba(255,255,255,0.2);
    backdrop-filter: blur(10px);
    border: 1px solid rgba(255,255,255,0.3);
    padding: 6px 16px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 16px;
  }
  .header h1 {
    font-size: 36px;
    font-weight: 800;
    line-height: 1.2;
    margin-bottom: 12px;
    text-shadow: 0 2px 10px rgba(0,0,0,0.1);
  }
  .header p {
    font-size: 16px;
    opacity: 0.85;
    line-height: 1.6;
  }

  /* 카드 */
  .card {
    background: white;
    border-radius: 24px;
    padding: 48px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.15);
  }

  /* 섹션 */
  .section-title {
    font-size: 12px;
    font-weight: 700;
    color: #7b2ff7;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    margin-bottom: 20px;
    padding-bottom: 10px;
    border-bottom: 2px solid #f0f0f0;
  }

  .section { margin-bottom: 36px; }
  .section:last-of-type { margin-bottom: 0; }

  /* 폼 그리드 */
  .form-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  @media (max-width: 600px) {
    .card { padding: 28px 20px; }
    .form-row { grid-template-columns: 1fr; }
    .header h1 { font-size: 28px; }
  }

  .form-group { margin-bottom: 20px; }
  .form-group:last-child { margin-bottom: 0; }

  label {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 14px;
    font-weight: 600;
    color: #333;
    margin-bottom: 8px;
  }
  .required {
    color: #ef4444;
    font-size: 16px;
    line-height: 1;
  }

  input[type="text"],
  input[type="email"],
  input[type="tel"],
  select,
  textarea {
    width: 100%;
    padding: 14px 16px;
    border: 2px solid #e8e8e8;
    border-radius: 12px;
    font-size: 15px;
    font-family: inherit;
    color: #333;
    transition: all 0.2s;
    outline: none;
    appearance: none;
    background: #fafafa;
  }
  input:focus, select:focus, textarea:focus {
    border-color: #7b2ff7;
    background: white;
    box-shadow: 0 0 0 4px rgba(123,47,247,0.08);
  }
  input::placeholder, textarea::placeholder { color: #bbb; }

  select {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23888' d='M6 8L1 3h10z'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 16px center;
    padding-right: 44px;
    cursor: pointer;
  }

  textarea {
    resize: vertical;
    min-height: 120px;
    line-height: 1.6;
  }

  /* 개인정보 동의 */
  .privacy-box {
    background: #f8f9ff;
    border: 2px solid #e8eeff;
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 28px;
  }
  .privacy-box h4 {
    font-size: 14px;
    font-weight: 700;
    color: #333;
    margin-bottom: 10px;
  }
  .privacy-content {
    font-size: 13px;
    color: #666;
    line-height: 1.7;
    margin-bottom: 14px;
    max-height: 100px;
    overflow-y: auto;
  }
  .privacy-check {
    display: flex;
    align-items: center;
    gap: 10px;
    cursor: pointer;
  }
  .privacy-check input[type="checkbox"] {
    width: 18px;
    height: 18px;
    accent-color: #7b2ff7;
    cursor: pointer;
    flex-shrink: 0;
  }
  .privacy-check span {
    font-size: 14px;
    font-weight: 600;
    color: #333;
  }

  /* 제출 버튼 */
  .submit-btn {
    width: 100%;
    padding: 18px;
    background: linear-gradient(135deg, #667eea, #7b2ff7);
    color: white;
    border: none;
    border-radius: 14px;
    font-size: 16px;
    font-weight: 700;
    cursor: pointer;
    letter-spacing: 0.5px;
    transition: all 0.2s;
    box-shadow: 0 4px 20px rgba(123,47,247,0.3);
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
  }
  .submit-btn:hover { transform: translateY(-1px); box-shadow: 0 8px 30px rgba(123,47,247,0.4); }
  .submit-btn:active { transform: translateY(0); }
  .submit-btn:disabled { opacity: 0.6; cursor: not-allowed; transform: none; }

  /* 알림 */
  .alert {
    display: none;
    padding: 16px 20px;
    border-radius: 12px;
    font-size: 15px;
    font-weight: 600;
    margin-bottom: 24px;
    animation: slideIn 0.3s ease;
  }
  .alert.success { background: #f0fdf4; border: 2px solid #86efac; color: #16a34a; display: flex; align-items: center; gap: 10px; }
  .alert.error { background: #fef2f2; border: 2px solid #fca5a5; color: #dc2626; display: flex; align-items: center; gap: 10px; }
  @keyframes slideIn { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; transform: translateY(0); } }

  .spinner { display: none; width: 18px; height: 18px; border: 2px solid rgba(255,255,255,0.4); border-top-color: white; border-radius: 50%; animation: spin 0.7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* 하단 */
  .footer { text-align: center; margin-top: 24px; font-size: 13px; color: rgba(255,255,255,0.7); }
</style>
</head>
<body>

<div class="container">
  <!-- 헤더 -->
  <div class="header">
    <div class="badge">📚 Education Consulting</div>
    <h1>교육 상담 신청</h1>
    <p>전문 상담사가 빠른 시간 내에 연락드립니다.<br>아래 양식을 작성하여 신청해 주세요.</p>
  </div>

  <!-- 폼 카드 -->
  <div class="card">
    <div id="alert" class="alert"></div>

    <form id="consultForm" novalidate>

      <!-- 기본 정보 -->
      <div class="section">
        <div class="section-title">01 기본 정보</div>
        <div class="form-row">
          <div class="form-group">
            <label for="name">이름 <span class="required">*</span></label>
            <input type="text" id="name" name="name" placeholder="홍길동" required>
          </div>
          <div class="form-group">
            <label for="phone">연락처 <span class="required">*</span></label>
            <input type="tel" id="phone" name="phone" placeholder="010-0000-0000" required>
          </div>
        </div>
        <div class="form-group">
          <label for="email">이메일 <span class="required">*</span></label>
          <input type="email" id="email" name="email" placeholder="example@email.com" required>
        </div>
      </div>

      <!-- 상담 정보 -->
      <div class="section">
        <div class="section-title">02 상담 정보</div>
        <div class="form-row">
          <div class="form-group">
            <label for="subject">상담 분야</label>
            <select id="subject" name="subject">
              <option value="">선택해주세요</option>
              <option>입시 상담</option>
              <option>학습 방법</option>
              <option>진로 탐색</option>
              <option>수강 문의</option>
              <option>기타</option>
            </select>
          </div>
          <div class="form-group">
            <label for="time">희망 상담 시간</label>
            <select id="time" name="time">
              <option value="">선택해주세요</option>
              <option>평일 오전 (9시~12시)</option>
              <option>평일 오후 (13시~17시)</option>
              <option>평일 저녁 (18시~21시)</option>
              <option>주말 오전 (9시~12시)</option>
              <option>주말 오후 (13시~17시)</option>
            </select>
          </div>
        </div>
        <div class="form-group">
          <label for="message">문의 내용</label>
          <textarea id="message" name="message" placeholder="궁금하신 점이나 상담받고 싶은 내용을 자유롭게 작성해 주세요."></textarea>
        </div>
      </div>

      <!-- 개인정보 동의 -->
      <div class="privacy-box">
        <h4>개인정보 수집 및 이용 안내</h4>
        <div class="privacy-content">
          수집 항목: 이름, 연락처, 이메일 주소<br>
          수집 목적: 교육 상담 신청 접수 및 안내 연락<br>
          보유 기간: 상담 완료 후 1년<br>
          귀하는 개인정보 제공에 동의하지 않을 권리가 있습니다. 단, 동의하지 않을 경우 상담 신청이 제한될 수 있습니다.
        </div>
        <label class="privacy-check">
          <input type="checkbox" id="privacy" name="privacy">
          <span>개인정보 수집·이용에 동의합니다 <span style="color:#ef4444">*</span></span>
        </label>
      </div>

      <button type="submit" class="submit-btn" id="submitBtn">
        <span id="btnText">상담 신청하기</span>
        <span style="font-size:18px" id="btnIcon">→</span>
        <div class="spinner" id="spinner"></div>
      </button>

    </form>
  </div>

  <div class="footer">
    신청 후 1~2 영업일 내 담당자가 연락드립니다.
  </div>
</div>

<script>
  // 전화번호 자동 하이픈
  document.getElementById('phone').addEventListener('input', function(e) {
    let v = e.target.value.replace(/\D/g, '');
    if (v.length <= 3) e.target.value = v;
    else if (v.length <= 7) e.target.value = v.slice(0,3) + '-' + v.slice(3);
    else if (v.length <= 11) e.target.value = v.slice(0,3) + '-' + v.slice(3,7) + '-' + v.slice(7);
    else e.target.value = v.slice(0,3) + '-' + v.slice(3,7) + '-' + v.slice(7,11);
  });

  function showAlert(type, msg) {
    const el = document.getElementById('alert');
    el.className = 'alert ' + type;
    el.innerHTML = (type === 'success' ? '✅' : '⚠️') + ' ' + msg;
    el.style.display = 'flex';
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function setLoading(on) {
    document.getElementById('submitBtn').disabled = on;
    document.getElementById('btnText').style.display = on ? 'none' : '';
    document.getElementById('btnIcon').style.display = on ? 'none' : '';
    document.getElementById('spinner').style.display = on ? 'block' : 'none';
  }

  document.getElementById('consultForm').addEventListener('submit', async function(e) {
    e.preventDefault();

    const name = document.getElementById('name').value.trim();
    const phone = document.getElementById('phone').value.trim();
    const email = document.getElementById('email').value.trim();
    const privacy = document.getElementById('privacy').checked;

    if (!name) { showAlert('error', '이름을 입력해주세요.'); return; }
    if (!phone) { showAlert('error', '연락처를 입력해주세요.'); return; }
    if (!email || !email.includes('@')) { showAlert('error', '올바른 이메일을 입력해주세요.'); return; }
    if (!privacy) { showAlert('error', '개인정보 수집·이용에 동의해주세요.'); return; }

    document.getElementById('alert').style.display = 'none';
    setLoading(true);

    try {
      const formData = new FormData(this);
      const response = await fetch('/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.fromEntries(formData))
      });
      const result = await response.json();

      if (result.success) {
        showAlert('success', '상담 신청이 완료되었습니다! 1~2 영업일 내에 연락드리겠습니다.');
        this.reset();
      } else {
        showAlert('error', result.message || '오류가 발생했습니다. 다시 시도해주세요.');
      }
    } catch (err) {
      showAlert('error', '서버 연결에 실패했습니다. 잠시 후 다시 시도해주세요.');
    } finally {
      setLoading(false);
    }
  });
</script>
</body>
</html>`;
}

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  const config = loadConfig();
  console.log(`\n🚀 교육 상담 신청 서버가 시작되었습니다.`);
  console.log(`   주소: http://localhost:${PORT}`);
  if (!config.databaseId) {
    console.log(`\n⚠️  Notion 설정이 필요합니다.`);
    console.log(`   http://localhost:${PORT}/setup 에서 설정을 완료해주세요.`);
  } else {
    console.log(`\n✅ Notion DB 연결됨: ${config.databaseId}`);
  }
  console.log('');
});
