const state = {
  csrfToken: "",
  email: "",
  mock: false,
  page: sessionStorage.getItem("eggscan_page") || "overview",
  categories: [],
  stream: null,
  session: { total: 0, byClass: {}, msSum: 0 },
  records: { page: 1, pages: 1, pageSize: 8, requestId: 0, searchTimer: 0, total: 0 },
  logs: { page: 1, pages: 1, requestId: 0, searchTimer: 0 },
  datasetTrainTimer: 0,
  eggFrame: 0,
  authRedirect: false,
  otpEmail: "",
  uploadedPhoto: false,
  returnToLiveAfterUpload: false,
};

const titles = {
  overview: ["Operations dashboard", "Overview"],
  live: ["Capture", "Live scan"],
  records: ["Scan history", "Scan Records"],
  dataset: ["Training data", "Dataset"],
  report: ["Write-up", "Report"],
  logs: ["System monitoring", "Activity Logs"],
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function safeColor(color) {
  return /^#[0-9a-fA-F]{6}$/.test(color || "") ? color : "#d99a2b";
}

function parseDate(value) {
  if (!value) return null;
  let text = String(value);
  if (!/[zZ]|[+-]\d\d:?\d\d$/.test(text)) text += "Z";
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatWhen(value) {
  const date = parseDate(value);
  if (!date) return "—";
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function formatRecordTimestamp(value) {
  const date = parseDate(value);
  if (!date) return "Timestamp unavailable";
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short",
  }).format(date);
}

function num(value, digits, suffix) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
  const text = Number(value).toFixed(digits);
  return suffix ? text + suffix : text;
}

function pct(value) {
  const text = num(value === null || value === undefined ? null : Number(value) * 100, 1);
  return text === null ? null : text + "%";
}

function toast(message, kind) {
  const node = document.createElement("div");
  node.className = "toast" + (kind === "error" ? " error" : "");
  node.textContent = message;
  document.getElementById("toasts").appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

function clearSession() {
  state.email = "";
  state.mock = false;
  sessionStorage.removeItem("eggscan_token");
  sessionStorage.removeItem("eggscan_email");
  sessionStorage.removeItem("eggscan_mock");
}

function completeLogin(data) {
  state.email = data.email;
  state.mock = !!data.mock_mode;
  document.getElementById("login-password").value = "";
  document.getElementById("otp-form").hidden = true;
  document.getElementById("login-form").hidden = false;
  document.getElementById("otp-code").value = "";
  state.otpEmail = "";
  showApp();
  setPage(state.page || "overview");
}

function upstreamResponseMessage(response) {
  if (response.headers.get("cf-mitigated") === "challenge") {
    return "Cloudflare requires verification for this request. Complete it in this browser, then retry.";
  }
  return "This request did not return a response from EggScan (HTTP " + response.status + "). Check the tunnel and server, then retry.";
}

async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  const method = options.method || "GET";
  if (!/^(GET|HEAD|OPTIONS)$/i.test(method) && state.csrfToken) headers["X-CSRF-Token"] = state.csrfToken;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.json);
  }
  let response;
  try {
    response = await secureFetch(path, {
      method,
      headers,
      body: options.body,
      signal: options.signal,
      credentials: "same-origin",
    });
  } catch (error) {
    if (error && error.name === "AbortError") throw error;
    throw new Error("Cannot reach the EggScan server.");
  }
  if (response.status === 204) return null;
  const eggscanOriginResponse = response.headers.get("X-EggScan-Origin") === "app";
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (error) {
      data = { detail: text };
    }
  }
  if (!response.ok) {
    let message = "Something went wrong.";
    if (typeof data?.detail === "string") message = data.detail;
    else if (Array.isArray(data?.detail)) message = data.detail.map((item) => item.msg).join(" ");
    if (!eggscanOriginResponse) {
      message = upstreamResponseMessage(response);
    } else if (response.status === 401 && ["Not authenticated.", "Session expired. Please sign in again."].includes(message)) {
      message = "Your EggScan session is missing or expired. Please sign in again to continue.";
      if (!state.authRedirect) {
        state.authRedirect = true;
        clearSession();
        stopCamera();
        showLogin();
        if (!options.silentAuth) toast(message, "error");
        setTimeout(() => { state.authRedirect = false; }, 600);
      }
    }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return data;
}

async function secureFetch(path, options = {}) {
  const method = options.method || "GET";
  const headers = new Headers(options.headers || {});
  if (!/^(GET|HEAD|OPTIONS)$/i.test(method) && state.csrfToken) {
    headers.set("X-CSRF-Token", state.csrfToken);
  }
  return fetch(path, { ...options, method, headers, credentials: "same-origin" });
}

function openModal(options) {
  const modal = document.getElementById("modal");
  const field = document.getElementById("modal-input");
  const ok = document.getElementById("modal-ok");
  const cancel = document.getElementById("modal-cancel");
  document.getElementById("modal-title").textContent = options.title;
  document.getElementById("modal-body").textContent = options.body || "";
  ok.textContent = options.okLabel || "Confirm";
  if (options.input) {
    field.hidden = false;
    field.value = options.input.value ?? "";
    field.placeholder = options.input.placeholder || "";
  } else {
    field.hidden = true;
    field.value = "";
  }
  modal.hidden = false;
  if (options.input) field.focus();
  return new Promise((resolve) => {
    function close(value) {
      modal.hidden = true;
      ok.onclick = null;
      cancel.onclick = null;
      field.onkeydown = null;
      resolve(value);
    }
    ok.onclick = () => close(options.input ? field.value : true);
    cancel.onclick = () => close(options.input ? null : false);
    field.onkeydown = (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        close(field.value);
      }
    };
  });
}

function showLogin() {
  document.getElementById("login-view").hidden = false;
  document.getElementById("app-view").hidden = true;
  startEgg();
}

function showApp() {
  document.getElementById("login-view").hidden = true;
  document.getElementById("app-view").hidden = false;
  document.getElementById("who").textContent = state.email;
  document.getElementById("mock-badge").hidden = !state.mock;
  document.getElementById("yolo-badge").hidden = state.mock;
  document.getElementById("mock-note").hidden = !state.mock;
  stopEgg();
}

function setPage(name) {
  if (!titles[name]) name = "overview";
  state.page = name;
  sessionStorage.setItem("eggscan_page", name);
  document.querySelectorAll(".page").forEach((page) => {
    page.classList.toggle("active", page.id === "page-" + name);
  });
  document.querySelectorAll(".nav-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === name);
  });
  document.getElementById("page-kicker").textContent = titles[name][0];
  document.getElementById("page-title").textContent = titles[name][1];
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("nav-backdrop").classList.remove("show");
  if (name !== "live") stopCamera();
  loadPage(name);
}

async function loadPage(name) {
  try {
    if (name === "overview") await loadOverview();
    else if (name === "live") {
      await ensureCategories();
      renderTally();
      if (typeof loadEspAddress === "function") await loadEspAddress();
    }
    else if (name === "records") await loadRecords();
    else if (name === "dataset") await loadDataset();
    else if (name === "report") await loadReport();
    else if (name === "logs") await loadLogs();
  } catch (error) {
    if (error.status !== 401 && name !== "overview") toast(error.message, "error");
  }
}

function setStat(id, value, emptyText) {
  document.getElementById(id).textContent = value === null || value === undefined || value === "" ? (emptyText || "No data yet") : value;
}

async function loadOverview() {
  const loading = document.getElementById("overview-loading");
  const errorBox = document.getElementById("overview-error");
  const content = document.getElementById("overview-content");
  const refresh = document.getElementById("overview-refresh");
  const retry = document.getElementById("overview-retry");
  loading.hidden = false;
  errorBox.hidden = true;
  content.setAttribute("aria-busy", "true");
  refresh.disabled = true;
  retry.disabled = true;
  let data;
  try {
    data = await api("/api/stats/overview");
  } catch (error) {
    document.getElementById("overview-error-text").textContent = error.message || "Overview data could not be loaded. Check your connection and try again.";
    errorBox.hidden = false;
    content.hidden = true;
    throw error;
  } finally {
    loading.hidden = true;
    content.removeAttribute("aria-busy");
    refresh.disabled = false;
    retry.disabled = false;
  }
  content.hidden = false;
  state.mock = !!data.mock_mode;
  showApp();
  setStat("stat-total", data.total_scanned, "0");
  setStat("stat-verified", data.verified_count, "0");
  const coverage = data.total_scanned > 0 ? (data.verified_count / data.total_scanned) * 100 : null;
  document.getElementById("stat-verified-note").textContent = coverage === null
    ? "Actual class recorded"
    : `${data.unverified_count || 0} pending · ${pct(coverage)} coverage`;
  document.getElementById("stat-coverage").textContent = coverage === null ? "No scans yet" : `${pct(coverage)} covered`;
  setStat("stat-accuracy", pct(data.accuracy));
  document.getElementById("stat-accuracy-note").textContent = data.labelled_count
    ? "n = " + data.labelled_count + " verified eggs"
    : "Verified eggs";
  setStat("stat-ms", num(data.avg_inference_ms, 1, " ms"));
  setStat("stat-conf", pct(data.avg_confidence));
  const has = data.total_scanned > 0;
  document.getElementById("days-empty").hidden = has;
  document.getElementById("chart-days").hidden = !has;
  document.getElementById("recent-empty").hidden = has;
  document.getElementById("overview-recent-table-wrap").hidden = !has;
  document.getElementById("recent-body").innerHTML = "";
  const distribution = data.class_distribution || [];
  const showDist = data.total_scanned > 0 && distribution.some((item) => Number(item.count) > 0);
  document.getElementById("dist-empty").textContent = data.total_scanned > 0 ? "No supported size class predictions yet" : "No scans yet";
  document.getElementById("dist-empty").hidden = showDist;
  document.getElementById("dist-list").hidden = !showDist;
  if (showDist) {
    const max = Math.max(...distribution.map((item) => item.count), 1);
    document.getElementById("dist-list").innerHTML = distribution.map((item) => `
      <div class="dist-row def-click" data-class="${esc(item.name)}" role="button" tabindex="0" aria-label="View ${esc(item.name)} scans, ${Number(item.count) || 0} records">
        <span style="color:${safeColor(item.color)}">${esc(item.name)}</span>
        <div class="track"><div class="fill" style="width:${((Number(item.count) || 0) / max) * 100}%;background:${safeColor(item.color)}"></div></div>
        <b>${Number(item.count) || 0}</b>
      </div>`).join("");
  }
  if (window.eggscanAfterOverview) window.eggscanAfterOverview(data);
  if (!has) return;
  const labels = data.per_day.map((day) => day.date.slice(5));
  const counts = data.per_day.map((day) => day.count);
  requestAnimationFrame(() => drawBars(document.getElementById("chart-days"), labels, counts));
  document.getElementById("recent-body").innerHTML = data.recent_scans.map((scan) => `
    <tr>
      <td>${esc(formatWhen(scan.created_at))}</td>
      <td style="color:${categoryColor(scan.predicted_class) || "inherit"}">${esc(scan.predicted_class)}</td>
      <td>${esc(num(scan.length_mm, 1))}</td>
      <td>${esc(num(scan.width_mm, 1))}</td>
      <td>${esc(num(scan.area_mm2, 0))}</td>
      <td>${esc(pct(scan.confidence))}</td>
    </tr>`).join("");
}

function prepCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(rect.width, 280);
  const height = Math.max(rect.height, 180);
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return { ctx, width, height };
}

function drawBars(canvas, labels, values) {
  const { ctx, width, height } = prepCanvas(canvas);
  const pad = { l: 28, r: 8, t: 12, b: 28 };
  const max = Math.max(...values, 1);
  const slot = (width - pad.l - pad.r) / Math.max(values.length, 1);
  ctx.strokeStyle = "rgba(19,55,44,0.15)";
  ctx.beginPath();
  ctx.moveTo(pad.l, pad.t);
  ctx.lineTo(pad.l, height - pad.b);
  ctx.lineTo(width - pad.r, height - pad.b);
  ctx.stroke();
  values.forEach((value, index) => {
    const barW = Math.max(slot * 0.62, 2);
    const x = pad.l + index * slot + (slot - barW) / 2;
    const h = ((height - pad.t - pad.b) * value) / max;
    ctx.fillStyle = "#d99a2b";
    ctx.fillRect(x, height - pad.b - h, barW, h);
    if (index % Math.ceil(labels.length / 7) === 0) {
      ctx.fillStyle = "#5e6d66";
      ctx.font = "11px JetBrains Mono, monospace";
      ctx.fillText(labels[index], x, height - 8);
    }
  });
}

function renderTally() {
  const root = document.getElementById("tally");
  if (!root) return;
  if (!state.session.total) {
    const waiting = window.eggscanSession && !window.eggscanSession.active;
    root.innerHTML = waiting
      ? '<p class="empty">Press Start session, then classify eggs. Space scans the next egg.</p>'
      : '<p class="empty">No eggs classified in this session.</p>';
    return;
  }
  const avg = state.session.msSum ? state.session.msSum / state.session.total : null;
  const rows = state.categories.map((cat) => {
    const count = state.session.byClass[cat.name] || 0;
    return `<div class="dist-row"><span style="color:${safeColor(cat.color)}">${esc(cat.name)}</span><b>${count}</b></div>`;
  }).join("");
  const avgLine = avg === null ? "" : `<p class="hint">Average ${esc(num(avg, 1, " ms"))}</p>`;
  root.innerHTML = `<p class="tally-count">${state.session.total}</p>${avgLine}<div class="dist-list">${rows}</div>`;
}

function showStage(mode) {
  const view = document.getElementById("live-view");
  const hud = document.getElementById("live-hud");
  if (view) view.hidden = mode !== "live";
  if (hud) hud.hidden = mode !== "live";
  const video = document.getElementById("cam");
  if (video) video.hidden = mode !== "live";
  document.getElementById("snap").hidden = mode !== "still";
  document.getElementById("stage-empty").hidden = mode !== "idle";
  const removeUpload = document.getElementById("btn-remove-upload");
  if (removeUpload) removeUpload.hidden = mode !== "still" || !state.uploadedPhoto;
}

function stopCamera() {
  if (state.stream) {
    state.stream.getTracks().forEach((track) => track.stop());
    state.stream = null;
  }
  const video = document.getElementById("cam");
  if (video) video.srcObject = null;
  const button = document.getElementById("btn-cam");
  if (button && state.page === "live") button.textContent = "Start scan";
  if (window.eggscanCameraChanged) window.eggscanCameraChanged();
}

async function startCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    toast("This browser cannot open a camera. Upload an image instead.", "error");
    return;
  }
  stopCamera();
  let settings = { camera_index: 0 };
  try { settings = await api("/api/settings"); } catch (error) { /* default camera */ }
  try {
    const probe = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    probe.getTracks().forEach((track) => track.stop());
    const devices = await navigator.mediaDevices.enumerateDevices();
    const cameras = devices.filter((device) => device.kind === "videoinput");
    const chosen = cameras[settings.camera_index] || cameras[0];
    const video = chosen ? { deviceId: { exact: chosen.deviceId } } : { facingMode: "environment" };
    state.stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
  } catch (error) {
    toast("Camera permission was blocked. You can still upload an image.", "error");
    return;
  }
  const video = document.getElementById("cam");
  video.srcObject = state.stream;
  showStage("live");
  document.getElementById("btn-cam").textContent = "Stop scan";
  if (window.eggscanCameraChanged) window.eggscanCameraChanged();
}

function drawBox(bbox, label) {
  const canvas = document.getElementById("snap");
  const ctx = canvas.getContext("2d");
  const stroke = Math.max(2, canvas.width / 280);
  ctx.lineWidth = stroke;
  ctx.strokeStyle = "#d99a2b";
  ctx.strokeRect(bbox.x, bbox.y, bbox.width, bbox.height);
  const size = Math.max(16, canvas.width / 36);
  ctx.font = `600 ${size}px JetBrains Mono, monospace`;
  const measured = ctx.measureText(label);
  const pad = 6;
  let top = bbox.y - size - pad * 2;
  if (top < 0) top = bbox.y + pad;
  ctx.fillStyle = "#d99a2b";
  ctx.fillRect(bbox.x, top, measured.width + pad * 2, size + pad);
  ctx.fillStyle = "#13372c";
  ctx.fillText(label, bbox.x + pad, top + size);
}

function renderResult(data) {
  if (!data.detected) {
    const outside = data.message && data.message.indexOf("Out of range") === 0;
    liveText("live-status", outside ? data.message : "Not saved");
    liveText("live-reason", data.message || "No egg detected or confidence is below the threshold.");
    paintClass("live-class", outside ? "" : "");
    liveText("live-class", outside ? data.message : "—");
    liveText("live-size", data.length_mm == null ? "—" : num(data.length_mm, 1) + " × " + num(data.width_mm, 1) + " mm");
    liveText("live-conf", "—");
    liveText("live-ms", data.inference_ms == null ? "—" : num(data.inference_ms, 1, " ms"));
    return;
  }
  liveText("live-status", data.mock ? "Mock detection" : "YOLO detection");
  liveText("live-reason", "");
  paintClass("live-class", data.predicted_class || data.class_name || "—");
  if (data.egg_count > 1) liveText("live-reason", "Multiple eggs detected. Showing the highest-confidence egg.");
  liveText("live-size", num(data.length_mm, 1) + " × " + num(data.width_mm, 1) + " mm");
  liveText("live-conf", pct(data.confidence) || "—");
  liveText("live-ms", num(data.inference_ms, 1, " ms"));
  const countThis = !window.eggscanSession || window.eggscanSession.active;
  if (countThis) {
    state.session.total += 1;
    state.session.byClass[data.predicted_class] = (state.session.byClass[data.predicted_class] || 0) + 1;
    state.session.msSum = (state.session.msSum || 0) + Number(data.inference_ms || 0);
    renderTally();
  }
}

async function classifyBlob(blob, name) {
  const button = document.getElementById("btn-capture");
  button.disabled = true;
  const previous = button.textContent;
  button.textContent = "Classifying…";
  try {
    const body = new FormData();
    body.append("file", blob, name || "frame.jpg");
    if (window.eggscanSession && window.eggscanSession.active && window.eggscanSession.id) {
      body.append("session_id", window.eggscanSession.id);
    }
    window.eggscanLastBlob = blob;
    window.eggscanLastName = name || "frame.jpg";
    if (window.eggscanPipelineStart) window.eggscanPipelineStart();
    const data = await api("/api/scan", { method: "POST", body });
    if (data.detected && data.bbox) {
      drawBox(data.bbox, `${data.predicted_class} ${Number(data.length_mm).toFixed(1)}x${Number(data.width_mm).toFixed(1)}mm`);
    }
    renderResult(data);
    if (window.eggscanAfterScan) await window.eggscanAfterScan(data);
    if (!data.detected) toast(data.message, "error");
    else toast(data.message, "ok");
  } catch (error) {
    if (window.eggscanAfterScan && error.status !== 401) {
      await window.eggscanAfterScan({ detected: false, saved: false, message: error.message });
    }
    if (error.status !== 401) toast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = previous;
  }
}

async function captureFrame() {
  const video = document.getElementById("cam");
  if (!state.stream || !video.videoWidth) {
    toast("Start the camera first, or upload an image.", "error");
    return;
  }
  const canvas = document.getElementById("snap");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  state.uploadedPhoto = false;
  showStage("still");
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
  await classifyBlob(blob, "frame.jpg");
}

