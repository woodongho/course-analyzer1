let activeCharts = [];
let latestReportText = "";
let latestReportMode = "B";
let reportViewMode = "preview"; // "preview" | "edit"

const bannerEl = document.getElementById("status-banner");
const authSectionEl = document.getElementById("section-auth");
const workspaceEl = document.getElementById("workspace-area");
const passwordInputEl = document.getElementById("password-input");
const btnLogin = document.getElementById("btn-login");
const btnLogout = document.getElementById("btn-logout");

const fileInputEl = document.getElementById("file-input");
const btnUpload = document.getElementById("btn-upload");
const questionInputEl = document.getElementById("question-input");
const btnAsk = document.getElementById("btn-ask");
const btnSuggest = document.getElementById("btn-suggest");
const btnRun = document.getElementById("btn-run");
const btnExplain = document.getElementById("btn-explain");
const btnCopy = document.getElementById("btn-copy");
const btnDownload = document.getElementById("btn-download");

const reportTabsEl = document.getElementById("report-tabs");
const btnTabPreview = document.getElementById("btn-tab-preview");
const btnTabEdit = document.getElementById("btn-tab-edit");
const reportPreviewEl = document.getElementById("report-preview");
const reportEditorEl = document.getElementById("report-editor");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function showBanner(message, type = "info") {
  bannerEl.textContent = message;
  bannerEl.className = `banner ${type}`;
}

function setBusy(isBusy, loadingMessage = "처리 중...") {
  [btnLogin, btnUpload, btnAsk, btnSuggest, btnRun, btnExplain].forEach((btn) => {
    if (btn) btn.disabled = isBusy;
  });
  if (isBusy) {
    showBanner(loadingMessage, "loading");
  }
}

function setAuthenticatedUI(isAuth) {
  if (isAuth) {
    authSectionEl.classList.add("hidden");
    workspaceEl.classList.remove("hidden");
    btnLogout.classList.remove("hidden");
  } else {
    authSectionEl.classList.remove("hidden");
    workspaceEl.classList.add("hidden");
    btnLogout.classList.add("hidden");
  }
}

async function checkAuthStatus() {
  try {
    const res = await fetch("/auth/status");
    const data = await res.json();
    console.log("[인증] 상태 확인:", data);
    if (!data.password_configured) {
      setAuthenticatedUI(false);
      showBanner(".env 파일에 APP_PASSWORD를 8자리 이상으로 설정한 뒤 서버를 다시 실행해 주세요.", "error");
      return;
    }
    setAuthenticatedUI(Boolean(data.authenticated));
  } catch (err) {
    console.error("[인증] 상태 확인 오류:", err);
  }
}

async function handleLogin() {
  const pw = (passwordInputEl.value || "").trim();
  if (pw.length < 8) {
    showBanner("비밀번호는 8자리 이상 입력해야 합니다.", "error");
    return;
  }
  setBusy(true, "처리 중... 비밀번호를 확인하고 있습니다.");
  try {
    const res = await fetch("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "로그인에 실패했습니다.");
    }
    passwordInputEl.value = "";
    setAuthenticatedUI(true);
    showBanner("잠금이 해제되었습니다. 엑셀(.xls) 파일을 올려 주세요.", "info");
    console.log("[인증] 잠금 해제 완료");
  } catch (err) {
    console.error("[인증] 로그인 오류:", err);
    showBanner(err.message, "error");
  } finally {
    setBusy(false);
  }
}

btnLogin.addEventListener("click", handleLogin);
passwordInputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") handleLogin();
});

btnLogout.addEventListener("click", async () => {
  await fetch("/logout", { method: "POST" });
  setAuthenticatedUI(false);
  showBanner("앱이 잠금 상태로 전환되었습니다.", "info");
  console.log("[인증] 로그아웃 완료");
});

function renderTable(records) {
  if (!records || records.length === 0) {
    return "<p class='helper-text'>표시할 항목이 없습니다.</p>";
  }
  const headers = Object.keys(records[0]);
  const thead = headers.map((h) => `<th>${escapeHtml(h)}</th>`).join("");
  const tbody = records
    .map((row) => {
      const tds = headers.map((h) => `<td>${escapeHtml(row[h])}</td>`).join("");
      return `<tr>${tds}</tr>`;
    })
    .join("");
  return `<div class="table-wrap"><table><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table></div>`;
}

