const CHECKER_LABELS = {
  "missing-section": "필수 항목이 빠졌어요",
  "length-violation": "분량 기준을 벗어났어요",
  "numeric-consistency": "숫자가 서로 맞지 않아요",
  "logic-gap": "논리 연결이 끊겨요",
  "unsupported-claim": "근거가 없는 주장이에요",
  "internal-contradiction": "문서 안에서 말이 엇갈려요",
  "vague-goal": "목표가 구체적이지 않아요",
};
const SEV_LABELS = { critical: "치명", warning: "주의", info: "참고" };
const SKIP_MESSAGES = {
  quota_ip: "오늘 AI 정밀 검사 횟수를 다 썼어요. 기본 검사 결과만 보여드려요 — 내일 다시 이용해주세요.",
  quota_global: "오늘 전체 AI 정밀 검사가 마감됐어요. 기본 검사 결과만 보여드려요 — 내일 다시 이용해주세요.",
  llm_error: "AI 정밀 검사 중 문제가 생겨 기본 검사 결과만 보여드려요.",
  quota_admin: "관리자 일일 상한을 다 썼어요. 기본 검사 결과만 보여드려요 — 내일 다시 이용해주세요.",
};

const $ = (id) => document.getElementById(id);
let selectedFile = null;
let lastResult = null;

// --- 관리자 모드: ?admin=<토큰>으로 한 번 접속하면 브라우저가 기억 ---
const ADMIN_STORAGE_KEY = "plw_admin_token";
(() => {
  const params = new URLSearchParams(location.search);
  const t = params.get("admin");
  if (t !== null) {
    if (t) localStorage.setItem(ADMIN_STORAGE_KEY, t);
    else localStorage.removeItem(ADMIN_STORAGE_KEY); // ?admin= (빈 값) → 해제
    history.replaceState(null, "", location.pathname); // 주소창에서 토큰 제거
  }
})();
function adminHeaders() {
  const t = localStorage.getItem(ADMIN_STORAGE_KEY);
  return t ? { "x-admin-token": t } : {};
}

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// 원문에서 인용 위치를 오프셋으로 먼저 수집한 뒤 한 번에 조립한다.
// 진행 중인 HTML 문자열에 재매칭하면 겹치는 인용이나 동일 문자열 인용이
// 조용히 유실되거나 중첩 mark가 생기기 때문 (원문 기준 매칭이 정답).
function highlightSource(text, findings) {
  const ranges = [];
  const taken = [];
  findings.forEach((f, idx) => {
    for (const q of f.quotes || []) {
      let from = 0;
      while (true) {
        const start = text.indexOf(q, from);
        if (start === -1) break; // 매칭 실패 시 카드만 표시 (스펙 §7)
        const end = start + q.length;
        const overlaps = taken.some(([s, e]) => start < e && end > s);
        if (!overlaps) {
          ranges.push({ start, end, sev: f.severity, idx });
          taken.push([start, end]);
          break;
        }
        from = start + 1; // 이미 하이라이트된 구간과 겹치면 다음 등장 위치 시도
      }
    }
  });
  ranges.sort((a, b) => a.start - b.start);
  let out = "";
  let pos = 0;
  for (const r of ranges) {
    out += esc(text.slice(pos, r.start));
    out += `<mark class="${esc(r.sev)}" data-idx="${r.idx}">` + esc(text.slice(r.start, r.end)) + "</mark>";
    pos = r.end;
  }
  out += esc(text.slice(pos));
  return out;
}

// --- 탭 (WAI-ARIA tabs 패턴: roving tabindex + 방향키) ---
$("tab-file").onclick = () => switchTab(true);
$("tab-text").onclick = () => switchTab(false);
function switchTab(isFile, moveFocus = false) {
  const fileTab = $("tab-file"), textTab = $("tab-text");
  for (const [tab, on] of [[fileTab, isFile], [textTab, !isFile]]) {
    tab.classList.toggle("active", on);
    tab.setAttribute("aria-selected", on ? "true" : "false");
    tab.tabIndex = on ? 0 : -1;   // 비활성 탭은 Tab 순회에서 빠진다
  }
  $("panel-file").hidden = !isFile;
  $("panel-text").hidden = isFile;
  if (moveFocus) (isFile ? fileTab : textTab).focus();
}
$("tab-file").parentElement.addEventListener("keydown", (e) => {
  const onFile = $("tab-file").getAttribute("aria-selected") === "true";
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
    e.preventDefault();
    switchTab(!onFile, true);
  } else if (e.key === "Home") {
    e.preventDefault();
    switchTab(true, true);
  } else if (e.key === "End") {
    e.preventDefault();
    switchTab(false, true);
  }
});