function categoryColor(name) {
  const match = (state.categories || []).find((cat) => cat.name === name);
  return match ? safeColor(match.color) : "";
}

function paintClass(id, name) {
  const node = document.getElementById(id);
  if (!node) return;
  node.textContent = name || "—";
  node.style.color = categoryColor(name) || "";
}

function classOptions(selected) {
  return '<option value="">Choose class</option>' + state.categories.map((cat) =>
    `<option value="${esc(cat.name)}"${cat.name === selected ? " selected" : ""}>${esc(cat.name)}</option>`).join("");
}

async function ensureCategories() {
  if (state.categories.length) return;
  const data = await api("/api/categories");
  state.categories = data.categories;
}

function fillClassFilter() {
  const select = document.getElementById("rec-class");
  const current = select.value;
  const options = '<option value="">All classes</option>' + state.categories.map((cat) =>
    `<option value="${esc(cat.name)}">${esc(cat.name)}</option>`).join("");
  select.innerHTML = options;
  select.value = current;
  const actual = document.getElementById("rec-actual");
  if (actual) {
    const chosen = actual.value;
    actual.innerHTML = '<option value="">All actual classes</option>' + state.categories.map((cat) =>
      `<option value="${esc(cat.name)}">${esc(cat.name)}</option>`).join("");
    actual.value = chosen;
  }
}

async function loadRecords() {
  const requestId = ++state.records.requestId;
  const root = document.getElementById("page-records");
  const loading = document.getElementById("rec-loading");
  const failure = document.getElementById("rec-error");
  loading.hidden = false;
  failure.hidden = true;
  document.getElementById("rec-table-wrap").hidden = true;
  document.getElementById("rec-empty").hidden = true;
  document.getElementById("rec-no-matches").hidden = true;
  try {
    await ensureCategories();
    fillClassFilter();
    const params = new URLSearchParams({
      q: document.getElementById("rec-q").value.trim(),
      class_name: document.getElementById("rec-class").value,
      correctness: document.getElementById("rec-correct").value,
      page: String(state.records.page),
      page_size: String(state.records.pageSize),
      include_summary: "true",
    });
    const from = document.getElementById("rec-from");
    const to = document.getElementById("rec-to");
    const actualName = document.getElementById("rec-actual");
    if (from.value) params.set("date_from", from.value);
    if (to.value) params.set("date_to", to.value);
    if (actualName.value) params.set("actual_name", actualName.value);
    const data = await api("/api/scans?" + params.toString());
    if (requestId !== state.records.requestId) return;

    const summary = data.summary || {};
    state.records.pages = data.pages;
    state.records.total = Number(data.total) || 0;
    document.getElementById("rec-kpi-total").textContent = Number(summary.total ?? 0).toLocaleString();
    document.getElementById("rec-kpi-verified").textContent = Number(summary.verified ?? 0).toLocaleString();
    document.getElementById("rec-kpi-pending").textContent = Number(summary.pending ?? 0).toLocaleString();
    document.getElementById("rec-kpi-correct").textContent = Number(summary.correct ?? 0).toLocaleString();
    document.getElementById("rec-kpi-incorrect").textContent = Number(summary.incorrect ?? 0).toLocaleString();
    renderRecordFilters();
    loading.hidden = true;

    if (!Number(summary.total)) {
      document.getElementById("rec-empty").hidden = false;
      document.getElementById("rec-range").textContent = "No saved scan records.";
      document.getElementById("rec-body").replaceChildren();
      document.getElementById("rec-pager").replaceChildren();
      if (window.eggscanAfterRecords) window.eggscanAfterRecords(data);
      return;
    }
    if (!data.total) {
      document.getElementById("rec-no-matches").hidden = false;
      document.getElementById("rec-range").textContent = `No matching records · ${Number(summary.total).toLocaleString()} total saved`;
      document.getElementById("rec-body").replaceChildren();
      document.getElementById("rec-pager").replaceChildren();
      if (window.eggscanAfterRecords) window.eggscanAfterRecords(data);
      return;
    }

    document.getElementById("rec-table-wrap").hidden = false;
    const rows = data.items || [];
    document.getElementById("rec-body").innerHTML = rows.map((scan) => {
      const result = scan.correct === null ? '<span class="pill pill-muted">Pending verification</span>'
        : scan.correct ? '<span class="pill pill-ok">Verified · correct</span>'
        : '<span class="pill pill-bad">Verified · mismatch</span>';
      const mock = scan.is_mock ? ' <span class="pill pill-muted">Demo</span>' : "";
      const color = categoryColor(scan.predicted_class) || "#13372c";
      const length = num(scan.length_mm, 1);
      const width = num(scan.width_mm, 1);
      const area = num(scan.area_mm2, 0);
      const confidence = pct(scan.confidence);
      return `<tr data-id="${Number(scan.id)}" tabindex="0" aria-label="Scan record ${Number(scan.id)}, ${esc(scan.predicted_class)}">
        <td data-label="Record"><strong class="records-id">#${Number(scan.id)}</strong>${mock}</td>
        <td data-label="Image"><button class="records-image-button" type="button" data-open-record="${Number(scan.id)}" aria-label="View scan ${Number(scan.id)} image"><img class="def-thumb" data-scan-id="${Number(scan.id)}" alt="Scan ${Number(scan.id)} preview" /></button></td>
        <td data-label="Captured"><time class="records-time" datetime="${esc(scan.created_at || "")}">${esc(formatWhen(scan.created_at))}</time></td>
        <td data-label="Prediction"><span class="records-class" style="--records-class-color:${esc(color)}">${esc(scan.predicted_class || "Unavailable")}</span><small class="records-confidence">${confidence === null ? "Confidence unavailable" : `${esc(confidence)} confidence`}</small></td>
        <td data-label="Verification">${result}${scan.actual_class ? `<small class="records-actual">Actual: ${esc(scan.actual_class)}</small>` : ""}</td>
        <td data-label="Measurements"><span class="records-measure-main">L ${length === null ? "—" : `${esc(length)} mm`} · W ${width === null ? "—" : `${esc(width)} mm`}</span><small class="records-measure-area">Area ${area === null ? "—" : `${esc(area)} mm²`}</small></td>
        <td data-label="Details"><button class="btn btn-ghost records-view" type="button" data-open-record="${Number(scan.id)}" aria-haspopup="dialog" aria-label="View details for scan ${Number(scan.id)}">View</button></td>
      </tr>`;
    }).join("");
    const first = (Number(data.page) - 1) * Number(data.page_size) + 1;
    const last = Math.min(first + rows.length - 1, Number(data.total));
    document.getElementById("rec-range").textContent = `Showing ${first.toLocaleString()}–${last.toLocaleString()} of ${Number(data.total).toLocaleString()} matching records`;
    renderPager("rec-pager", data.page, data.pages, (page) => {
      state.records.page = page;
      loadRecords();
    });
    if (window.eggscanAfterRecords) window.eggscanAfterRecords(data);
  } catch (error) {
    if (requestId !== state.records.requestId) return;
    loading.hidden = true;
    document.getElementById("rec-table-wrap").hidden = true;
    document.getElementById("rec-error-text").textContent = error.message || "Check the connection and try again.";
    failure.hidden = false;
    document.getElementById("rec-body").replaceChildren();
  }
}

function renderRecordFilters() {
  const values = [
    ["Search", document.getElementById("rec-q").value.trim()],
    ["Predicted", document.getElementById("rec-class").value],
    ["Verification", document.getElementById("rec-correct").selectedOptions[0]?.textContent || ""],
    ["Actual", document.getElementById("rec-actual").value],
    ["From", document.getElementById("rec-from").value],
    ["To", document.getElementById("rec-to").value],
  ].filter(([, value]) => value && value !== "All records");
  const root = document.getElementById("rec-active-filters");
  root.replaceChildren();
  values.forEach(([label, value]) => {
    const chip = document.createElement("span");
    chip.className = "records-filter-chip";
    chip.textContent = `${label}: ${value}`;
    root.append(chip);
  });
  root.hidden = values.length === 0;
  document.getElementById("rec-reset").disabled = values.length === 0;
}

function resetRecordFilters() {
  clearTimeout(state.records.searchTimer);
  ["rec-q", "rec-class", "rec-correct", "rec-actual", "rec-from", "rec-to"].forEach((id) => {
    const field = document.getElementById(id);
    field.value = id === "rec-correct" ? "all" : "";
  });
  state.records.page = 1;
  loadRecords();
}

function renderPager(id, page, pages, onPage) {
  const root = document.getElementById(id);
  if (pages <= 1) { root.innerHTML = ""; return; }
  root.innerHTML = `
    <button class="btn btn-ghost" type="button" data-goto="${page - 1}" ${page <= 1 ? "disabled" : ""}>Previous</button>
    <span class="hint">Page ${page} of ${pages}</span>
    <button class="btn btn-ghost" type="button" data-goto="${page + 1}" ${page >= pages ? "disabled" : ""}>Next</button>`;
  root.querySelectorAll("[data-goto]").forEach((button) => {
    button.onclick = () => {
      const next = Number(button.dataset.goto);
      if (next >= 1 && next <= pages) onPage(next);
    };
  });
}