function renderInspectResult(data) {
  const metricsEl = document.getElementById("inspect-metrics");
  const metrics = [
    ["전체 행 수", `${data.row_count.toLocaleString()}행`],
    ["분반 수 (번호+분반)", `${data.section_count.toLocaleString()}개`],
    ["교과목번호 / 명", `${data.course_code_count} / ${data.course_name_count}`],
    ["단과대 수", `${data.college_count}개`],
    ["정상 개설", `${data.active_count.toLocaleString()}건`],
    ["폐강 강좌", `${data.cancelled_count.toLocaleString()}건`],
  ];
  metricsEl.innerHTML = metrics
    .map(
      ([label, val]) =>
        `<div class="metric-box"><div class="metric-label">${escapeHtml(label)}</div><div class="metric-value">${escapeHtml(val)}</div></div>`
    )
    .join("");

  const warningListEl = document.getElementById("warning-list");
  warningListEl.innerHTML = (data.warnings || [])
    .map((w) => `<li><strong>[${escapeHtml(w.type)}]</strong> ${escapeHtml(w.message)}</li>`)
    .join("");

  const missingWrap = document.getElementById("missing-table-wrap");
  const missingRows = (data.missing_by_column || []).map((m) => ({
    열이름: m.column,
    빈칸수: m.count,
    해석: m.note,
  }));
  missingWrap.innerHTML = renderTable(missingRows);

  const issueWrap = document.getElementById("issue-detail-wrap");
  const issueRows = [];
  (data.numeric_issues || []).forEach((n) => {
    issueRows.push({
      유형: n.issue,
      대상: n.column,
      건수: n.count,
      세부내용: `엑셀 행: ${(n.excel_rows || []).join(", ")}`,
    });
  });
  (data.similar_values || []).forEach((s) => {
    issueRows.push({
      유형: `비슷한 값(${s.reason})`,
      대상: s.column,
      건수: 2,
      세부내용: `'${s.value_a}'(${s.count_a}) vs '${s.value_b}'(${s.count_b})`,
    });
  });
  issueWrap.innerHTML = renderTable(issueRows);

  document.getElementById("section-inspect").classList.remove("hidden");
  document.getElementById("section-suggest").classList.remove("hidden");
}

function buildSuggestionItemHtml(s, labelPrefix = "") {
  const colsText = (s.columns || []).join(", ");
  const paramsJson = escapeHtml(JSON.stringify(s.params || {}));
  return `
    <label class="suggestion-item">
      <input type="checkbox" class="suggest-check" value="${escapeHtml(s.tool)}" data-params="${paramsJson}" checked />
      <div>
        <div><strong>${escapeHtml(labelPrefix)}${escapeHtml(s.tool)}</strong></div>
        <div>${escapeHtml(s.reason)}</div>
        <div class="suggestion-meta">사용할 열: ${escapeHtml(colsText || "자동 선택")}</div>
      </div>
    </label>
  `;
}

function renderSuggestions(suggestions) {
  const listEl = document.getElementById("suggestion-list");
  listEl.innerHTML = suggestions.map((s, idx) => buildSuggestionItemHtml(s, `${idx + 1}. `)).join("");
  btnRun.classList.remove("hidden");
}

function renderCalculationResults(results) {
  activeCharts.forEach((ch) => ch.destroy());
  activeCharts = [];

  const container = document.getElementById("results-container");
  container.innerHTML = "";

  results.forEach((res, idx) => {
    const card = document.createElement("div");
    card.className = "result-card";
    const chartId = `result-chart-${idx}`;

    card.innerHTML = `
      <h3>${idx + 1}. ${escapeHtml(res.title)} (${escapeHtml(res.tool_name)})</h3>
      <div class="meta-grid">
        <div><strong>기준(정의):</strong> ${escapeHtml(res.definition)}</div>
        <div><strong>사용한 행 수:</strong> ${escapeHtml(res.used_rows)}행</div>
        <div><strong>제외한 행 수와 이유:</strong> ${escapeHtml(res.excluded_rows)}행 (${escapeHtml(res.excluded_reason)})</div>
        <div><strong>사용한 열:</strong> ${escapeHtml((res.used_columns || []).join(", "))}</div>
        <div><strong>단위:</strong> ${escapeHtml(res.unit)}</div>
        <div><strong>주의사항:</strong> ${escapeHtml(res.cautions)}</div>
      </div>
      ${renderTable(res.records)}
      ${res.chart ? `<div class="chart-wrap"><canvas id="${chartId}"></canvas></div>` : ""}
    `;
    container.appendChild(card);

    if (res.chart && typeof Chart !== "undefined") {
      const ctx = document.getElementById(chartId).getContext("2d");
      const chart = new Chart(ctx, {
        type: "bar",
        data: {
          labels: res.chart.labels,
          datasets: [
            {
              label: res.chart.label || res.title,
              data: res.chart.values,
              backgroundColor: "rgba(29, 78, 216, 0.7)",
              borderColor: "rgba(30, 64, 175, 1)",
              borderWidth: 1,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: true } },
        },
      });
      activeCharts.push(chart);
    }
  });

  document.getElementById("section-results").classList.remove("hidden");
  document.getElementById("section-report").classList.remove("hidden");
}