// --- 파일 선택/드롭 ---
const dz = $("dropzone");
dz.onclick = () => $("file-input").click();
dz.onkeydown = (e) => {  // 키보드 사용자도 Enter/Space로 파일 선택 가능 (a11y)
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    $("file-input").click();
  }
};
$("file-input").onchange = (e) => pickFile(e.target.files[0]);
dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("dragover"); };
dz.ondragleave = () => dz.classList.remove("dragover");
dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("dragover"); pickFile(e.dataTransfer.files[0]); };
function pickFile(f) {
  if (!f) return;
  if (f.name.toLowerCase().endsWith(".hwp")) {
    showError("구형 한글(.hwp) 파일이에요. 한글에서 \"다른 이름으로 저장 → HWPX\"로 저장한 뒤 다시 올려주세요. 어렵다면 텍스트 붙여넣기를 이용해주세요.");
    return;
  }
  selectedFile = f;
  $("file-name").textContent = "선택됨: " + f.name;
  hideError();
}

function showError(msg) { const b = $("error-box"); b.textContent = msg; b.hidden = false; }
function hideError() { $("error-box").hidden = true; }

// --- 쿼터 표시 ---
async function refreshQuota() {
  try {
    const r = await (await fetch("/api/quota", { headers: adminHeaders() })).json();
    $("quota-info").textContent = r.admin
      ? `관리자 모드 — 오늘 ${r.remaining_today}회 남음`
      : `(오늘 남은 횟수: ${r.remaining_today}회)`;
  } catch { /* 표시는 부가 기능 — 실패해도 무시 */ }
}
refreshQuota();

// --- 진단 요청 ---
$("submit").onclick = async () => {
  hideError();
  const fd = new FormData();
  const fileTab = $("tab-file").classList.contains("active");
  if (fileTab) {
    if (!selectedFile) { showError("파일을 먼저 선택해주세요."); return; }
    fd.append("file", selectedFile);
  } else {
    const t = $("text-input").value.trim();
    if (!t) { showError("텍스트를 붙여넣어주세요."); return; }
    fd.append("text", t);
  }
  fd.append("use_llm", $("use-llm").checked ? "true" : "false");

  $("loading").hidden = false;
  $("submit").disabled = true;
  try {
    const resp = await fetch("/api/lint", { method: "POST", body: fd, headers: adminHeaders() });
    const body = await resp.json();
    if (!resp.ok) {
      showError(body.error || "진단에 실패했어요. 잠시 후 다시 시도해주세요.");
      if (resp.status === 422 && fileTab) switchTab(false); // 변환 실패 → 붙여넣기로 유도
      return;
    }
    lastResult = body;
    renderReport(body);
  } catch {
    showError("서버에 연결하지 못했어요. 잠시 후 다시 시도해주세요.");
  } finally {
    $("loading").hidden = true;
    $("submit").disabled = false;
    refreshQuota();
  }
};