function renderMatrix(confusion, targetId = "model-matrix") {
  const labels = confusion.labels || [];
  const matrix = confusion.matrix || [];
  const max = Math.max(1, ...matrix.flat());
  const head = `<div class="matrix-row" style="grid-template-columns: 110px repeat(${labels.length}, minmax(36px, 1fr))"><div></div>${labels.map((label) => `<div class="matrix-label">${esc(label)}</div>`).join("")}</div>`;
  const rows = labels.map((label, row) => {
    const cells = labels.map((_, col) => {
      const count = matrix[row][col] || 0;
      const alpha = count ? 0.15 + 0.75 * (count / max) : 0.04;
      return `<button type="button" class="matrix-cell" data-actual="${esc(label)}" data-predicted="${esc(labels[col])}" style="background:rgba(19,55,44,${alpha});color:${count / max > 0.6 ? "#f6f1e5" : "#13372c"}">${count}</button>`;
    }).join("");
    return `<div class="matrix-row" style="grid-template-columns: 110px repeat(${labels.length}, minmax(36px, 1fr))"><div class="matrix-label">${esc(label)}</div>${cells}</div>`;
  }).join("");
  const target = document.getElementById(targetId);
  if (target) target.innerHTML = head + rows;
}

function benchCard(label, value) {
  return `<article class="card stat"><p>${esc(label)}</p><strong>${esc(value)}</strong></article>`;
}

const DATASET_CLASSES = [
  ["small", "Small"],
  ["medium", "Medium"],
  ["large", "Large"],
  ["extra large", "Extra Large"],
  ["jumbo", "Jumbo"],
];

function renderIncoming(data) {
  const ready = document.getElementById("data-ready");
  const list = document.getElementById("data-class-list");
  if (!data || !ready || !list) return;
  const counts = data.classes || {};
  ready.textContent = (data.ready_classes || 0) + " / 5 classes ready";
  const annotated = data.annotated || {};
  list.innerHTML = DATASET_CLASSES.map(([key, name]) => {
    const count = Number(counts[key] || 0);
    const done = Number(annotated[key] || 0);
    const mark = count > 0 && done === count ? "✓" : "·";
    return `<div class="dist-row"><span>${mark} ${esc(name)}</span><b>${count} images · ${done} annotated</b></div>`;
  }).join("");
  DATASET_CLASSES.forEach(([key]) => {
    const count = Number(counts[key] || 0);
    const done = Number(annotated[key] || 0);
    const node = document.querySelector('[data-annotate-status="' + key + '"]');
    if (node) node.textContent = done + " / " + count + " annotated";
    const remove = document.querySelector('[data-delete-images="' + key + '"]');
    if (remove) remove.disabled = count === 0;
  });
  const process = document.getElementById("data-process");
  if (process) process.disabled = !data.valid;
}

function renderDatasetJob(result) {
  const job = document.getElementById("data-job");
  if (!job || !result) return;
  job.hidden = false;
  const bar = document.getElementById("data-bar");
  if (bar) bar.style.width = Math.max(0, Math.min(100, Number(result.percent) || 0)) + "%";
  const steps = document.getElementById("data-steps");
  if (steps) {
    steps.innerHTML = (result.steps || []).map((step) => {
      const mark = step.ok ? "✓" : "✗";
      return `<li class="${step.ok ? "ok" : "bad"}">${mark} ${esc(step.label)}</li>`;
    }).join("");
  }
  const status = document.getElementById("data-status");
  if (status) status.textContent = result.status || "";
  const train = document.getElementById("data-train");
  if (train && result.ready !== undefined) train.disabled = !result.ready;
}

function renderTrain(result) {
  const job = document.getElementById("data-job");
  if (job) job.hidden = false;
  const status = document.getElementById("data-status");
  const epoch = result && result.epoch ? "Epoch: " + result.epoch + " / " + result.epochs : "";
  if (status) status.textContent = [result && result.message, epoch].filter(Boolean).join(" · ");
  const bar = document.getElementById("data-bar");
  if (bar && result && result.status === "running") bar.style.width = Math.max(0, Math.min(100, Number(result.percent) || 0)) + "%";
  if (bar && result && result.status === "done") bar.style.width = "100%";
  const train = document.getElementById("data-train");
  if (train) train.disabled = !result || result.status === "running" || result.ready === false;
  const use = document.getElementById("data-use");
  if (use) use.hidden = !result || result.status !== "done";
  if (result && result.status === "done") {
    const steps = document.getElementById("data-steps");
    if (steps) {
      steps.innerHTML = ["Training completed", "Checkpoint loaded", "Active inference version confirmed"].map((label) => `<li>✓ ${esc(label)}</li>`).join("");
    }
    if (typeof result.mock === "boolean") {
      state.mock = result.mock;
      showApp();
    }
  }
}

function pollTraining() {
  clearInterval(state.datasetTrainTimer);
  state.datasetTrainTimer = setInterval(async () => {
    try {
      const result = await api("/api/dataset/train");
      renderTrain(result);
      if (result.status !== "running") {
        clearInterval(state.datasetTrainTimer);
        if (result.status === "done") toast(result.message, "ok");
        if (result.status === "error") toast(result.message || "Training failed.", "error");
        loadDataset().catch((error) => { if (error.status !== 401) toast(error.message, "error"); });
      }
    } catch (error) {
      clearInterval(state.datasetTrainTimer);
      if (error.status !== 401) toast(error.message, "error");
    }
  }, 3000);
}

function renderPreparedOverview(data) {
  const total = Math.max(0, Number(data.total_images) || 0);
  const labelled = Math.min(total, Math.max(0, Number(data.labeled_images) || 0));
  const coverage = total ? Math.round((labelled / total) * 100) : 0;
  const classes = Array.isArray(data.per_class) ? data.per_class : [];
  const represented = classes.filter((item) => Number(item.images) > 0).length;
  const boxes = classes.reduce((sum, item) => sum + Math.max(0, Number(item.objects) || 0), 0);
  const splits = data.splits || {};
  const splitRows = [
    { key: "train", label: "Train", color: "train" },
    { key: "val", label: "Validation", color: "validation" },
    { key: "test", label: "Test", color: "test" },
  ].map((item) => ({ ...item, count: Math.max(0, Number(splits[item.key]) || 0) }));
  const splitTotal = splitRows.reduce((sum, item) => sum + item.count, 0);
  const status = document.getElementById("prepared-status");
  const coverageBar = document.getElementById("prepared-coverage-bar");
  const ready = total > 0 && labelled === total;
  status.textContent = ready ? "Labels ready" : total ? "Needs labels" : "Not prepared";
  status.dataset.state = ready ? "ready" : total ? "pending" : "empty";
  document.getElementById("prepared-coverage-value").textContent = `${coverage}%`;
  coverageBar.style.width = `${coverage}%`;
  coverageBar.closest(".prepared-track").setAttribute("aria-valuenow", String(coverage));
  document.getElementById("prepared-coverage-note").textContent = total
    ? `${labelled} of ${total} images have saved egg annotations${total > labelled ? ` · ${total - labelled} still need labels` : ""}.`
    : "Saved annotations will appear here after you prepare a dataset.";
  document.getElementById("prepared-class-count").textContent = `${represented} / 5`;
  document.getElementById("prepared-box-count").textContent = String(boxes);
  document.getElementById("prepared-total").textContent = `${total} ${total === 1 ? "image" : "images"}`;
  const splitBar = document.getElementById("prepared-split-bar");
  splitBar.replaceChildren();
  splitBar.setAttribute("aria-label", splitTotal
    ? `Training split: ${splitRows.map((item) => `${item.label} ${item.count}`).join(", ")}`
    : "Training split is empty");
  splitRows.forEach((item) => {
    if (!item.count || !splitTotal) return;
    const segment = document.createElement("span");
    segment.className = `prepared-segment prepared-segment-${item.color}`;
    segment.style.width = `${(item.count / splitTotal) * 100}%`;
    segment.title = `${item.label}: ${item.count} images`;
    splitBar.append(segment);
  });
  const splitList = document.getElementById("prepared-splits");
  splitList.replaceChildren();
  splitRows.forEach((item) => {
    const row = document.createElement("div");
    row.className = "prepared-split-row";
    const label = document.createElement("span");
    label.className = "prepared-split-label";
    const marker = document.createElement("i");
    marker.className = `prepared-legend-dot prepared-dot-${item.color}`;
    marker.setAttribute("aria-hidden", "true");
    label.append(marker, document.createTextNode(item.label));
    const value = document.createElement("strong");
    value.textContent = `${item.count} ${item.count === 1 ? "image" : "images"}${splitTotal ? ` · ${Math.round((item.count / splitTotal) * 100)}%` : ""}`;
    row.append(label, value);
    splitList.append(row);
  });
  document.getElementById("prepared-empty").hidden = total > 0;
}

async function loadDataset() {
  const data = await api("/api/dataset");
  document.getElementById("data-summary").innerHTML = [
    benchCard("Images", String(data.total_images)),
    benchCard("Train", String(data.splits.train || 0)),
    benchCard("Validation", String(data.splits.val || 0)),
    benchCard("Test", String(data.splits.test || 0)),
    benchCard("Labelled", String(data.labeled_images)),
  ].join("");
  renderPreparedOverview(data);
  try {
    const [incoming, training] = await Promise.all([
      api("/api/dataset/incoming"),
      api("/api/dataset/train"),
    ]);
    renderIncoming(incoming);
    if (training.ready && training.status !== "running") {
      const train = document.getElementById("data-train");
      if (train) train.disabled = false;
    }
    if (training.status === "running") {
      renderTrain(training);
      pollTraining();
    }
    if (training.status === "done") renderTrain(training);
  } catch (error) {
    if (error.status === 401) throw error;
  }
}

