const CHECKER_LABELS = {
  "missing-section": "필수 항목이 빠졌어요",
  "length-violation": "분량 기준을 벗어났어요",
  "numeric-consistency": "숫자가 서로 맞지 않아요",
  "logic-gap": "논리 연결이 끊겨요",
  "unsupported-claim": "근거가 없는 주장이에요",
  "internal-contradiction": "문서 안에서 말이 엇갈려요",
};
const SEV_LABELS = { critical: "치명", warning: "주의", info: "참고" };
const SKIP_MESSAGES = {
  quota_ip: "오늘 AI 정밀 검사 횟수를 다 썼어요. 기본 검사 결과만 보여드려요 — 내일 다시 이용해주세요.",
  quota_global: "오늘 전체 AI 정밀 검사가 마감됐어요. 기본 검사 결과만 보여드려요 — 내일 다시 이용해주세요.",
  llm_error: "AI 정밀 검사 중 문제가 생겨 기본 검사 결과만 보여드려요. 사용 횟수는 차감되지 않았어요.",
};

const $ = (id) => document.getElementById(id);
let selectedFile = null;
let lastResult = null;

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

// --- 탭 ---
$("tab-file").onclick = () => switchTab(true);
$("tab-text").onclick = () => switchTab(false);
function switchTab(isFile) {
  $("tab-file").classList.toggle("active", isFile);
  $("tab-text").classList.toggle("active", !isFile);
  $("panel-file").hidden = !isFile;
  $("panel-text").hidden = isFile;
}

// --- 파일 선택/드롭 ---
const dz = $("dropzone");
dz.onclick = () => $("file-input").click();
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
    const r = await (await fetch("/api/quota")).json();
    $("quota-info").textContent = `(오늘 남은 횟수: ${r.remaining_today}회)`;
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
    const resp = await fetch("/api/lint", { method: "POST", body: fd });
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

  const banner = $("banner");
  const notes = [];
  if (body.meta.llm_skipped_reason) notes.push(SKIP_MESSAGES[body.meta.llm_skipped_reason]);
  for (const w of body.meta.conversion_warnings) notes.push(w);
  banner.hidden = notes.length === 0;
  banner.textContent = notes.join(" · ");

  // 요약 배지
  const counts = { critical: 0, warning: 0, info: 0 };
  for (const f of body.findings) counts[f.severity] = (counts[f.severity] || 0) + 1;
  $("summary").innerHTML = body.findings.length === 0
    ? '<span class="badge clean">발견된 결함이 없어요</span>'
    : Object.entries(counts).filter(([, n]) => n > 0)
        .map(([sev, n]) => `<span class="badge ${esc(sev)}">${SEV_LABELS[sev]} ${n}</span>`).join("");

  // 원문 + 하이라이트: highlightSource 참고 (파일 상단 설명 주석)
  $("source-pane").innerHTML = highlightSource(body.converted_text, body.findings);

  // 결함 카드
  $("cards-pane").innerHTML = body.findings.map((f, idx) => `
    <div class="card ${esc(f.severity)}" data-idx="${idx}">
      <span class="sev">${SEV_LABELS[f.severity]}</span>
      <h3>${esc(CHECKER_LABELS[f.checker] || f.checker)}</h3>
      <p>${esc(f.message)}</p>
      ${(f.quotes || []).map((q) => `<blockquote>${esc(q)}</blockquote>`).join("")}
      ${f.suggestion ? `<p class="suggestion">💡 ${esc(f.suggestion)}</p>` : ""}
    </div>`).join("");

  // 카드 ↔ 하이라이트 상호 스크롤
  document.querySelectorAll(".card").forEach((card) => {
    card.onclick = () => focusMark("#source-pane mark", card.dataset.idx);
  });
  document.querySelectorAll("#source-pane mark").forEach((m) => {
    m.onclick = () => focusMark("#cards-pane .card", m.dataset.idx);
  });
}

function focusMark(selector, idx) {
  const el = document.querySelector(`${selector}[data-idx="${idx}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  document.querySelectorAll(".focused").forEach((x) => x.classList.remove("focused"));
  el.classList.add("focused");
}

// --- 결과 복사 (마크다운) ---
$("copy-btn").onclick = () => {
  if (!lastResult) return;
  const lines = ["# plan-lint 진단 결과", ""];
  for (const f of lastResult.findings) {
    lines.push(`## [${SEV_LABELS[f.severity]}] ${CHECKER_LABELS[f.checker] || f.checker}`);
    lines.push(f.message);
    for (const q of f.quotes || []) lines.push(`> ${q}`);
    if (f.suggestion) lines.push(`제안: ${f.suggestion}`);
    lines.push("");
  }
  navigator.clipboard.writeText(lines.join("\n"));
  $("copy-btn").textContent = "복사됐어요!";
  setTimeout(() => ($("copy-btn").textContent = "결과 복사"), 1500);
};

$("again-btn").onclick = () => {
  $("report-view").hidden = true;
  $("input-view").hidden = false;
};
