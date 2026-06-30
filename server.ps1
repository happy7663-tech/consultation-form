# 교육 상담 신청 서버 (PowerShell 내장 - 별도 설치 불필요)
# 실행: powershell -ExecutionPolicy Bypass -File server.ps1

$PORT = 3000
$NOTION_TOKEN = "ntn_v665215908877AbvamFl83HijgGZlsKc1mqfCpVgEK01M5"
$NOTION_VERSION = "2022-06-28"
$CONFIG_FILE = Join-Path $PSScriptRoot "config.json"

# ── 설정 파일 로드/저장 ──────────────────────────────────────────
function Load-Config {
    if (Test-Path $CONFIG_FILE) {
        try { return (Get-Content $CONFIG_FILE -Raw | ConvertFrom-Json) }
        catch { }
    }
    return [PSCustomObject]@{ databaseId = $null }
}

function Save-Config($data) {
    $data | ConvertTo-Json | Set-Content $CONFIG_FILE -Encoding UTF8
}

# ── Notion API 호출 ──────────────────────────────────────────────
function Invoke-NotionAPI($method, $endpoint, $body) {
    $headers = @{
        "Authorization"  = "Bearer $NOTION_TOKEN"
        "Notion-Version" = $NOTION_VERSION
        "Content-Type"   = "application/json"
    }
    $uri = "https://api.notion.com/v1/$endpoint"
    try {
        $bodyJson = if ($body) { $body | ConvertTo-Json -Depth 10 } else { $null }
        $response = Invoke-RestMethod -Uri $uri -Method $method -Headers $headers -Body $bodyJson -ErrorVariable restErr
        return @{ success = $true; data = $response }
    } catch {
        $errMsg = $_.ErrorDetails.Message
        try { $errObj = $errMsg | ConvertFrom-Json; return @{ success = $false; message = $errObj.message } }
        catch { return @{ success = $false; message = $errMsg } }
    }
}

# ── Notion 데이터베이스 생성 ─────────────────────────────────────
function Create-NotionDatabase($parentPageId) {
    $body = @{
        parent = @{ type = "page_id"; page_id = $parentPageId }
        icon   = @{ type = "emoji"; emoji = "📋" }
        title  = @(@{ type = "text"; text = @{ content = "교육 상담 신청 목록" } })
        properties = @{
            "이름"         = @{ title = @{} }
            "연락처"       = @{ phone_number = @{} }
            "이메일"       = @{ email = @{} }
            "상담 분야"    = @{
                select = @{
                    options = @(
                        @{ name = "입시 상담"; color = "blue" }
                        @{ name = "학습 방법"; color = "green" }
                        @{ name = "진로 탐색"; color = "yellow" }
                        @{ name = "수강 문의"; color = "orange" }
                        @{ name = "기타";      color = "gray" }
                    )
                }
            }
            "희망 상담 시간" = @{
                select = @{
                    options = @(
                        @{ name = "평일 오전 (9시~12시)";  color = "pink" }
                        @{ name = "평일 오후 (13시~17시)"; color = "purple" }
                        @{ name = "평일 저녁 (18시~21시)"; color = "red" }
                        @{ name = "주말 오전 (9시~12시)";  color = "blue" }
                        @{ name = "주말 오후 (13시~17시)"; color = "green" }
                    )
                }
            }
            "문의 내용"    = @{ rich_text = @{} }
            "개인정보 동의" = @{
                select = @{
                    options = @(
                        @{ name = "동의";   color = "green" }
                        @{ name = "미동의"; color = "red" }
                    )
                }
            }
            "신청 상태"    = @{
                select = @{
                    options = @(
                        @{ name = "신규 접수"; color = "blue" }
                        @{ name = "확인 중";   color = "yellow" }
                        @{ name = "상담 완료"; color = "green" }
                        @{ name = "취소";      color = "gray" }
                    )
                }
            }
            "신청일시"     = @{ created_time = @{} }
        }
    }
    return Invoke-NotionAPI "POST" "databases" $body
}

# ── Notion 페이지(신청 항목) 추가 ────────────────────────────────
function Add-Consultation($databaseId, $form) {
    $props = [ordered]@{
        "이름"    = @{ title     = @(@{ text = @{ content = "$($form.name)" } }) }
        "연락처"  = @{ phone_number = "$($form.phone)" }
        "이메일"  = @{ email     = "$($form.email)" }
        "문의 내용" = @{ rich_text = @(@{ text = @{ content = "$($form.message)" } }) }
        "개인정보 동의" = @{ select = @{ name = if ($form.privacy -eq "on") { "동의" } else { "미동의" } } }
        "신청 상태" = @{ select = @{ name = "신규 접수" } }
    }
    if ($form.subject) { $props["상담 분야"]      = @{ select = @{ name = "$($form.subject)" } } }
    if ($form.time)    { $props["희망 상담 시간"] = @{ select = @{ name = "$($form.time)" } } }

    $body = @{ parent = @{ database_id = $databaseId }; properties = $props }
    return Invoke-NotionAPI "POST" "pages" $body
}