async function loadReport() {
  const root = document.getElementById("report-body");
  root.innerHTML = '<p class="empty" role="status">Loading report data…</p>';
  try {
    const data = await api("/api/stats/report");
    const summary = data.summary || {};
    const evaluation = data.evaluation || {};
    const labels = evaluation.labels || [];
    const value = (item, digits = 1) => item === null || item === undefined || !Number.isFinite(Number(item)) ? "N/A" : Number(item).toFixed(digits);
    const percent = (item) => item === null || item === undefined || !Number.isFinite(Number(item)) ? "N/A" : `${(Number(item) * 100).toFixed(1)}%`;
    const classColor = (name) => safeColor((data.colors || {})[name]);
    const classRows = (evaluation.per_class || []).map((row) => `<tr>
      <th scope="row" style="--class-color:${classColor(row.name)}">${esc(row.name)}</th>
      <td>${percent(row.precision)}</td><td>${percent(row.recall)}</td><td>${percent(row.f1)}</td>
      <td>${row.support}</td><td>${row.predicted_count}</td><td>${row.correct}</td><td>${row.incorrect}</td>
    </tr>`).join("");
    const aggregateRows = [
      ["Macro average", evaluation.macro_precision, evaluation.macro_recall, evaluation.macro_f1],
      ["Support-adjusted average", evaluation.support_adjusted_precision, evaluation.support_adjusted_recall, evaluation.support_adjusted_f1],
    ].map((row) => `<tr class="report-aggregate"><th scope="row">${row[0]}</th><td>${percent(row[1])}</td><td>${percent(row[2])}</td><td>${percent(row[3])}</td><td colspan="4">Supported classes only; undefined class metrics are omitted.</td></tr>`).join("");
    const matrix = (evaluation.confusion || {}).matrix || [];
    const matrixPct = (evaluation.confusion || {}).row_percentages || [];
    const matrixRows = labels.map((actual, rowIndex) => `<tr><th scope="row">${esc(actual)}</th>${labels.map((predicted, colIndex) => {
      const count = Number((matrix[rowIndex] || [])[colIndex]) || 0;
      const share = (matrixPct[rowIndex] || [])[colIndex];
      const shareLabel = share === null || share === undefined ? "N/A" : `${(share * 100).toFixed(1)}%`;
      return `<td class="report-confusion-cell${rowIndex === colIndex ? " is-diagonal" : ""}" style="--cell-share:${share || 0}" aria-label="Actual ${esc(actual)}, predicted ${esc(predicted)}: ${count} (${shareLabel} of actual ${esc(actual)})"><strong>${count}</strong><small>${shareLabel}</small></td>`;
    }).join("")}<td class="report-matrix-support">${(matrix[rowIndex] || []).reduce((sum, count) => sum + (Number(count) || 0), 0)}</td></tr>`).join("");
    const rowMax = (items, key) => Math.max(1, ...(items || []).map((row) => Number(row[key]) || 0));
    const predictionRows = (data.prediction_distribution || []).map((row) => {
      const total = Number(row.count) || 0;
      const real = Number(row.real_count) || 0;
      const mock = Number(row.mock_count) || 0;
      const realWidth = total ? (real / total) * 100 : 0;
      const mockWidth = total ? (mock / total) * 100 : 0;
      return `<div class="report-bar-row"><div class="report-bar-label"><span style="--class-color:${classColor(row.name)}">${esc(row.name)}</span><strong>${total}</strong></div><div class="report-bar-track" role="img" aria-label="${esc(row.name)}: ${real} real model scans, ${mock} mock scans"><i class="report-bar-real" style="width:${realWidth}%"></i><i class="report-bar-mock" style="width:${mockWidth}%"></i></div></div>`;
    }).join("");
    const actualRows = (evaluation.actual_distribution || []).map((row) => `<div class="report-bar-row"><div class="report-bar-label"><span style="--class-color:${classColor(row.name)}">${esc(row.name)}</span><strong>${Number(row.count) || 0}</strong></div><div class="report-bar-track" role="img" aria-label="${esc(row.name)} verified actual class: ${Number(row.count) || 0}"><i class="report-bar-actual" style="width:${summary.verified_count ? ((Number(row.count) || 0) / summary.verified_count) * 100 : 0}%"></i></div></div>`).join("");
    const correct = Number(summary.correct_count) || 0;
    const incorrect = Number(summary.incorrect_count) || 0;
    const verified = Number(summary.verified_count) || 0;
    const correctWidth = verified ? (correct / verified) * 100 : 0;
    const incorrectWidth = verified ? (incorrect / verified) * 100 : 0;
    const daily = data.daily || [];
    const dailyMax = rowMax(daily, "count");
    const dailyBars = daily.map((row) => {
      const count = Number(row.count) || 0;
      const good = Number(row.correct) || 0;
      const bad = Number(row.incorrect) || 0;
      const barHeight = count ? Math.max(4, (count / dailyMax) * 100) : 0;
      const dayLabel = new Date(`${row.date}T00:00:00Z`).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
      return `<div class="report-day" title="${esc(dayLabel)}: ${count} verified · ${good} correct · ${bad} incorrect" aria-label="${esc(dayLabel)}: ${count} verified scans, ${good} correct, ${bad} incorrect"><div class="report-day-bar" style="height:${barHeight}%"><i class="report-day-correct" style="height:${count ? (good / count) * 100 : 0}%"></i><i class="report-day-incorrect" style="height:${count ? (bad / count) * 100 : 0}%"></i></div><small>${esc(dayLabel)}</small></div>`;
    }).join("");
    const measurements = data.measurements || [];
    const maxLength = rowMax(measurements, "mean_length_mm");
    const maxWidth = rowMax(measurements, "mean_width_mm");
    const measurementRows = measurements.map((row) => `<tr><th scope="row" style="--class-color:${classColor(row.name)}">${esc(row.name)}</th><td>${row.count}</td><td>${esc(value(row.mean_length_mm))} × ${esc(value(row.mean_width_mm))}</td><td>${esc(value(row.mean_area_mm2))}</td><td>${esc(value(row.median_area_mm2))}</td><td>${row.length_count}/${row.width_count}/${row.area_count}</td></tr>`).join("");
    const measurementBars = measurements.map((row) => `<div class="report-measure-row"><strong style="--class-color:${classColor(row.name)}">${esc(row.name)}</strong><div><small>Length</small><span class="report-measure-track"><i style="width:${row.mean_length_mm ? Math.min(100, (row.mean_length_mm / maxLength) * 100) : 0}%;--class-color:${classColor(row.name)}"></i></span></div><div><small>Width</small><span class="report-measure-track"><i style="width:${row.mean_width_mm ? Math.min(100, (row.mean_width_mm / maxWidth) * 100) : 0}%;--class-color:${classColor(row.name)}"></i></span></div></div>`).join("");
    const period = summary.period && summary.period.from
      ? `${esc(formatWhen(summary.period.from))} – ${esc(formatWhen(summary.period.to))}`
      : "No verified evaluation period yet";
    const sampleNotice = verified
      ? `<p class="report-notice ${verified < 30 ? "is-caution" : ""}">${verified < 30 ? "Preliminary evaluation: " : "Evaluation sample: "}${verified} verified scan records. This does not establish generalization; repeated scans cannot be separated into unique eggs.</p>`
      : '<p class="report-notice is-caution">No eligible verified evaluation samples. Accuracy and class metrics are shown as N/A until real model scans are verified with a supported actual class.</p>';

    root.innerHTML = `<div class="report-layout">
      <header class="report-heading"><div><p class="eyebrow dark">SmartEgg · Analytics</p><h2>Performance report</h2><p class="hint">Classification, measurement, and data-quality summary from saved scan records.</p></div><div class="report-period"><span>Evaluation period</span><strong>${period}</strong><small>Refreshed ${esc(formatWhen(summary.refreshed_at))}</small></div></header>
      ${sampleNotice}
      <section class="report-kpis" aria-label="Executive summary">
        <article><span>Total scans</span><strong>${Number(summary.total_scans) || 0}</strong><small>All saved records</small></article>
        <article><span>Verified samples</span><strong>${verified}</strong><small>Eligible for evaluation</small></article>
        <article><span>Correct</span><strong>${correct}</strong><small>Of ${verified} verified</small></article>
        <article><span>Accuracy</span><strong>${esc(percent(evaluation.accuracy))}</strong><small>Correct ÷ verified</small></article>
        <article><span>Classes represented</span><strong>${Number(summary.represented_classes) || 0}<small> / ${Number(summary.class_count) || 0}</small></strong><small>With verified actual samples</small></article>
      </section>
      <div class="report-two-col">
        <section class="report-section"><div class="report-section-head"><div><p class="eyebrow dark">01 · Classification</p><h3>Per-class performance</h3></div><span>n = ${verified}</span></div>
          <div class="table-wrap"><table class="report-table"><caption>Metrics calculated only from eligible verified real-model scans.</caption><thead><tr><th scope="col">Actual class</th><th scope="col">Precision</th><th scope="col">Recall</th><th scope="col">F1</th><th scope="col">Support</th><th scope="col">Predicted</th><th scope="col">Correct</th><th scope="col">Incorrect</th></tr></thead><tbody>${classRows || `<tr><td colspan="8" class="report-na">No class metrics available.</td></tr>`}${aggregateRows}</tbody></table></div>
          <details class="report-method"><summary>Metric definitions</summary><p>Precision = TP ÷ (TP + FP). Recall = TP ÷ (TP + FN). F1 = harmonic mean of precision and recall. Support is the number of verified actual samples. Macro averages weight supported classes equally; weighted averages weight by actual support. Undefined class metrics are excluded from their aggregate and displayed as N/A.</p></details>
        </section>
        <section class="report-section"><div class="report-section-head"><div><p class="eyebrow dark">02 · Evaluation</p><h3>Confusion matrix</h3></div><span>Actual rows · predicted columns</span></div>
          <div class="table-wrap report-matrix-wrap"><table class="report-confusion"><caption>Each cell shows count and percentage of that actual-class row.</caption><thead><tr><th scope="col">Actual \ Predicted</th>${labels.map((label) => `<th scope="col">${esc(label)}</th>`).join("")}<th scope="col">Support</th></tr></thead><tbody>${matrixRows || `<tr><td colspan="${labels.length + 2}" class="report-na">No verified evaluations.</td></tr>`}</tbody></table></div>
          <div class="report-legend"><span><i class="report-legend-correct"></i>Correct class</span><span><i class="report-legend-error"></i>Misclassification</span></div>
        </section>
      </div>
      <div class="report-two-col">
        <section class="report-section"><div class="report-section-head"><div><p class="eyebrow dark">03 · Distribution</p><h3>Predictions and verified classes</h3></div></div>
          <div class="report-distribution-grid"><div><h4>Predictions across saved scans</h4><p class="hint">Bars separate real-model and mock predictions; neither is ground truth.</p><div class="report-bars">${predictionRows || '<p class="report-na">No predictions available.</p>'}</div><div class="report-legend"><span><i class="report-legend-real"></i>Real model</span><span><i class="report-legend-mock"></i>Mock mode</span></div></div>
            <div><h4>Verified actual class</h4><p class="hint">Ground-truth labels in the evaluation set only.</p><div class="report-bars">${actualRows || '<p class="report-na">No verified labels.</p>'}</div></div></div>
          <div class="report-outcome"><div class="report-section-head"><h4>Verified outcomes</h4><span>${verified} samples</span></div><div class="report-outcome-track" role="img" aria-label="${correct} correct and ${incorrect} incorrect of ${verified} verified samples"><i class="report-outcome-correct" style="width:${correctWidth}%"></i><i class="report-outcome-incorrect" style="width:${incorrectWidth}%"></i></div><div class="report-outcome-key"><span>Correct <b>${correct}</b></span><span>Incorrect <b>${incorrect}</b></span></div></div>
        </section>
        <section class="report-section"><div class="report-section-head"><div><p class="eyebrow dark">04 · Trend</p><h3>Verified scans over time</h3></div><span>Last 14 days · UTC</span></div><div class="report-trend" role="group" aria-label="Daily verified classifications, correct and incorrect">${dailyBars}</div><div class="report-legend"><span><i class="report-legend-correct"></i>Correct</span><span><i class="report-legend-error"></i>Incorrect</span></div></section>
      </div>
      <section class="report-section"><div class="report-section-head"><div><p class="eyebrow dark">05 · Measurements</p><h3>Egg measurements by verified actual class</h3></div><span>Stored millimetre estimates</span></div>
        <p class="hint">Length and width use millimetres; area uses mm². The database does not retain per-scan calibration snapshots, so historical physical calibration cannot be independently confirmed.</p>
        <div class="report-measure-chart" aria-label="Mean length and width comparison">${measurementBars || '<p class="report-na">No valid measurements for verified samples.</p>'}</div>
        <div class="table-wrap"><table class="report-table"><caption>Only finite positive stored measurements are summarized. Valid L/W/area counts are reported separately.</caption><thead><tr><th scope="col">Actual class</th><th scope="col">Samples</th><th scope="col">Mean L × W (mm)</th><th scope="col">Mean area (mm²)</th><th scope="col">Median area (mm²)</th><th scope="col">Valid L/W/area</th></tr></thead><tbody>${measurementRows || `<tr><td colspan="6" class="report-na">No measurement data.</td></tr>`}</tbody></table></div>
      </section>
    </div>`;
  } catch (error) {
    root.innerHTML = `<div class="report-error" role="alert"><h3>Report data could not be loaded</h3><p>${esc(error.message || "Check the server connection, then retry.")}</p><button id="report-retry" class="btn btn-ghost" type="button">Retry</button></div>`;
    const retry = document.getElementById("report-retry");
    if (retry) retry.onclick = () => loadReport();
  }
}