function renderMarkdownPreview() {
  if (typeof marked !== "undefined" && typeof DOMPurify !== "undefined") {
    reportPreviewEl.innerHTML = DOMPurify.sanitize(marked.parse(latestReportText || ""));
  } else {
    reportPreviewEl.textContent = latestReportText;
  }
}

function switchReportView(mode) {
  reportViewMode = mode;
  if (mode === "preview") {
    renderMarkdownPreview();
    reportPreviewEl.classList.remove("hidden");
    reportEditorEl.classList.add("hidden");
    btnTabPreview.classList.add("active");
    btnTabEdit.classList.remove("active");
  } else {
    reportEditorEl.value = latestReportText;
    reportEditorEl.classList.remove("hidden");
    reportPreviewEl.classList.add("hidden");
    btnTabEdit.classList.add("active");
    btnTabPreview.classList.remove("active");
  }
}

btnTabPreview.addEventListener("click", () => switchReportView("preview"));
btnTabEdit.addEventListener("click", () => switchReportView("edit"));
reportEditorEl.addEventListener("input", () => {
  latestReportText = reportEditorEl.value;
});

btnUpload.addEventListener("click", async () => {
  const file = fileInputEl.files[0];
  if (!file) {
    showBanner("먼저 .xls 엑셀 파일을 선택해 주세요.", "error");
    return;
  }
  if (!file.name.toLowerCase().endsWith(".xls")) {
    showBanner("'.xls' 형식의 엑셀 파일만 업로드할 수 있습니다.", "error");
    return;
  }

  console.log("[1단계] 파일 업로드 및 진단 시작:", file.name);
  setBusy(true, "처리 중... 엑셀 파일을 읽고 진단하고 있습니다.");

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch("/inspect", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "진단 중 오류가 발생했습니다.");
    }
    console.log("[1단계] 진단 완료:", data);
    renderInspectResult(data);
    document.getElementById("suggestion-list").innerHTML = "";
    btnRun.classList.add("hidden");
    document.getElementById("section-results").classList.add("hidden");
    document.getElementById("section-report").classList.add("hidden");
    showBanner(`'${data.filename}' 진단이 완료되었습니다. (총 ${data.row_count.toLocaleString()}행)`, "info");
  } catch (err) {
    console.error("[1단계] 오류:", err);
    showBanner(err.message, "error");
  } finally {
    setBusy(false);
  }
});

btnAsk.addEventListener("click", async () => {
  const question = (questionInputEl.value || "").trim();
  if (!question) {
    showBanner("분석하고 싶은 질문을 입력해 주세요.", "error");
    return;
  }

  console.log("[3단계] 직접 질문 번역 요청:", question);
  setBusy(true, "처리 중... AI가 질문을 안전한 계산 도구와 조건으로 변환하고 있습니다.");

  try {
    const res = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "질문 처리 중 오류가 발생했습니다.");
    }
    if (!data.answerable) {
      showBanner(`분석 불가 안내: ${data.reason}`, "error");
      return;
    }

    const listEl = document.getElementById("suggestion-list");
    listEl.insertAdjacentHTML("afterbegin", buildSuggestionItemHtml(data.suggestion, "★ "));
    btnRun.classList.remove("hidden");
    questionInputEl.value = "";
    showBanner("질문에 맞는 분석 항목이 맨 위에 추가되었습니다. '선택한 분석 계산 실행'을 눌러 주세요.", "info");
  } catch (err) {
    console.error("[3단계] 질문 오류:", err);
    showBanner(err.message, "error");
  } finally {
    setBusy(false);
  }
});