// --- 리포트 렌더 ---
function renderReport(body) {
  $("input-view").hidden = true;
  $("report-view").hidden = false;

  $("report-meta").textContent = body.meta.llm_ran
    ? "기본 검사 + AI 정밀 검사 완료 · 보강 제안 포함"
    : "기본 검사 완료";

  const banner = $("banner");
  const notes = [];
  if (body.meta.llm_skipped_reason) notes.push(SKIP_MESSAGES[body.meta.llm_skipped_reason]);
  for (const w of body.meta.conversion_warnings) notes.push(w);
  banner.hidden = notes.length === 0;
  banner.innerHTML = notes.length
    ? `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M12 8v5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="16.2" r="1" fill="currentColor"/></svg><span>${esc(notes.join(" · "))}</span>`
    : "";

  // 요약 배지
  const counts = { critical: 0, warning: 0, info: 0 };
  for (const f of body.findings) counts[f.severity] = (counts[f.severity] || 0) + 1;
  $("summary").innerHTML = body.findings.length === 0
    ? '<span class="badge clean">발견된 결함 없음</span>'
    : Object.entries(counts).filter(([, n]) => n > 0)
        .map(([sev, n]) => `<span class="badge ${esc(sev)}">${SEV_LABELS[sev]} ${n}</span>`).join("");

  // 원문 + 하이라이트: highlightSource 참고 (파일 상단 설명 주석)
  // .source-inner 래핑: 깎기 카드(§5-1) 안의 맨 텍스트 노드는 표면 아래로 깔린다
  $("source-pane").innerHTML =
    '<div class="source-inner">' + highlightSource(body.converted_text, body.findings) + "</div>";

  // 결함 카드 (0건이면 클린 상태 패널)
  const SUGGESTION_ICON =
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M9 18h6M10 21h4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><path d="M12 3a6 6 0 0 0-3.4 10.9c.7.5 1.1 1.3 1.2 2.1h4.4c.1-.8.5-1.6 1.2-2.1A6 6 0 0 0 12 3z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>';
  $("cards-pane").innerHTML = body.findings.length === 0
    ? `<div class="clean-state cut-card">
        <div class="clean-inner">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M8.5 12.2l2.4 2.4 4.6-4.9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
          <h3>이번 검사에서 결함을 찾지 못했어요</h3>
          <p>검사 항목 기준으로 문제가 없다는 뜻이며, 계획서의 완성도를 보장하는 평가는 아니에요.</p>
        </div>
      </div>`
    : body.findings.map((f, idx) => `
    <div class="card cut-card cut-card--sm ${esc(f.severity)}" data-idx="${idx}"
         role="button" tabindex="0"
         aria-label="${esc(SEV_LABELS[f.severity])} — ${esc(CHECKER_LABELS[f.checker] || f.checker)}. 원문 위치로 이동">
      <div class="card-inner">
        <div class="card-head">
          <span class="sev">${SEV_LABELS[f.severity]}</span>
          <h3>${esc(CHECKER_LABELS[f.checker] || f.checker)}</h3>
        </div>
        <p>${esc(f.message)}</p>
        ${(f.quotes || []).map((q) => `<blockquote>${esc(q)}</blockquote>`).join("")}
        ${f.suggestion ? `<div class="suggestion"><span class="suggestion-label">${SUGGESTION_ICON}보강 제안</span><span class="suggestion-body">${esc(f.suggestion)}</span></div>` : ""}
      </div>
    </div>`).join("");

  // 카드 ↔ 하이라이트 상호 스크롤 (카드는 키보드로도 조작 가능)
  document.querySelectorAll(".card").forEach((card) => {
    const go = () => focusMark("#source-pane mark", card.dataset.idx);
    card.onclick = go;
    card.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
    };
  });
  document.querySelectorAll("#source-pane mark").forEach((m) => {
    m.onclick = () => focusMark("#cards-pane .card", m.dataset.idx);
  });

  // 화면이 바뀌었음을 스크린리더에 알리고 읽기 시작점을 제목으로 옮긴다
  $("report-heading").focus();
}

function focusMark(selector, idx) {
  const el = document.querySelector(`${selector}[data-idx="${idx}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  document.querySelectorAll(".focused").forEach((x) => x.classList.remove("focused"));
  el.classList.add("focused");
}

// --- 결과 복사 (마크다운) ---
$("copy-btn").onclick = async () => {
  if (!lastResult) return;
  const lines = ["# plan-lint 진단 결과", ""];
  for (const f of lastResult.findings) {
    lines.push(`## [${SEV_LABELS[f.severity]}] ${CHECKER_LABELS[f.checker] || f.checker}`);
    lines.push(f.message);
    for (const q of f.quotes || []) lines.push(`> ${q}`);
    if (f.suggestion) lines.push(`제안: ${f.suggestion}`);
    lines.push("");
  }
  const text = lines.join("\n");
  const btn = $("copy-btn");
  const original = btn.innerHTML; // SVG 아이콘 보존을 위해 innerHTML로 복원
  try {
    // 완료를 기다린 뒤에만 성공을 표시한다 — 권한 거부·비보안 컨텍스트에서
    // 거짓 성공을 띄우면 사용자가 붙여넣기에 실패하고서야 알게 된다
    if (!navigator.clipboard) throw new Error("clipboard unavailable");
    await navigator.clipboard.writeText(text);
    btn.textContent = "복사됐어요!";
    setTimeout(() => (btn.innerHTML = original), 1500);
  } catch {
    btn.textContent = "복사 실패";
    setTimeout(() => (btn.innerHTML = original), 1500);
    showCopyFallback(text);
  }
};

function showCopyFallback(text) {
  // 자동 복사가 막힌 환경 — 직접 선택해 복사할 수 있게 원문을 펼쳐 보여준다
  let box = $("copy-fallback");
  if (!box) {
    box = document.createElement("div");
    box.id = "copy-fallback";
    box.className = "copy-fallback";
    box.innerHTML =
      '<p>자동 복사가 차단됐어요. 아래 내용을 선택해 복사해주세요 (Ctrl+A → Ctrl+C).</p>' +
      '<textarea readonly rows="10" aria-label="진단 결과 원문"></textarea>';
    $("report-view").appendChild(box);
  }
  const area = box.querySelector("textarea");
  area.value = text;
  box.hidden = false;
  area.focus();
  area.select();
}

$("again-btn").onclick = () => {
  $("report-view").hidden = true;
  $("input-view").hidden = false;
};