async function loadLogs() {
  const requestId = ++state.logs.requestId;
  const params = new URLSearchParams({
    level: document.getElementById("log-level").value,
    category: document.getElementById("log-category").value,
    q: document.getElementById("log-search").value.trim(),
    actor: document.getElementById("log-actor").value.trim(),
    date_from: document.getElementById("log-from").value,
    date_to: document.getElementById("log-to").value,
    page: String(state.logs.page),
    page_size: "8",
  });
  document.getElementById("logs-loading").hidden = false;
  document.getElementById("logs-error").hidden = true;
  document.getElementById("logs-table-wrap").hidden = true;
  document.getElementById("logs-no-matches").hidden = true;
  document.getElementById("log-empty").hidden = true;
  try {
    const data = await api("/api/logs?" + params.toString());
    if (requestId !== state.logs.requestId) return;
    const rows = data.items || [];
    const allTotal = Number(data.all_total) || 0;
    const total = Number(data.total) || 0;
    const counts = data.level_counts || {};
    document.getElementById("logs-stat-total").textContent = String(total);
    document.getElementById("logs-stat-info").textContent = String(Number(counts.INFO) || 0);
    document.getElementById("logs-stat-warn").textContent = String(Number(counts.WARN) || 0);
    document.getElementById("logs-stat-error").textContent = String(Number(counts.ERROR) || 0);
    document.getElementById("logs-stat-recent").textContent = String(Number(data.recent_count) || 0);
    renderLogActiveFilters();
    document.getElementById("logs-loading").hidden = true;
    if (!allTotal) {
      document.getElementById("log-empty").hidden = false;
      document.getElementById("logs-range").textContent = "No stored activity entries.";
      document.getElementById("log-body").replaceChildren();
      document.getElementById("log-pager").replaceChildren();
      return;
    }
    if (!total) {
      document.getElementById("logs-no-matches").hidden = false;
      document.getElementById("logs-range").textContent = `No matching events · ${allTotal} total stored logs`;
      document.getElementById("log-body").replaceChildren();
      document.getElementById("log-pager").replaceChildren();
      return;
    }
    document.getElementById("logs-table-wrap").hidden = false;
    const first = (Number(data.page) - 1) * Number(data.page_size) + 1;
    const last = Math.min(first + rows.length - 1, total);
    document.getElementById("logs-range").textContent = `Showing ${first.toLocaleString()}–${last.toLocaleString()} of ${total.toLocaleString()} matching logs · newest first`;
    document.getElementById("log-body").innerHTML = rows.map((row) => {
      const level = String(row.level || "").toUpperCase();
      const kind = level === "ERROR" ? "pill-bad" : level === "WARN" ? "pill-warn" : level === "INFO" ? "pill-ok" : "pill-muted";
      const category = LOG_CATEGORY_LABELS[row.category] || "System";
      const actor = row.actor || "—";
      const exact = row.created_at || "";
      return `<tr data-log-id="${Number(row.id)}">
        <td data-label="Timestamp"><time class="log-time" datetime="${esc(exact)}" title="Stored: ${esc(exact)}">${esc(formatLogTimestamp(exact))}</time><small class="log-relative-time">${esc(relativeLogTime(exact))}</small></td>
        <td data-label="Severity"><span class="pill ${kind}">${esc(level || "UNKNOWN")}</span></td>
        <td data-label="Category"><span class="log-category log-category-${esc(row.category || "system")}">${esc(category)}</span></td>
        <td data-label="Actor" class="log-actor-cell">${esc(actor)}</td>
        <td data-label="Event"><button type="button" class="log-event-open" data-log-open="${Number(row.id)}" aria-label="View full details for log ${Number(row.id)}">${esc(row.message || "")}</button></td>
      </tr>`;
    }).join("");
    renderPager("log-pager", data.page, data.pages, (page) => {
      state.logs.page = page;
      loadLogs().catch((error) => showLogsError(error));
    });
  } catch (error) {
    if (requestId === state.logs.requestId) showLogsError(error);
  } finally {
    if (requestId === state.logs.requestId) document.getElementById("logs-loading").hidden = true;
  }
}

const LOG_CATEGORY_LABELS = {
  authentication: "Authentication", scan: "Scan", verification: "Verification",
  dataset: "Dataset", training: "Training", system: "System",
};

function showLogsError(error) {
  document.getElementById("logs-loading").hidden = true;
  document.getElementById("logs-table-wrap").hidden = true;
  document.getElementById("log-empty").hidden = true;
  document.getElementById("logs-no-matches").hidden = true;
  document.getElementById("logs-error").hidden = false;
  const detail = error && error.message ? error.message : "Check your connection and retry.";
  document.getElementById("logs-error").querySelector("span").textContent = `Activity logs could not be loaded. ${detail}`;
}

function formatLogTimestamp(value) {
  const date = parseDate(value);
  if (!date) return "Timestamp unavailable";
  return new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short" }).format(date);
}

function relativeLogTime(value) {
  const date = parseDate(value);
  if (!date) return "";
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const abs = Math.abs(seconds);
  const [amount, unit] = abs < 60 ? [seconds, "second"] : abs < 3600 ? [Math.round(seconds / 60), "minute"] : abs < 86400 ? [Math.round(seconds / 3600), "hour"] : [Math.round(seconds / 86400), "day"];
  return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(amount, unit);
}

function renderLogActiveFilters() {
  const values = [
    ["Search", document.getElementById("log-search").value.trim()],
    ["Severity", document.getElementById("log-level").value],
    ["Category", LOG_CATEGORY_LABELS[document.getElementById("log-category").value] || ""],
    ["Actor", document.getElementById("log-actor").value.trim()],
    ["From", document.getElementById("log-from").value],
    ["To", document.getElementById("log-to").value],
  ].filter(([, value]) => value);
  const root = document.getElementById("logs-active-filters");
  root.replaceChildren();
  values.forEach(([label, value]) => {
    const chip = document.createElement("span");
    chip.className = "logs-filter-chip";
    chip.textContent = `${label}: ${value}`;
    root.append(chip);
  });
  const active = values.length > 0;
  document.getElementById("logs-reset").disabled = !active;
  document.getElementById("logs-active-filters").hidden = !active;
}