btnSuggest.addEventListener("click", async () => {
  console.log("[3단계] AI 분석 제안 요청 시작");
  setBusy(true, "처리 중... AI가 진단 요약을 보고 분석을 제안하고 있습니다.");

  try {
    const res = await fetch("/suggest", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "AI 제안을 받아오지 못했습니다.");
    }
    console.log("[3단계] AI 제안 수신:", data);
    renderSuggestions(data.suggestions || []);
    showBanner("AI 분석 제안이 도착했습니다. 실행할 항목을 선택하세요.", "info");
  } catch (err) {
    console.error("[3단계] 오류:", err);
    showBanner(err.message, "error");
  } finally {
    setBusy(false);
  }
});

btnRun.addEventListener("click", async () => {
  const checked = Array.from(document.querySelectorAll(".suggest-check:checked"));
  if (checked.length === 0) {
    showBanner("계산할 분석을 1개 이상 선택해 주세요.", "error");
    return;
  }

  const selected = checked.map((el) => ({
    tool: el.value,
    params: JSON.parse(el.dataset.params || "{}"),
  }));

  console.log("[4단계] pandas 계산 실행:", selected);
  setBusy(true, "처리 중... 파이썬(pandas)이 숫자를 계산하고 있습니다.");

  try {
    const res = await fetch("/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "계산 실행 중 오류가 발생했습니다.");
    }
    console.log("[4단계] 계산 완료:", data);
    renderCalculationResults(data.results || []);
    showBanner(`${data.results.length}개 분석 계산이 완료되었습니다.`, "info");
  } catch (err) {
    console.error("[4단계] 오류:", err);
    showBanner(err.message, "error");
  } finally {
    setBusy(false);
  }
});

btnExplain.addEventListener("click", async () => {
  const modeEl = document.querySelector('input[name="report-mode"]:checked');
  const mode = modeEl ? modeEl.value : "B";

  console.log(`[5단계] 보고서 생성 시작 (모드 ${mode})`);
  setBusy(true, `처리 중... AI가 모드 ${mode} 보고서를 작성하고 있습니다.`);

  try {
    const res = await fetch("/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "보고서 생성 중 오류가 발생했습니다.");
    }
    console.log(`[5단계] 보고서 생성 완료 (모드 ${data.mode}, ${data.report.length}자)`);
    latestReportText = data.report;
    latestReportMode = data.mode;

    reportTabsEl.classList.remove("hidden");
    btnCopy.classList.remove("hidden");
    btnDownload.classList.remove("hidden");
    switchReportView("preview");
    showBanner(`모드 ${data.mode} 보고서가 생성되었습니다. 미리보기 또는 직접 수정을 이용해 보세요.`, "info");
  } catch (err) {
    console.error("[5단계] 오류:", err);
    showBanner(err.message, "error");
  } finally {
    setBusy(false);
  }
});

btnCopy.addEventListener("click", async () => {
  if (!latestReportText) return;
  try {
    if (reportViewMode === "preview") {
      renderMarkdownPreview();
      const plainText = reportPreviewEl.innerText;
      const styledHtml = `<div style="font-family:'Malgun Gothic',sans-serif;line-height:1.6;">${reportPreviewEl.innerHTML.replaceAll(
        "<table>",
        '<table style="border-collapse:collapse;width:100%;" border="1" cellpadding="6">'
      )}</div>`;

      if (window.ClipboardItem && navigator.clipboard.write) {
        await navigator.clipboard.write([
          new ClipboardItem({
            "text/html": new Blob([styledHtml], { type: "text/html" }),
            "text/plain": new Blob([plainText], { type: "text/plain" }),
          }),
        ]);
      } else {
        await navigator.clipboard.writeText(plainText);
      }
      showBanner("미리보기 서식(워드·이메일용)과 깔끔한 텍스트(카톡용)가 복사되었습니다.", "info");
    } else {
      await navigator.clipboard.writeText(latestReportText);
      showBanner("수정 중인 마크다운 원문이 클립보드에 복사되었습니다.", "info");
    }
    console.log("[5단계] 보고서 복사 완료 (viewMode=" + reportViewMode + ")");
  } catch (err) {
    console.error("[5단계] 복사 실패:", err);
    showBanner("클립보드 복사에 실패했습니다. 직접 드래그해서 복사해 주세요.", "error");
  }
});

btnDownload.addEventListener("click", () => {
  if (!latestReportText) return;
  const blob = new Blob([latestReportText], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `course-analysis-report-mode${latestReportMode}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  console.log("[5단계] .md 파일 다운로드 완료");
});

checkAuthStatus();