# ── URL 파라미터 파싱 ─────────────────────────────────────────────
function Parse-QueryString($qs) {
    $result = @{}
    if ($qs) {
        foreach ($pair in $qs.TrimStart('?').Split('&')) {
            $parts = $pair.Split('=', 2)
            if ($parts.Length -eq 2) {
                $result[[Uri]::UnescapeDataString($parts[0])] = [Uri]::UnescapeDataString($parts[1])
            }
        }
    }
    return $result
}

function Parse-FormBody($body) {
    $result = @{}
    foreach ($pair in $body.Split('&')) {
        $parts = $pair.Split('=', 2)
        if ($parts.Length -eq 2) {
            $result[[Uri]::UnescapeDataString($parts[0].Replace('+', ' '))] = [Uri]::UnescapeDataString($parts[1].Replace('+', ' '))
        }
    }
    return $result
}

# ── HTML 템플릿 ───────────────────────────────────────────────────
function Get-SetupPage($error) {
    $errorHtml = if ($error) { "<div class='error'>⚠️ $error</div>" } else { "" }
    return @"
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>초기 설정 | 교육 상담 신청</title>
<style>
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif; background:#f0f4ff; min-height:100vh; display:flex; align-items:center; justify-content:center; padding:20px; }
.card { background:white; border-radius:20px; padding:48px; max-width:600px; width:100%; box-shadow:0 4px 40px rgba(0,0,0,.08); }
.icon { font-size:48px; margin-bottom:16px; }
h1 { font-size:24px; font-weight:700; color:#1a1a2e; margin-bottom:8px; }
.subtitle { color:#666; font-size:15px; line-height:1.6; margin-bottom:32px; }
.steps { background:#f8f9ff; border-radius:12px; padding:24px; margin-bottom:28px; }
.steps h3 { font-size:13px; font-weight:700; color:#4361ee; margin-bottom:14px; text-transform:uppercase; letter-spacing:.5px; }
.step { display:flex; gap:12px; margin-bottom:12px; font-size:14px; color:#444; line-height:1.5; }
.step:last-child { margin-bottom:0; }
.step-num { width:22px; height:22px; background:#4361ee; color:white; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:11px; font-weight:700; flex-shrink:0; margin-top:1px; }
.form-group { margin-bottom:16px; }
label { display:block; font-size:14px; font-weight:600; color:#333; margin-bottom:8px; }
input[type=text] { width:100%; padding:14px 16px; border:2px solid #e0e0e0; border-radius:10px; font-size:14px; transition:border-color .2s; outline:none; font-family:inherit; }
input[type=text]:focus { border-color:#4361ee; }
.btn { width:100%; padding:16px; background:linear-gradient(135deg,#4361ee,#7b2ff7); color:white; border:none; border-radius:12px; font-size:16px; font-weight:700; cursor:pointer; transition:opacity .2s; font-family:inherit; }
.btn:hover { opacity:.9; }
.error { background:#fff0f0; border:1px solid #ffcccc; color:#c00; border-radius:10px; padding:12px 16px; font-size:14px; margin-bottom:20px; }
</style>
</head>
<body>
<div class="card">
  <div class="icon">⚙️</div>
  <h1>Notion 연동 설정</h1>
  <p class="subtitle">최초 1회 설정 후 상담 신청 데이터가 Notion에 자동 저장됩니다.</p>
  $errorHtml
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
      <div>해당 페이지의 <strong>URL을 복사</strong>하여 아래에 붙여넣기</div>
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
</html>
"@
}

function Get-FormPage {
    return @'
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>교육 상담 신청</title>
<style>
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:'Apple SD Gothic Neo','Malgun Gothic',-apple-system,sans-serif; background:linear-gradient(135deg,#667eea 0%,#764ba2 100%); min-height:100vh; padding:40px 20px; }
.container { max-width:680px; margin:0 auto; }
.header { text-align:center; margin-bottom:32px; color:white; }
.badge { display:inline-block; background:rgba(255,255,255,.2); backdrop-filter:blur(10px); border:1px solid rgba(255,255,255,.3); padding:6px 16px; border-radius:20px; font-size:13px; font-weight:600; letter-spacing:1px; text-transform:uppercase; margin-bottom:16px; }
.header h1 { font-size:36px; font-weight:800; line-height:1.2; margin-bottom:12px; text-shadow:0 2px 10px rgba(0,0,0,.1); }
.header p { font-size:16px; opacity:.85; line-height:1.6; }
.card { background:white; border-radius:24px; padding:48px; box-shadow:0 20px 60px rgba(0,0,0,.15); }
.section-title { font-size:12px; font-weight:700; color:#7b2ff7; text-transform:uppercase; letter-spacing:1.5px; margin-bottom:20px; padding-bottom:10px; border-bottom:2px solid #f0f0f0; }
.section { margin-bottom:36px; }
.form-row { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media(max-width:600px){.card{padding:28px 20px;}.form-row{grid-template-columns:1fr;}.header h1{font-size:28px;}}
.form-group { margin-bottom:20px; }
.form-group:last-child { margin-bottom:0; }
label { display:flex; align-items:center; gap:4px; font-size:14px; font-weight:600; color:#333; margin-bottom:8px; }
.req { color:#ef4444; font-size:16px; line-height:1; }
input[type=text],input[type=email],input[type=tel],select,textarea { width:100%; padding:14px 16px; border:2px solid #e8e8e8; border-radius:12px; font-size:15px; font-family:inherit; color:#333; transition:all .2s; outline:none; -webkit-appearance:none; appearance:none; background:#fafafa; }
input:focus,select:focus,textarea:focus { border-color:#7b2ff7; background:white; box-shadow:0 0 0 4px rgba(123,47,247,.08); }
input::placeholder,textarea::placeholder { color:#bbb; }
select { background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23888' d='M6 8L1 3h10z'/%3E%3C/svg%3E"); background-repeat:no-repeat; background-position:right 16px center; padding-right:44px; cursor:pointer; }
textarea { resize:vertical; min-height:120px; line-height:1.6; }
.privacy-box { background:#f8f9ff; border:2px solid #e8eeff; border-radius:12px; padding:20px; margin-bottom:28px; }
.privacy-box h4 { font-size:14px; font-weight:700; color:#333; margin-bottom:10px; }
.privacy-content { font-size:13px; color:#666; line-height:1.7; margin-bottom:14px; max-height:100px; overflow-y:auto; }
.privacy-check { display:flex; align-items:center; gap:10px; cursor:pointer; }
.privacy-check input[type=checkbox] { width:18px; height:18px; accent-color:#7b2ff7; cursor:pointer; flex-shrink:0; }
.privacy-check span { font-size:14px; font-weight:600; color:#333; }
.submit-btn { width:100%; padding:18px; background:linear-gradient(135deg,#667eea,#7b2ff7); color:white; border:none; border-radius:14px; font-size:16px; font-weight:700; cursor:pointer; letter-spacing:.5px; transition:all .2s; box-shadow:0 4px 20px rgba(123,47,247,.3); display:flex; align-items:center; justify-content:center; gap:8px; font-family:inherit; }
.submit-btn:hover { transform:translateY(-1px); box-shadow:0 8px 30px rgba(123,47,247,.4); }
.submit-btn:disabled { opacity:.6; cursor:not-allowed; transform:none; }
.alert { display:none; padding:16px 20px; border-radius:12px; font-size:15px; font-weight:600; margin-bottom:24px; }
.alert.success { background:#f0fdf4; border:2px solid #86efac; color:#16a34a; }
.alert.error { background:#fef2f2; border:2px solid #fca5a5; color:#dc2626; }
.spinner { display:none; width:18px; height:18px; border:2px solid rgba(255,255,255,.4); border-top-color:white; border-radius:50%; animation:spin .7s linear infinite; }
@keyframes spin{to{transform:rotate(360deg);}}
.footer { text-align:center; margin-top:24px; font-size:13px; color:rgba(255,255,255,.7); }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="badge">📚 Education Consulting</div>
    <h1>교육 상담 신청</h1>
    <p>전문 상담사가 빠른 시간 내에 연락드립니다.<br>아래 양식을 작성하여 신청해 주세요.</p>
  </div>

  <div class="card">
    <div id="alert" class="alert"></div>
    <form id="consultForm" novalidate>

      <div class="section">
        <div class="section-title">01 기본 정보</div>
        <div class="form-row">
          <div class="form-group">
            <label>이름 <span class="req">*</span></label>
            <input type="text" id="name" name="name" placeholder="홍길동" required>
          </div>
          <div class="form-group">
            <label>연락처 <span class="req">*</span></label>
            <input type="tel" id="phone" name="phone" placeholder="010-0000-0000" required>
          </div>
        </div>
        <div class="form-group">
          <label>이메일 <span class="req">*</span></label>
          <input type="email" id="email" name="email" placeholder="example@email.com" required>
        </div>
      </div>

      <div class="section">
        <div class="section-title">02 상담 정보</div>
        <div class="form-row">
          <div class="form-group">
            <label>상담 분야</label>
            <select name="subject">
              <option value="">선택해주세요</option>
              <option>입시 상담</option>
              <option>학습 방법</option>
              <option>진로 탐색</option>
              <option>수강 문의</option>
              <option>기타</option>
            </select>
          </div>
          <div class="form-group">
            <label>희망 상담 시간</label>
            <select name="time">
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
          <label>문의 내용</label>
          <textarea name="message" placeholder="궁금하신 점이나 상담받고 싶은 내용을 자유롭게 작성해 주세요."></textarea>
        </div>
      </div>

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
        <span id="btnIcon" style="font-size:18px">→</span>
        <div class="spinner" id="spinner"></div>
      </button>
    </form>
  </div>
  <div class="footer">신청 후 1~2 영업일 내 담당자가 연락드립니다.</div>
</div>

<script>
document.getElementById('phone').addEventListener('input', function(e) {
  let v = e.target.value.replace(/\D/g,'');
  if(v.length<=3) e.target.value=v;
  else if(v.length<=7) e.target.value=v.slice(0,3)+'-'+v.slice(3);
  else if(v.length<=11) e.target.value=v.slice(0,3)+'-'+v.slice(3,7)+'-'+v.slice(7);
  else e.target.value=v.slice(0,3)+'-'+v.slice(3,7)+'-'+v.slice(7,11);
});

function showAlert(type, msg) {
  const el = document.getElementById('alert');
  el.className='alert '+type;
  el.innerHTML=(type==='success'?'✅ ':'⚠️ ')+msg;
  el.style.display='block';
  el.scrollIntoView({behavior:'smooth',block:'nearest'});
}

function setLoading(on) {
  document.getElementById('submitBtn').disabled=on;
  document.getElementById('btnText').style.display=on?'none':'';
  document.getElementById('btnIcon').style.display=on?'none':'';
  document.getElementById('spinner').style.display=on?'block':'none';
}

document.getElementById('consultForm').addEventListener('submit', async function(e) {
  e.preventDefault();
  const name=document.getElementById('name').value.trim();
  const phone=document.getElementById('phone').value.trim();
  const email=document.getElementById('email').value.trim();
  const privacy=document.getElementById('privacy').checked;
  if(!name){showAlert('error','이름을 입력해주세요.');return;}
  if(!phone){showAlert('error','연락처를 입력해주세요.');return;}
  if(!email||!email.includes('@')){showAlert('error','올바른 이메일을 입력해주세요.');return;}
  if(!privacy){showAlert('error','개인정보 수집·이용에 동의해주세요.');return;}

  document.getElementById('alert').style.display='none';
  setLoading(true);
  try {
    const formData = new URLSearchParams(new FormData(this)).toString();
    const res = await fetch('/submit', {
      method:'POST',
      headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body: formData
    });
    const result = await res.json();
    if(result.success){
      showAlert('success','상담 신청이 완료되었습니다! 1~2 영업일 내에 연락드리겠습니다.');
      this.reset();
    } else {
      showAlert('error', result.message||'오류가 발생했습니다. 다시 시도해주세요.');
    }
  } catch(err) {
    showAlert('error','서버 연결에 실패했습니다. 잠시 후 다시 시도해주세요.');
  } finally {
    setLoading(false);
  }
});
</script>
</body>
</html>
'@
}

# ── HTTP 서버 시작 ─────────────────────────────────────────────────
$http = [System.Net.HttpListener]::new()
$http.Prefixes.Add("http://localhost:$PORT/")
$http.Start()

Write-Host ""
Write-Host "🚀 교육 상담 신청 서버가 시작되었습니다." -ForegroundColor Green
Write-Host "   주소: http://localhost:$PORT" -ForegroundColor Cyan

$config = Load-Config
if (-not $config.databaseId) {
    Write-Host ""
    Write-Host "⚠️  Notion 설정이 필요합니다." -ForegroundColor Yellow
    Write-Host "   http://localhost:$PORT/setup 에서 설정해주세요." -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "✅ Notion DB 연결됨: $($config.databaseId)" -ForegroundColor Green
}
Write-Host ""
Write-Host "서버 종료: Ctrl+C" -ForegroundColor Gray
Write-Host ""

# 브라우저 자동 열기
Start-Process "http://localhost:$PORT"

while ($http.IsListening) {
    $ctx = $http.GetContext()
    $req = $ctx.Request
    $res = $ctx.Response
    $res.ContentType = "text/html; charset=utf-8"

    $path   = $req.Url.AbsolutePath
    $method = $req.HttpMethod
    $config = Load-Config

    try {
        # GET /setup
        if ($method -eq "GET" -and $path -eq "/setup") {
            $qs    = Parse-QueryString $req.Url.Query
            $html  = Get-SetupPage $qs["error"]
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($html)
            $res.OutputStream.Write($bytes, 0, $bytes.Length)
        }
        # POST /setup
        elseif ($method -eq "POST" -and $path -eq "/setup") {
            $reader  = [System.IO.StreamReader]::new($req.InputStream)
            $body    = $reader.ReadToEnd()
            $form    = Parse-FormBody $body

            $pageUrl = $form["pageUrl"]
            if (-not $pageUrl) {
                $res.Redirect("/setup?error=URL을 입력해주세요")
            } else {
                # page ID 추출
                $match = [regex]::Match($pageUrl, '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})|([0-9a-f]{32})', 'IgnoreCase')
                if (-not $match.Success) {
                    $res.Redirect("/setup?error=올바른 Notion 페이지 URL이 아닙니다")
                } else {
                    $rawId = $match.Value -replace '-',''
                    $pageId = "$($rawId.Substring(0,8))-$($rawId.Substring(8,4))-$($rawId.Substring(12,4))-$($rawId.Substring(16,4))-$($rawId.Substring(20))"

                    $result = Create-NotionDatabase $pageId
                    if (-not $result.success) {
                        $msg = [Uri]::EscapeDataString($result.message -replace '"','')
                        $res.Redirect("/setup?error=$msg")
                    } else {
                        Save-Config @{ databaseId = $result.data.id }
                        Write-Host "✅ Notion DB 생성 완료: $($result.data.id)" -ForegroundColor Green
                        $res.Redirect("/")
                    }
                }
            }
        }
        # GET /
        elseif ($method -eq "GET" -and ($path -eq "/" -or $path -eq "")) {
            if (-not $config.databaseId) {
                $res.Redirect("/setup")
            } else {
                $html  = Get-FormPage
                $bytes = [System.Text.Encoding]::UTF8.GetBytes($html)
                $res.OutputStream.Write($bytes, 0, $bytes.Length)
            }
        }
        # POST /submit
        elseif ($method -eq "POST" -and $path -eq "/submit") {
            $res.ContentType = "application/json; charset=utf-8"
            if (-not $config.databaseId) {
                $json = '{"success":false,"message":"설정이 필요합니다."}'
            } else {
                $reader = [System.IO.StreamReader]::new($req.InputStream)
                $body   = $reader.ReadToEnd()
                $form   = Parse-FormBody $body

                if (-not $form["name"] -or -not $form["phone"] -or -not $form["email"]) {
                    $json = '{"success":false,"message":"필수 항목을 모두 입력해주세요."}'
                } elseif ($form["privacy"] -ne "on") {
                    $json = '{"success":false,"message":"개인정보 수집·이용에 동의해주세요."}'
                } else {
                    $result = Add-Consultation $config.databaseId $form
                    if ($result.success) {
                        Write-Host "📝 신청 접수: $($form['name']) ($($form['phone']))" -ForegroundColor Cyan
                        $json = '{"success":true,"message":"상담 신청이 완료되었습니다!"}'
                    } else {
                        $errMsg = ($result.message -replace '"','\"') -replace "'","'"
                        $json = "{`"success`":false,`"message`":`"$errMsg`"}"
                    }
                }
            }
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
            $res.OutputStream.Write($bytes, 0, $bytes.Length)
        }
        # 404
        else {
            $res.StatusCode = 404
            $bytes = [System.Text.Encoding]::UTF8.GetBytes("<h1>404 Not Found</h1>")
            $res.OutputStream.Write($bytes, 0, $bytes.Length)
        }
    } catch {
        Write-Host "오류: $_" -ForegroundColor Red
        try {
            $res.StatusCode = 500
            $bytes = [System.Text.Encoding]::UTF8.GetBytes("Internal Server Error")
            $res.OutputStream.Write($bytes, 0, $bytes.Length)
        } catch {}
    } finally {
        try { $res.OutputStream.Close() } catch {}
    }
}