function openLogDetails(id) {
  const row = document.querySelector(`#log-body tr[data-log-id="${Number(id)}"]`);
  if (!row) return;
  const dialog = document.getElementById("log-details-dialog");
  const data = row;
  const cells = [...data.cells];
  const message = cells[4].querySelector("button").textContent;
  const details = [
    ["Exact timestamp", cells[0].querySelector("time").getAttribute("datetime") || "Not recorded"],
    ["Severity", cells[1].textContent.trim()],
    ["Category", cells[2].textContent.trim()],
    ["Actor", cells[3].textContent.trim() === "—" ? "Not recorded" : cells[3].textContent.trim()],
    ["Log ID", row.dataset.logId],
    ["Message (sensitive values masked)", message],
  ];
  const list = document.getElementById("log-details-content");
  list.replaceChildren();
  details.forEach(([label, value]) => {
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = value;
    if (label === "Message (sensitive values masked)") description.className = "logs-full-message";
    list.append(term, description);
  });
  dialog.showModal();
}

function resetLogFilters() {
  clearTimeout(state.logs.searchTimer);
  document.getElementById("log-search").value = "";
  document.getElementById("log-level").value = "";
  document.getElementById("log-category").value = "";
  document.getElementById("log-actor").value = "";
  document.getElementById("log-from").value = "";
  document.getElementById("log-to").value = "";
  state.logs.page = 1;
  loadLogs().catch(showLogsError);
}

function startEgg() {
  const stage = document.getElementById("egg-stage");
  if (stage.dataset.ready === "1") return;
  if (!window.THREE) {
    stage.innerHTML = '<div class="egg-fallback"></div>';
    stage.dataset.ready = "1";
    return;
  }
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  stage.innerHTML = "";
  stage.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 20);
  camera.position.set(0, 0.15, 4.2);
  const egg = new THREE.Mesh(
    new THREE.SphereGeometry(1, 48, 48),
    new THREE.MeshStandardMaterial({ color: 0xf4efe2, roughness: 0.42, metalness: 0.04 })
  );
  egg.scale.set(0.78, 1.05, 0.78);
  scene.add(egg);
  scene.add(new THREE.AmbientLight(0xf6f1e5, 0.7));
  const key = new THREE.DirectionalLight(0xffffff, 1.05);
  key.position.set(2, 3, 4);
  scene.add(key);
  const rim = new THREE.PointLight(0xd99a2b, 0.8);
  rim.position.set(-2.2, 0.4, 1.5);
  scene.add(rim);
  function resize() {
    const width = stage.clientWidth || 480;
    const height = stage.clientHeight || 320;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }
  resize();
  window.addEventListener("resize", resize);
  stage.dataset.ready = "1";
  const clock = new THREE.Clock();
  function frame() {
    state.eggFrame = requestAnimationFrame(frame);
    if (document.getElementById("login-view").hidden) return;
    const t = clock.getElapsedTime();
    egg.rotation.y += 0.01;
    egg.rotation.z = Math.sin(t * 0.7) * 0.08;
    egg.position.y = Math.sin(t * 0.9) * 0.05;
    renderer.render(scene, camera);
  }
  frame();
}

function stopEgg() {
  if (state.eggFrame) cancelAnimationFrame(state.eggFrame);
}

function bind() {
  document.getElementById("overview-refresh").onclick = () => loadOverview().catch(() => {});
  document.getElementById("overview-retry").onclick = () => loadOverview().catch(() => {});
  const passwordInput = document.getElementById("login-password");
  const passwordToggle = document.getElementById("lg-toggle-password");
  passwordToggle.addEventListener("click", () => {
    const visible = passwordInput.type === "password";
    passwordInput.type = visible ? "text" : "password";
    passwordToggle.setAttribute("aria-label", visible ? "Hide password" : "Show password");
    passwordToggle.setAttribute("aria-pressed", String(visible));
  });
  document.getElementById("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = document.getElementById("login-submit");
    const error = document.getElementById("login-error");
    error.textContent = "";
    error.classList.remove("lg-success");
    button.disabled = true;
    try {
      const data = await api("/api/auth/login", {
        method: "POST",
        json: {
          email: document.getElementById("login-email").value.trim(),
          password: document.getElementById("login-password").value,
        },
      });
      if (data.otp_required) {
        state.otpEmail = data.email;
        document.getElementById("otp-email-label").textContent = data.email;
        document.getElementById("login-form").hidden = true;
        document.getElementById("otp-form").hidden = false;
        document.getElementById("otp-code").focus();
        document.getElementById("otp-error").textContent = "A verification code was sent to your email.";
        document.getElementById("otp-error").classList.add("lg-success");
      } else {
        completeLogin(data);
      }
    } catch (err) {
      error.textContent = err.message;
      error.classList.remove("lg-success");
    } finally {
      button.disabled = false;
    }
  });
  document.getElementById("otp-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = document.getElementById("otp-submit");
    const error = document.getElementById("otp-error");
    error.textContent = "";
    error.classList.remove("lg-success");
    button.disabled = true;
    try {
      const data = await api("/api/auth/verify-otp", {
        method: "POST",
        json: { email: state.otpEmail, code: document.getElementById("otp-code").value.trim() },
      });
      completeLogin(data);
    } catch (err) {
      error.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  });
  document.getElementById("otp-resend").addEventListener("click", async () => {
    const button = document.getElementById("otp-resend");
    const error = document.getElementById("otp-error");
    button.disabled = true;
    error.textContent = "";
    error.classList.remove("lg-success");
    try {
      await api("/api/auth/resend-otp", { method: "POST", json: { email: state.otpEmail } });
      error.textContent = "A new verification code was sent.";
      error.classList.add("lg-success");
    } catch (err) {
      error.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  });
  document.getElementById("otp-change-email").addEventListener("click", () => {
    document.getElementById("otp-form").hidden = true;
    document.getElementById("login-form").hidden = false;
    document.getElementById("otp-code").value = "";
    document.getElementById("login-error").textContent = "";
    document.getElementById("login-email").focus();
    state.otpEmail = "";
  });
  document.body.addEventListener("click", async (event) => {
    const nav = event.target.closest(".nav-btn");
    if (nav) setPage(nav.dataset.page);
    if (event.target.id === "menu-btn") {
      document.getElementById("sidebar").classList.add("open");
      document.getElementById("nav-backdrop").classList.add("show");
    }
    if (event.target.id === "nav-backdrop") {
      document.getElementById("sidebar").classList.remove("open");
      document.getElementById("nav-backdrop").classList.remove("show");
    }
    if (event.target.id === "logout") {
      try {
        await api("/api/auth/logout", { method: "POST", json: {} });
      } catch (error) {
        if (error.status !== 401) toast(error.message, "error");
      }
      clearSession();
      document.getElementById("login-password").value = "";
      stopCamera();
      showLogin();
    }
  });

  document.body.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const card = event.target.closest(".def-click[role='button']");
    const row = event.target.closest("#dist-list [role='button']");
    const chart = event.target.closest("#chart-days[role='button']");
    if (card) {
      event.preventDefault();
      card.click();
    } else if (row) {
      event.preventDefault();
      row.click();
    } else if (chart) {
      event.preventDefault();
      chart.click();
    }
  });

  document.getElementById("btn-cam").onclick = () => {
    if (state.stream || document.getElementById("btn-cam").textContent === "Stop scan") {
      stopCamera();
      showStage("idle");
    } else {
      if (typeof startSelectedCamera === "function") startSelectedCamera();
      else startCamera();
    }
  };
  document.getElementById("btn-capture").onclick = () => captureFrame();
  document.getElementById("btn-remove-upload").addEventListener("click", () => {
    if (!state.uploadedPhoto) return;
    state.uploadedPhoto = false;
    state.returnToLiveAfterUpload = false;
    if (typeof stopCamera === "function") stopCamera();
    const canvas = document.getElementById("snap");
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    canvas.width = 0;
    canvas.height = 0;
    showStage("idle");
  });
  document.getElementById("file-input").addEventListener("change", () => {
    const file = document.getElementById("file-input").files[0];
    document.getElementById("file-input").value = "";
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) {
      toast("Image must be 10 MB or smaller.", "error");
      return;
    }
    const image = new Image();
    const url = URL.createObjectURL(file);
    image.onload = async () => {
      const canvas = document.getElementById("snap");
      state.returnToLiveAfterUpload = Boolean(state.stream) || (typeof liveOn !== "undefined" && liveOn);
      state.uploadedPhoto = true;
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      canvas.getContext("2d").drawImage(image, 0, 0);
      showStage("still");
      URL.revokeObjectURL(url);
      await classifyBlob(file, file.name || "upload.jpg");
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      toast("Could not read that image. Use JPEG, PNG, or WebP.", "error");
    };
    image.src = url;
  });
  const tallyReset = document.getElementById("tally-reset");
  if (tallyReset) tallyReset.onclick = () => {
    state.session = { total: 0, byClass: {}, msSum: 0 };
    renderTally();
  };

  document.getElementById("rec-q").addEventListener("input", () => {
    clearTimeout(state.records.searchTimer);
    state.records.searchTimer = setTimeout(() => {
      state.records.page = 1;
      loadRecords();
    }, 300);
  });
  document.getElementById("rec-class").onchange = () => { state.records.page = 1; loadRecords(); };
  document.getElementById("rec-correct").onchange = () => { state.records.page = 1; loadRecords(); };
  document.getElementById("rec-reset").onclick = resetRecordFilters;
  document.getElementById("rec-no-matches-reset").onclick = resetRecordFilters;
  document.getElementById("rec-refresh").onclick = () => loadRecords();
  document.getElementById("rec-retry").onclick = () => loadRecords();
  document.getElementById("rec-page-size").onchange = (event) => {
    state.records.pageSize = Number(event.target.value) || 8;
    state.records.page = 1;
    loadRecords();
  };
  ["rec-actual", "rec-from", "rec-to"].forEach((id) => {
    const field = document.getElementById(id);
    if (!field) return;
    field.addEventListener("change", () => {
      state.records.page = 1;
      loadRecords();
    });
  });
  document.getElementById("rec-export").onclick = async () => {
    try {
      const exportParams = new URLSearchParams({
        q: document.getElementById("rec-q").value.trim(),
        class_name: document.getElementById("rec-class").value,
        correctness: document.getElementById("rec-correct").value,
        actual_name: document.getElementById("rec-actual") ? document.getElementById("rec-actual").value : "",
        date_from: document.getElementById("rec-from") ? document.getElementById("rec-from").value : "",
        date_to: document.getElementById("rec-to") ? document.getElementById("rec-to").value : "",
      });
      const response = await fetch("/api/scans/export?" + exportParams.toString(), { credentials: "same-origin" });
      if (response.status === 401) {
        clearSession();
        showLogin();
        toast("Session expired. Please sign in again.", "error");
        return;
      }
      if (!response.ok) throw new Error("Could not export the records.");
      const blob = await response.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "eggscan-records.csv";
      link.click();
      URL.revokeObjectURL(link.href);
      toast("CSV downloaded.", "ok");
    } catch (error) {
      toast(error.message, "error");
    }
  };
  document.getElementById("report-print").onclick = () => window.print();

  document.getElementById("rec-body").addEventListener("click", async (event) => {
    const remove = event.target.closest(".js-delete");
    if (remove) {
      const yes = await openModal({ title: "Delete scan", body: "Remove scan #" + remove.dataset.id + " and its image?", okLabel: "Delete" });
      if (!yes) return;
      try {
        await api("/api/scans/" + remove.dataset.id, { method: "DELETE" });
        toast("Scan deleted.", "ok");
        await loadRecords();
      } catch (error) {
        if (error.status !== 401) toast(error.message, "error");
      }
    }
  });

  document.querySelectorAll("[data-delete-images]").forEach((button) => {
    button.addEventListener("click", async () => {
      const key = button.dataset.deleteImages;
      const label = key.charAt(0).toUpperCase() + key.slice(1);
      const yes = await openModal({
        title: "Delete images",
        body: "Delete the uploaded " + label + " images? Their length marks are removed too.",
        okLabel: "Delete images",
      });
      if (!yes) return;
      button.disabled = true;
      try {
        const result = await api("/api/dataset/class/" + encodeURIComponent(key), { method: "DELETE" });
        const input = document.querySelector('#data-form input[data-class="' + key + '"]');
        if (input) input.value = "";
        const modal = document.getElementById("annotate-modal");
        if (modal && !modal.hidden && modal.dataset.annotateClass === key && typeof annotateClose === "function") annotateClose();
        renderIncoming(result);
        toast(result.message, "ok");
      } catch (error) {
        if (error.status !== 401) toast(error.message, "error");
        button.disabled = false;
      }
    });
  });

  document.querySelectorAll("#data-form input[data-class]").forEach((input) => {
    input.addEventListener("change", async () => {
      const files = Array.from(input.files || []);
      input.value = "";
      if (!files.length) return;
      const folder = files[0].webkitRelativePath ? files[0].webkitRelativePath.split(/[/\\]/)[0] : "";
      const tooBig = files.find((file) => file.size > 10 * 1024 * 1024);
      if (tooBig) {
        toast(tooBig.name + " is larger than 10 MB.", "error");
        return;
      }
      const body = new FormData();
      body.append("class_name", input.dataset.class);
      if (folder) body.append("folder_name", folder);
      files.forEach((file) => body.append("files", file, file.webkitRelativePath || file.name));
      try {
        const result = await api("/api/dataset/class", { method: "POST", body });
        renderIncoming(result);
        toast(result.message, result.class_valid ? "ok" : "error");
      } catch (error) {
        if (error.status !== 401) toast(error.message, "error");
      }
    });
  });
  document.getElementById("data-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const trainPct = Number(document.getElementById("data-train-pct").value);
    const valPct = Number(document.getElementById("data-val-pct").value);
    const testPct = Number(document.getElementById("data-test-pct").value);
    if (trainPct + valPct + testPct !== 100) {
      toast("Train, validation, and test percents must add up to 100.", "error");
      return;
    }
    const button = document.getElementById("data-process");
    button.disabled = true;
    const status = document.getElementById("data-status");
    const job = document.getElementById("data-job");
    if (job) job.hidden = false;
    if (status) status.textContent = "Preparing the dataset.";
    try {
      const result = await api("/api/dataset/prepare", {
        method: "POST",
        json: { train_pct: trainPct, val_pct: valPct, test_pct: testPct, seed: Number(document.getElementById("data-seed").value || 42) },
      });
      renderDatasetJob(result);
      toast(result.status, result.ready ? "ok" : "error");
      if (result.ready) await loadDataset();
    } catch (error) {
      if (error.status !== 401) {
        if (status) status.textContent = error.message;
        const steps = document.getElementById("data-steps");
        if (steps) steps.innerHTML = `<li class="bad">${esc(error.message)}</li>`;
        toast("Dataset validation failed. Review the preparation details.", "error");
      }
    } finally {
      const incoming = await api("/api/dataset/incoming").catch(() => null);
      if (incoming) renderIncoming(incoming);
      else button.disabled = false;
    }
  });
  document.getElementById("data-train").onclick = async () => {
    const button = document.getElementById("data-train");
    button.disabled = true;
    try {
      const result = await api("/api/dataset/train", { method: "POST", json: {} });
      renderTrain(result);
      pollTraining();
    } catch (error) {
      if (error.status !== 401) toast(error.message, "error");
      button.disabled = false;
    }
  };
  document.getElementById("data-use").onclick = async () => {
    try {
      const settings = await api("/api/settings");
      await api("/api/settings", {
        method: "PUT",
        json: {
          confidence: settings.confidence,
          pixels_per_mm: settings.pixels_per_mm,
          classification_mode: "dimensions",
          camera_index: settings.camera_index,
          out_of_range: settings.out_of_range || "nearest",
        },
      });
      toast("The trained egg detector is on. Size classes use calibrated length and width.", "ok");
    } catch (error) {
      if (error.status !== 401) toast(error.message, "error");
    }
  };
  document.getElementById("log-level").onchange = () => {
    state.logs.page = 1;
    loadLogs();
  };
  const reloadLogsFromFilter = () => {
    state.logs.page = 1;
    loadLogs();
  };
  ["log-category", "log-from", "log-to"].forEach((id) => {
    document.getElementById(id).addEventListener("change", reloadLogsFromFilter);
  });
  ["log-search", "log-actor"].forEach((id) => {
    document.getElementById(id).addEventListener("input", () => {
      clearTimeout(state.logs.searchTimer);
      state.logs.searchTimer = setTimeout(reloadLogsFromFilter, 320);
    });
  });
  document.getElementById("logs-reset").onclick = resetLogFilters;
  document.getElementById("logs-empty-reset").onclick = resetLogFilters;
  document.getElementById("logs-refresh").onclick = () => loadLogs();
  document.getElementById("logs-retry").onclick = () => loadLogs();
  document.getElementById("log-body").addEventListener("click", (event) => {
    const open = event.target.closest("[data-log-open]");
    if (open) openLogDetails(open.dataset.logOpen);
  });
  document.getElementById("log-details-close").onclick = () => document.getElementById("log-details-dialog").close();

  const clearDialog = document.getElementById("log-clear-dialog");
  const clearConfirm = document.getElementById("log-clear-confirm");
  const clearCancel = document.getElementById("log-clear-cancel");
  const clearError = document.getElementById("logs-clear-error");
  document.getElementById("log-clear").onclick = () => {
    clearError.hidden = true;
    clearError.textContent = "";
    clearCancel.disabled = false;
    clearConfirm.disabled = false;
    clearConfirm.textContent = "Clear all logs";
    clearDialog.showModal();
  };
  clearCancel.onclick = () => clearDialog.close();
  clearDialog.addEventListener("cancel", (event) => {
    if (clearConfirm.disabled) event.preventDefault();
  });
  clearConfirm.onclick = async () => {
    if (clearConfirm.disabled) return;
    clearConfirm.disabled = true;
    clearCancel.disabled = true;
    clearConfirm.textContent = "Clearing…";
    clearError.hidden = true;
    try {
      const result = await api("/api/logs", { method: "DELETE" });
      if (!result || result.ok !== true) throw new Error("The server did not confirm that logs were cleared.");
      clearDialog.close();
      toast(`${Number(result.deleted_count) || 0} previous log entries cleared. The action was recorded.`, "ok");
      state.logs.page = 1;
      await loadLogs();
    } catch (error) {
      if (error.status === 401) return;
      clearError.textContent = error.message || "Could not clear logs. No success was confirmed.";
      clearError.hidden = false;
      clearConfirm.disabled = false;
      clearCancel.disabled = false;
      clearConfirm.textContent = "Try again";
    }
  };
}

async function boot() {
  bind();
  clearSession();
  startEgg();
  try {
    const csrfResponse = await fetch("/api/auth/csrf", { credentials: "same-origin", cache: "no-store" });
    if (!csrfResponse.ok) throw new Error("Could not initialize the sign-in security token.");
    const csrfData = await csrfResponse.json();
    state.csrfToken = csrfData.csrf_token || "";
    const me = await api("/api/auth/me", { silentAuth: true });
    state.email = me.email;
    state.mock = !!me.mock_mode;
    showApp();
    setPage(state.page || "overview");
  } catch (error) {
    showLogin();
  }
}

boot();
