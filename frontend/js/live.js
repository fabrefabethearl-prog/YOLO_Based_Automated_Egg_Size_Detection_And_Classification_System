/* Continuous live scan. Detection starts with the camera; Save scan stores one egg. */

let liveOn = false;
let liveGen = 0;
let liveControl = null;
let liveSmooth = null;
let liveGoneAt = 0;
let liveHistory = [];
let liveLast = null;
let liveBlob = null;
let liveSaved = false;
let liveSaving = false;
let liveSavedId = null;
let liveFrameTimes = [];
let liveErrorAt = 0;
let liveTracks = [];
let liveNextId = 1;
let liveSource = "computer";
let liveNoEggTimer = 0;
let phonePeer = null;
let phoneSocket = null;
let phoneRemoteCandidates = [];
let phoneLoopGen = -1;
let phoneIceRestart = false;
let phonePairTimer = 0;
let phonePairPending = false;
let phonePairRequestGen = -1;
const liveGrab = document.createElement("canvas");
const LIVE_NO_EGG_DELAY_MS = 500;

function livePrepareLayout() {
  const view = document.getElementById("live-view");
  if (!view || view.dataset.enhanced === "true") return;
  view.dataset.enhanced = "true";
  document.getElementById("live-no-egg")?.remove();
  const indicator = document.createElement("div");
  indicator.id = "live-indicator";
  indicator.className = "live-indicator";
  indicator.setAttribute("role", "status");
  indicator.setAttribute("aria-live", "polite");
  indicator.innerHTML = '<span class="live-indicator-dot" aria-hidden="true"></span><span id="live-indicator-text">Position 1 egg within the camera view to measure its size.</span>';
  const overlay = document.createElement("canvas");
  overlay.id = "live-detection-overlay";
  overlay.setAttribute("aria-hidden", "true");
  view.append(overlay, indicator);
  const cameraCard = document.getElementById("stage")?.closest("article");
  const controls = cameraCard?.querySelector(":scope > .button-row");
  const panel = document.getElementById("result-panel");
  if (cameraCard && controls && panel) {
    panel.classList.add("live-results");
    const status = document.getElementById("live-status");
    const reason = document.getElementById("live-reason");
    const count = document.getElementById("live-count");
    const distribution = document.getElementById("live-summary");
    const geometry = document.getElementById("live-geometry");
    const heading = document.getElementById("live-class");
    const metrics = panel.querySelector(".result-grid");
    if (status && reason && count && distribution && heading && metrics && geometry) {
      const head = document.createElement("div");
      head.className = "live-results-head";
      const headText = document.createElement("div");
      headText.append(status, reason);
      head.append(headText, count);
      panel.insertBefore(head, panel.firstChild);
      panel.insertBefore(distribution, head.nextSibling);
      const individualResults = document.createElement("ul");
      individualResults.id = "live-individual-results";
      individualResults.className = "live-individual-results";
      individualResults.setAttribute("aria-live", "polite");
      panel.insertBefore(individualResults, distribution.nextSibling);
      const current = document.createElement("div");
      current.className = "live-current-result";
      const currentLabel = document.createElement("div");
      currentLabel.innerHTML = '<span class="live-field-label">Current egg</span>';
      currentLabel.appendChild(heading);
      current.append(currentLabel, metrics);
      panel.insertBefore(current, geometry);
      geometry.classList.add("live-geometry");
    }

  }
}

function liveIndicator(text, stateName) {
  const node = document.getElementById("live-indicator-text");
  const box = document.getElementById("live-indicator");
  if (node) node.textContent = text;
  if (box) box.dataset.state = stateName || "ready";
}

function liveUpdateIndicator(data) {
  if (!liveOn) {
    liveIndicator("Position 1 egg within the camera view to measure its size.", "ready");
    return;
  }
  if (!data) {
    liveIndicator(liveSource === "esp32" ? "Connecting to ESP32 camera…" : "Connecting to camera…", "connecting");
    return;
  }
  const detections = liveDetections(data);
  if (!detections.length) {
    liveIndicator("No egg detected — position an egg within the camera view.", "empty");
  } else if (detections.length > 1) {
    liveIndicator(detections.length + " eggs detected — measurements shown for each egg.", "detected");
  } else if (data.length_mm != null && data.width_mm != null &&
      Number.isFinite(Number(data.length_mm)) && Number.isFinite(Number(data.width_mm)) && data["class"]) {
    liveIndicator(data["class"] + " egg detected", "complete");
  } else {
    liveIndicator("Egg detected — calculating size…", "detected");
  }
}

function liveDelay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function liveMedian(values) {
  const sorted = values.filter((value) => Number.isFinite(value)).slice().sort((a, b) => a - b);
  if (!sorted.length) return null;
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function liveText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
}

function liveStatus(text) {
  const node = document.getElementById("live-status");
  if (!node) return;
  node.textContent = text;
  node.classList.toggle("is-stable", text === "Stable");
  node.classList.toggle("is-saved", text === "Saved" || text === "Remove egg");
  node.classList.toggle("is-scanning", text === "Scanning…");
}

function liveClearNoEgg() {
  if (liveNoEggTimer) clearTimeout(liveNoEggTimer);
  liveNoEggTimer = 0;
  const notice = document.getElementById("live-no-egg");
  if (notice) notice.hidden = true;
}

function liveUpdateNoEgg(data) {
  const notice = document.getElementById("live-no-egg");
  const hasDetection = !!(data && (data.detected || liveDetections(data).length));
  if (!notice || !liveOn || hasDetection || !data || data.detected !== false) {
    liveClearNoEgg();
    return;
  }
  const hasNoEggResult = data.egg_count === 0 &&
    Array.isArray(data.detections) &&
    data.detections.length === 0 &&
    (data.reason === "no egg" || data.reason === "low confidence");
  if (!hasNoEggResult) {
    liveClearNoEgg();
    return;
  }
  if (liveNoEggTimer) return;
  const source = liveSource;
  liveNoEggTimer = setTimeout(() => {
    liveNoEggTimer = 0;
    const latest = liveLast;
    const latestHasDetection = !!(latest && (latest.detected || liveDetections(latest).length));
    if (liveOn && liveSource === source && latest && latest.detected === false &&
        latest.egg_count === 0 && Array.isArray(latest.detections) && latest.detections.length === 0 &&
        (latest.reason === "no egg" || latest.reason === "low confidence") && !latestHasDetection) {
      notice.hidden = false;
    }
  }, LIVE_NO_EGG_DELAY_MS);
}

function livePipeline(data) {
  const root = document.getElementById("def-pipeline");
  if (!root) return;
  const detected = !!(data && data.detected);
  const placed = !!(data && data.bbox);
  const steps = [
    { label: "Egg placed", status: placed ? "done" : "pending" },
    { label: "Image captured", status: data ? "done" : "pending" },
    {
      label: "YOLO detects",
      status: detected ? "done" : data ? "fail" : "pending",
      reason: detected ? "" : data && data.reason,
      ms: data && data.inference_ms,
    },
    { label: "Size estimated", status: detected ? "done" : "pending" },
    { label: "Class assigned", status: detected ? "done" : "pending" },
    { label: "Result displayed", status: detected ? "done" : "pending" },
  ];
  root.innerHTML = steps.map((step) => {
    const cls = step.status === "done" ? "is-done" : step.status === "fail" ? "is-fail" : "";
    const extra = step.ms == null ? "" : "<small>" + esc(num(step.ms, 1, " ms")) + "</small>";
    const reason = step.status === "fail" && step.reason ? "<small>" + esc(step.reason) + "</small>" : "";
    return '<div class="def-step ' + cls + '"><b>' + esc(step.label) + "</b>" + extra + reason + "</div>";
  }).join("");
}

function liveReading() {
  const length = liveMedian(liveHistory.map((item) => item.length));
  const width = liveMedian(liveHistory.map((item) => item.width));
  const classes = liveHistory.map((item) => item.className);
  const unit = liveHistory.every((item) => item.unit === "mm") ? "mm" : "px";
  const stable = unit === "mm" && liveHistory.length >= 7 && classes.every((name) => name && name === classes[0]);
  return {
    length,
    width,
    unit,
    className: stable ? classes[0] : (liveLast && liveLast["class"]) || null,
    stable,
  };
}

function liveModelMismatchMessage(data) {
  if (!data || data.classification_reason !== "unmapped model class") return "";
  const classId = data.model_class_id == null ? "unknown" : data.model_class_id;
  const modelClass = data.model_class || "unnamed";
  return `Trained model class ID ${classId} ("${modelClass}") does not match the configured egg-size classes. Size classification is unavailable; use a model trained with the five size classes.`;
}

function liveShowReading(reading, data) {
  const outside = data && data.reason && data.reason.indexOf("Out of range") === 0;
  if (outside) {
    liveStatus(data.reason);
    liveText("live-class", data.reason);
    const node = document.getElementById("live-class");
    if (node) node.style.color = "";
    liveText("live-size", data.length_mm == null ? "—" : num(data.length_mm, 1) + " × " + num(data.width_mm, 1) + " mm");
    liveText("live-conf", "—");
    liveText("live-ms", data.inference_ms == null ? "—" : num(data.inference_ms, 1, " ms"));
    liveText("live-reason", data.reason);
    liveText("live-geometry", "");
    return;
  }
  const label = data && data.classification_reason === "unmapped model class"
    ? "Unmapped model class"
    : reading.className || "—";
  if (typeof paintClass === "function") paintClass("live-class", label);
  else liveText("live-class", label);
  liveText("live-size", reading.length == null ? "—" : num(reading.length, 1) + " × " + num(reading.width, 1) + " " + (reading.unit || data.unit || "mm"));
  liveText("live-conf", data && data.confidence != null && data.detected ? (pct(data.confidence) || "—") : "—");
  liveText("live-ms", data && data.inference_ms != null ? num(data.inference_ms, 1, " ms") : "—");
  const measured = data && Array.isArray(data.detections) && data.detections.some((item) => item.length_source === "measured");
  const explanation = liveModelMismatchMessage(data) ||
    (measured ? "Class from each egg's measured length."
      : data && data.reason === "calibration missing" ? "Set pixels per mm to classify by physical size or save scans." : "");
  liveText("live-reason", data && !data.detected ? (data.reason || "") : explanation);
  const geometry = data && data.geometry;
  const geometryText = geometry
    ? (geometry.source === "contour" ? "Contour fit" : "Bounding-box estimate") +
      " · major × minor " + num(geometry.major_axis_px, 1) + " × " + num(geometry.minor_axis_px, 1) +
      (geometry.ellipse_major_px == null ? "" : " · ellipse " + num(geometry.ellipse_major_px, 1) + " × " + num(geometry.ellipse_minor_px, 1) + " px") +
      " px · aspect " + num(geometry.aspect_ratio, 2) +
      (geometry.length_mm == null ? "" : " · " + num(geometry.length_mm, 1) + " × " + num(geometry.width_mm, 1) + " mm") +
      (geometry.area_px == null ? "" : " · area " + num(geometry.area_px, 0) + " px²")
    : data && data.detected ? "Contour fit unavailable; showing the model box." : "";
  liveText("live-geometry", geometryText);
}

function liveClearCard() {
  liveStatus(liveOn ? "Scanning…" : "Scan stopped");
  liveText("live-reason", "");
  liveText("live-class", "—");
  liveText("live-size", "—");
  liveText("live-conf", "—");
  liveText("live-ms", "—");
  liveText("live-geometry", "");
  liveRenderSummary();
  const verify = document.getElementById("live-verify");
  if (verify) verify.hidden = true;
}

function liveIou(a, b) {
  const left = Math.max(a.x, b.x);
  const top = Math.max(a.y, b.y);
  const right = Math.min(a.x + a.w, b.x + b.w);
  const bottom = Math.min(a.y + a.h, b.y + b.h);
  const overlap = Math.max(0, right - left) * Math.max(0, bottom - top);
  const union = a.w * a.h + b.w * b.h - overlap;
  return union <= 0 ? 0 : overlap / union;
}

function liveDetections(data) {
  const rows = data && Array.isArray(data.detections) ? data.detections : [];
  const ready = rows.filter((item) => item && item.bbox);
  if (ready.length) return ready;
  if (data && data.detected && data.bbox) {
    return [{
      class: data.class,
      model_class: data.model_class,
      confidence: data.confidence,
      bbox: data.bbox,
      length_mm: data.length_mm,
      width_mm: data.width_mm,
      length_px: data.length_px,
      width_px: data.width_px,
      geometry: data.geometry,
      measurement_source: data.measurement_source,
    }];
  }
  return [];
}

function liveAssociate(detections) {
  const used = new Set();
  const next = [];
  liveTracks.forEach((track) => {
    let best = -1;
    let bestScore = 0.3;
    detections.forEach((item, index) => {
      if (used.has(index)) return;
      const score = liveIou(track, item.bbox);
      if (score > bestScore) {
        best = index;
        bestScore = score;
      }
    });
    if (best < 0) return;
    used.add(best);
    const item = detections[best];
    next.push({
      id: track.id,
      x: item.bbox.x,
      y: item.bbox.y,
      w: item.bbox.w,
      h: item.bbox.h,
      className: item.class || (item.classification_reason === "unmapped model class" ? "Unmapped model class" : null),
      confidence: item.confidence,
      length: item.length_mm,
      width: item.width_mm,
      lengthPx: item.length_px,
      widthPx: item.width_px,
      geometry: item.geometry,
      measurementSource: item.measurement_source,
    });
  });
  detections.forEach((item, index) => {
    if (used.has(index)) return;
    next.push({
      id: liveNextId++,
      x: item.bbox.x,
      y: item.bbox.y,
      w: item.bbox.w,
      h: item.bbox.h,
      className: item.class || (item.classification_reason === "unmapped model class" ? "Unmapped model class" : null),
      confidence: item.confidence,
      length: item.length_mm,
      width: item.width_mm,
      lengthPx: item.length_px,
      widthPx: item.width_px,
      geometry: item.geometry,
      measurementSource: item.measurement_source,
    });
  });
  liveTracks = next;
}

function liveRenderSummary() {
  const count = document.getElementById("live-count");
  if (count) count.textContent = "Detected eggs: " + liveTracks.length;
  const list = document.getElementById("live-summary");
  if (!list) return;
  list.innerHTML = (state.categories || []).map((category) => category.name).map((name) => {
    const group = liveTracks.filter((track) => track.className === name);
    const average = group.length
      ? group.reduce((sum, track) => sum + Number(track.confidence || 0), 0) / group.length
      : null;
    const shown = average == null ? "—" : (pct(average) || "—");
    return "<li><span>" + esc(name) + "</span><b>" + group.length + "</b><span>" + shown + "</span></li>";
  }).join("");
  const individuals = document.getElementById("live-individual-results");
  if (individuals) individuals.innerHTML = liveTracks.map((track, index) => {
    const classLabel = track.className || "Class not recognized";
    const unit = track.length != null && track.width != null ? "mm" : "px";
    const size = track.length == null || track.width == null
      ? "Measurements unavailable"
      : num(track.length, 1) + " x " + num(track.width, 1) + " " + unit;
    const confidence = track.confidence == null ? "" : pct(track.confidence);
    return "<li><b>Egg " + (index + 1) + "</b><span>" + esc(classLabel) + "</span><small>" + esc(size) + (confidence ? " | " + esc(confidence) : "") + "</small></li>";
  }).join("");
}

function liveHud(data) {
  const hud = document.getElementById("live-hud");
  if (!hud || !data) return;
  const now = performance.now();
  liveFrameTimes.push(now);
  liveFrameTimes = liveFrameTimes.filter((stamp) => now - stamp <= 1000);
  hud.hidden = false;
  hud.textContent = liveFrameTimes.length + " fps · " + num(data.inference_ms, 0) + " ms";
}

function liveTally(data) {
  if (!window.eggscanSession || !window.eggscanSession.active) return;
  state.session.total += 1;
  state.session.byClass[data.predicted_class] = (state.session.byClass[data.predicted_class] || 0) + 1;
  state.session.msSum = (state.session.msSum || 0) + Number(data.inference_ms || 0);
  renderTally();
}

async function liveVerifyForm(data) {
  const box = document.getElementById("live-verify");
  const select = document.getElementById("live-actual-class");
  if (!box || !select || !data.id) return;
  await ensureCategories();
  select.innerHTML = classOptions(data.actual_class || data.predicted_class || "");
  box.hidden = false;
  box.open = false;
  const button = document.getElementById("live-verify-save");
  button.onclick = async () => {
    const chosen = select.value;
    if (!chosen) {
      toast("Choose a size class.", "error");
      return;
    }
    try {
      await api("/api/scans/" + data.id, { method: "PATCH", json: { actual_class: chosen } });
      toast("Actual size class saved.", "ok");
      loadOverview().catch(() => {});
    } catch (error) {
      if (error.status !== 401) toast(error.message, "error");
    }
  };
}

async function saveLive() {
  if (!liveLast || !liveLast.detected || !liveBlob) {
    toast(liveLast && liveLast.reason ? liveLast.reason : "No egg in the frame.", "error");
    return;
  }
  if (liveSaved) {
    toast("This egg is already saved. Remove it before the next scan.", "ok");
    return;
  }
  if (liveSaving) return;
  liveSaving = true;
  const button = document.getElementById("btn-capture");
  if (button) button.disabled = true;
  try {
    const reading = liveReading();
    const body = new FormData();
    body.append("file", liveBlob, "frame.jpg");
    if (reading.length != null) body.append("length_mm", String(Math.round(reading.length * 100) / 100));
    if (reading.width != null) body.append("width_mm", String(Math.round(reading.width * 100) / 100));
    if (liveLast.confidence != null) body.append("confidence", String(liveLast.confidence));
    if (window.eggscanSession && window.eggscanSession.active && window.eggscanSession.id) {
      body.append("session_id", window.eggscanSession.id);
    }
    const data = await api("/api/scan", { method: "POST", body });
    if (!data.saved) {
      toast(data.message || "Not saved", "error");
      return;
    }
    liveSaved = true;
    liveSavedId = data.id;
    liveStatus("Saved");
    toast(data.message, "ok");
    liveTally(data);
    await liveVerifyForm(data);
    loadOverview().catch(() => {});
  } catch (error) {
    if (error.name !== "AbortError" && error.status !== 401) toast(error.message, "error");
  } finally {
    liveSaving = false;
    if (button) button.disabled = false;
  }
}

function liveOnPreview(data) {
  liveLast = data;
  liveUpdateNoEgg(data);
  liveUpdateIndicator(data);
  liveHud(data);
  const detections = liveDetections(data);
  liveAssociate(detections);
  liveDrawDetections();
  liveRenderSummary();
  if (liveTracks.length > 1) {
    liveGoneAt = 0;
    liveSmooth = null;
    liveHistory = [];
    livePipeline(Object.assign({}, data, { detected: true, bbox: data.bbox || liveTracks[0] }));
    liveStatus("Scanning…");
    liveText("live-class", liveTracks.length + " eggs");
    liveText("live-size", "—");
    liveText("live-conf", "—");
    liveText("live-ms", data.inference_ms == null ? "—" : num(data.inference_ms, 1, " ms"));
    const mismatch = liveModelMismatchMessage(data);
    liveText("live-reason", mismatch);
    liveText("live-geometry", "Measurements are shown beside each detected egg.");
    return;
  }
  livePipeline(data);
  if (liveTracks.length === 1 && data.detected && data.bbox) {
    liveGoneAt = 0;
    liveSmooth = {
      x: liveTracks[0].x,
      y: liveTracks[0].y,
      w: liveTracks[0].w,
      h: liveTracks[0].h,
    };
    liveHistory.push({
      length: data.length_mm == null ? data.length_px : data.length_mm,
      width: data.width_mm == null ? data.width_px : data.width_mm,
      unit: data.unit || (data.length_mm == null ? "px" : "mm"),
      className: data["class"],
    });
    if (liveHistory.length > 7) liveHistory.shift();
    const reading = liveReading();
    liveShowReading(reading, data);
    if (liveSaved) liveStatus("Remove egg");
    else if (reading.stable) {
      liveStatus("Stable");
      saveLive();
    } else liveStatus("Scanning…");
    return;
  }
  liveSmooth = null;
  liveHistory = [];
  liveGoneAt = 0;
  const outside = data.reason && data.reason.indexOf("Out of range") === 0;
  if (outside) {
    liveShowReading({ length: data.length_mm, width: data.width_mm, className: null }, data);
    return;
  }
  liveText("live-reason", data.reason || "");
  if (liveOn) liveStatus("Scanning…");
  liveShowReading({ length: null, width: null, className: null }, data);
}

function liveMedia() {
  return document.getElementById(liveSource === "esp32" ? "esp-view" : "cam");
}

function liveFrameSize(media) {
  if (!media) return { width: 0, height: 0 };
  return {
    width: media.videoWidth || media.naturalWidth || 0,
    height: media.videoHeight || media.naturalHeight || 0,
  };
}

function liveDrawDetections() {
  const canvas = document.getElementById("live-detection-overlay");
  if (!canvas) return;
  const view = document.getElementById("live-view");
  const rect = view ? view.getBoundingClientRect() : null;
  const width = rect ? rect.width : 0;
  const height = rect ? rect.height : 0;
  const ratio = Math.max(1, window.devicePixelRatio || 1);
  const targetWidth = Math.max(1, Math.round(width * ratio));
  const targetHeight = Math.max(1, Math.round(height * ratio));
  if (canvas.width !== targetWidth || canvas.height !== targetHeight) {
    canvas.width = targetWidth;
    canvas.height = targetHeight;
  }
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  if (!liveOn || !width || !height || !liveTracks.length) return;

  const media = liveMedia();
  const source = liveFrameSize(media);
  if (!source.width || !source.height) return;
  const scale = Math.max(width / source.width, height / source.height);
  const offsetX = (width - source.width * scale) / 2;
  const offsetY = (height - source.height * scale) / 2;
  const mirrored = liveSource === "computer" && !!document.getElementById("cam")?.classList.contains("is-mirror");

  context.lineWidth = 2;
  context.strokeStyle = "#baff32";
  context.fillStyle = "#baff32";
  context.font = "600 13px Inter, system-ui, sans-serif";
  context.textBaseline = "middle";
  liveTracks.forEach((track) => {
    let left = track.x * source.width * scale + offsetX;
    const top = track.y * source.height * scale + offsetY;
    const boxWidth = track.w * source.width * scale;
    const boxHeight = track.h * source.height * scale;
    if (mirrored) left = width - left - boxWidth;

    context.setLineDash([]);
    context.strokeRect(left, top, boxWidth, boxHeight);
    const ellipse = track.geometry && track.geometry.ellipse;
    if (ellipse) {
      let centerX = ellipse.center_x * source.width * scale + offsetX;
      const centerY = ellipse.center_y * source.height * scale + offsetY;
      if (mirrored) centerX = width - centerX;
      context.save();
      context.strokeStyle = "rgba(186, 255, 50, .94)";
      context.setLineDash([6, 4]);
      context.beginPath();
      context.ellipse(
        centerX,
        centerY,
        Math.max(2, ellipse.major_ratio * source.width * scale / 2),
        Math.max(2, ellipse.minor_ratio * source.width * scale / 2),
        (mirrored ? -1 : 1) * Number(ellipse.angle_deg || 0) * Math.PI / 180,
        0,
        Math.PI * 2
      );
      context.stroke();
      context.restore();
    }

    const unit = track.length != null && track.width != null ? "mm" : "px";
    const length = track.length != null ? track.length : track.lengthPx;
    const measuredWidth = track.width != null ? track.width : track.widthPx;
    const size = length != null && measuredWidth != null
      ? Number(length).toFixed(1) + " x " + Number(measuredWidth).toFixed(1) + " " + unit
      : "size pending";
    const label = (track.className || "Egg") + " ? " + size;
    const labelWidth = context.measureText(label).width + 16;
    const labelX = Math.max(0, Math.min(width - labelWidth, left));
    const labelY = top >= 28 ? top - 13 : Math.min(height - 14, top + boxHeight + 14);
    context.fillStyle = "rgba(12, 32, 25, .92)";
    context.fillRect(labelX, labelY - 12, labelWidth, 24);
    context.fillStyle = "#d7ff72";
    context.fillText(label, labelX + 8, labelY);
  });
}

function liveGrabFrame(video) {
  const width = 640;
  const height = Math.max(1, Math.round(video.videoHeight * (width / video.videoWidth)));
  liveGrab.width = width;
  liveGrab.height = height;
  liveGrab.getContext("2d").drawImage(video, 0, 0, width, height);
  return new Promise((resolve) => liveGrab.toBlob(resolve, "image/jpeg", 0.7));
}

function liveWaitFrame(video) {
  return new Promise((resolve) => {
    if (!liveOn) {
      resolve();
      return;
    }
    if (typeof video.requestVideoFrameCallback === "function") video.requestVideoFrameCallback(() => resolve());
    else requestAnimationFrame(() => resolve());
  });
}

async function liveLoop(gen) {
  const video = document.getElementById("cam");
  while (liveOn && gen === liveGen && (liveSource === "computer" || liveSource === "phone") && state.stream) {
    await liveWaitFrame(video);
    if (!liveOn || gen !== liveGen || (liveSource !== "computer" && liveSource !== "phone")) break;
    if (!video.videoWidth) continue;
    const blob = await liveGrabFrame(video);
    if (!blob || !liveOn || gen !== liveGen || (liveSource !== "computer" && liveSource !== "phone")) break;
    const started = performance.now();
    liveControl = new AbortController();
    const body = new FormData();
    body.append("file", blob, "frame.jpg");
    try {
      const data = await api("/api/scan/preview", { method: "POST", body, signal: liveControl.signal });
      if (!liveOn || gen !== liveGen || (liveSource !== "computer" && liveSource !== "phone")) break;
      liveBlob = blob;
      liveOnPreview(data);
    } catch (error) {
      if (!liveOn || gen !== liveGen || (liveSource !== "computer" && liveSource !== "phone")) break;
      liveClearNoEgg();
      if (error.name === "AbortError") break;
      liveIndicator(error.message || "Inference is temporarily unavailable.", "error");
      if (error.status !== 429 && error.status !== 401 && performance.now() - liveErrorAt > 4000) {
        liveErrorAt = performance.now();
        toast(error.message, "error");
      }
    }
    const elapsed = performance.now() - started;
    if (liveOn && elapsed < 100) await liveDelay(100 - elapsed);
  }
}

function haltLive() {
  liveOn = false;
  liveGen += 1;
  if (liveControl) liveControl.abort();
  liveControl = null;
}

function stopCamera() {
  haltLive();
  if (phonePairTimer) clearInterval(phonePairTimer);
  phonePairTimer = 0;
  phonePairPending = false;
  liveClearNoEgg();
  if (phoneSocket && phoneSocket.readyState === WebSocket.OPEN) {
    try { phoneSocket.send(JSON.stringify({ type: "disconnect" })); } catch (error) { /* socket is closing */ }
  }
  if (phoneSocket) phoneSocket.close();
  phoneSocket = null;
  if (phonePeer) phonePeer.close();
  phonePeer = null;
  phoneRemoteCandidates = [];
  phoneIceRestart = false;
  const pairPanel = document.getElementById("phone-pair-panel");
  if (pairPanel) pairPanel.hidden = true;
  const espButton = document.getElementById("btn-esp");
  if (espButton) espButton.disabled = false;
  if (state.stream) {
    state.stream.getTracks().forEach((track) => track.stop());
    state.stream = null;
  }
  const video = document.getElementById("cam");
  if (video) {
    video.srcObject = null;
    video.hidden = false;
  }
  const button = document.getElementById("btn-cam");
  if (button) button.textContent = "Start scan";
  liveSmooth = null;
  liveHistory = [];
  liveTracks = [];
  liveDrawDetections();
  liveNextId = 1;
  liveLast = null;
  liveBlob = null;
  liveSaved = false;
  liveGoneAt = 0;
  liveFrameTimes = [];
  liveSource = "computer";
  const esp = document.getElementById("esp-view");
  if (esp) {
    if (esp.src && esp.src.startsWith("blob:")) URL.revokeObjectURL(esp.src);
    esp.removeAttribute("src");
    esp.hidden = true;
  }
  const hud = document.getElementById("live-hud");
  if (hud) {
    hud.hidden = true;
    hud.textContent = "";
  }
  showStage("idle");
  liveClearCard();
  livePipeline(null);
  liveUpdateIndicator(null);
  if (window.eggscanCameraChanged) window.eggscanCameraChanged();
}

async function startCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    toast("This browser cannot open a camera. Upload an image instead.", "error");
    return;
  }
  stopCamera();
  liveSource = "computer";
  const gen = liveGen;
  let settings = { camera_index: 0 };
  try {
    settings = await api("/api/settings");
  } catch (error) {
    if (gen !== liveGen) return;
  }
  if (gen !== liveGen) return;
  let stream;
  try {
    const probe = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    probe.getTracks().forEach((track) => track.stop());
    if (gen !== liveGen) return;
    const devices = await navigator.mediaDevices.enumerateDevices();
    if (gen !== liveGen) return;
    const cameras = devices.filter((device) => device.kind === "videoinput");
    const chosen = cameras[settings.camera_index] || cameras[0];
    const video = chosen
      ? { deviceId: { exact: chosen.deviceId }, width: { ideal: 1280 }, height: { ideal: 720 } }
      : { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } };
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
    } catch (deviceError) {
      if (!chosen || gen !== liveGen) throw deviceError;
      // Retry with the browser default if the selected device is stale or busy.
      stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    }
    if (gen !== liveGen) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
  } catch (error) {
    if (gen !== liveGen) return;
    liveStatus("Scan stopped");
    const cameraMessage = error.name === "NotAllowedError"
      ? "Camera permission was denied. Allow camera access in the browser settings, then try again."
      : error.name === "NotReadableError"
        ? "The camera is busy in another app. Close the other app and try again."
        : "The camera could not be opened. Check that it is connected and try again.";
    liveText("live-reason", cameraMessage);
    toast(cameraMessage + " You can still upload an image.", "error");
    return;
  }
  state.stream = stream;
  const video = document.getElementById("cam");
  video.srcObject = state.stream;
  video.hidden = false;
  const esp = document.getElementById("esp-view");
  if (esp) esp.hidden = true;
  video.classList.add("is-mirror");
  showStage("live");
  document.getElementById("btn-cam").textContent = "Stop scan";
  liveOn = true;
  liveUpdateIndicator(null);
  liveStatus("Scanning…");
  if (window.eggscanCameraChanged) window.eggscanCameraChanged();
  liveLoop(liveGen);
}

async function captureFrame() {
  const media = liveMedia();
  const ready = liveSource === "esp32" ? liveFrameSize(media).width : state.stream && document.getElementById("cam").videoWidth;
  if (!ready) {
    toast("Start the camera first, or upload an image.", "error");
    return;
  }
  await saveLive();
}

async function loadEspAddress() {
  try {
    const data = await api("/api/camera/esp");
    const input = document.getElementById("esp-ip");
    if (input && data.ip) input.value = data.ip;
  } catch (error) {
    if (error.status === 401) throw error;
  }
}

async function startEsp() {
  const input = document.getElementById("esp-ip");
  const address = input ? input.value.trim() : "";
  const button = document.getElementById("btn-esp");
  if (button && button.disabled) return;
  stopCamera();
  const gen = liveGen;
  liveSource = "esp32";
  const source = document.getElementById("camera-source");
  if (source) source.value = "esp32";
  liveUpdateIndicator(null);
  const video = document.getElementById("cam");
  const esp = document.getElementById("esp-view");
  if (video) video.hidden = true;
  if (esp) esp.hidden = true;
  if (!address) {
    liveStatus("Scan stopped");
    liveText("live-reason", "Enter the ESP32 IP address.");
    toast("Enter the ESP32 IP address.", "error");
    return;
  }
  if (button) button.disabled = true;
  try {
    const saved = await api("/api/camera/esp", { method: "POST", json: { ip: address } });
    if (gen !== liveGen || liveSource !== "esp32") return;
    if (input) input.value = saved.ip;
  } catch (error) {
    if (gen !== liveGen || liveSource !== "esp32") return;
    liveStatus("Scan stopped");
    liveText("live-reason", error.message || "ESP32-CAM was not found.");
    if (error.status !== 401) toast(error.message, "error");
    if (button) button.disabled = false;
    return;
  }
  if (button) button.disabled = false;
  if (esp) {
    esp.hidden = false;
    esp.classList.remove("is-mirror");
  }
  showStage("live");
  if (video) video.hidden = true;
  document.getElementById("btn-cam").textContent = "Stop scan";
  liveOn = true;
  liveUpdateIndicator(null);
  liveStatus("Scanning…");
  liveText("live-reason", "");
  if (window.eggscanCameraChanged) window.eggscanCameraChanged();
  liveLoopEsp(gen);
}

function phonePairStatus(message) {
  const status = document.getElementById("phone-pair-status");
  if (status) status.textContent = message;
  liveText("live-reason", message);
}

async function phoneIcePath(peer) {
  try {
    const stats = await peer.getStats();
    for (const report of stats.values()) {
      if (report.type !== "candidate-pair" || report.state !== "succeeded" || !(report.selected || report.nominated)) continue;
      const local = stats.get(report.localCandidateId);
      const remote = stats.get(report.remoteCandidateId);
      return local?.candidateType === "relay" || remote?.candidateType === "relay" ? "TURN relay" : "direct peer-to-peer";
    }
  } catch (error) {
    // Route details are diagnostic only; video can still work without them.
  }
  return null;
}

function phoneSessionEnded(message) {
  const source = document.getElementById("camera-source");
  stopCamera();
  if (source) source.value = "phone";
  const panel = document.getElementById("phone-pair-panel");
  const image = document.getElementById("phone-pair-qr");
  const link = document.getElementById("phone-pair-link");
  const renew = document.getElementById("phone-pair-renew");
  if (panel) panel.hidden = false;
  if (image) image.removeAttribute("src");
  if (link) { link.removeAttribute("href"); link.textContent = ""; }
  if (renew) renew.disabled = false;
  phonePairStatus(message);
}

async function startPhoneCamera() {
  if (phonePairPending) return;
  stopCamera();
  phonePairPending = true;
  const renew = document.getElementById("phone-pair-renew");
  if (renew) renew.disabled = true;
  liveSource = "phone";
  const gen = liveGen;
  phonePairRequestGen = gen;
  const source = document.getElementById("camera-source");
  if (source) source.value = "phone";
  liveStatus("Waiting for phone");
  liveUpdateIndicator(null);
  liveText("live-reason", "Create a temporary pairing link, then open it on your phone.");
  showStage("live");
  const button = document.getElementById("btn-cam");
  if (button) button.textContent = "Stop scan";
  liveOn = true;
  const panel = document.getElementById("phone-pair-panel");
  if (panel) panel.hidden = false;
  const image = document.getElementById("phone-pair-qr");
  const link = document.getElementById("phone-pair-link");
  if (image) image.removeAttribute("src");
  if (link) { link.removeAttribute("href"); link.textContent = ""; }
  phonePairStatus("Creating a secure, temporary pairing code...");
  try {
    const pair = await api("/api/camera/phone/pair", { method: "POST", json: {} });
    if (!liveOn || gen !== liveGen || liveSource !== "phone") return;
    if (image) image.src = pair.qr_data_url;
    if (link) { link.href = pair.url; link.textContent = pair.url; }
    if (renew) renew.disabled = false;
    const expiresAt = Date.now() + Number(pair.expires_in || 300) * 1000;
    const updateExpiry = () => {
      if (!liveOn || gen !== liveGen || liveSource !== "phone") return;
      const remaining = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
      if (!remaining) {
        phoneSessionEnded("This QR code expired. Generate a new QR code to reconnect.");
        return;
      }
      const minutes = Math.floor(remaining / 60);
      const seconds = String(remaining % 60).padStart(2, "0");
      const relayNote = pair.turn_available
        ? ""
        : " TURN relay is not configured; some mobile or restrictive networks may fail to connect.";
      phonePairStatus("Scan the QR code with your phone, then tap Start Sharing. It expires in " + minutes + ":" + seconds + "." + relayNote);
    };
    updateExpiry();
    phonePairTimer = setInterval(updateExpiry, 1000);
    const peer = new RTCPeerConnection({ iceServers: pair.ice_servers || [] });
    phonePeer = peer;
    peer.addTransceiver("video", { direction: "recvonly" });
    peer.onicecandidate = (event) => {
      if (event.candidate && phoneSocket && phoneSocket.readyState === WebSocket.OPEN) {
        phoneSocket.send(JSON.stringify({ type: "candidate", candidate: event.candidate.toJSON() }));
      }
    };
    peer.ontrack = (event) => {
      if (gen !== liveGen || liveSource !== "phone") return;
      const video = document.getElementById("cam");
      state.stream = event.streams[0];
      video.srcObject = state.stream;
      video.hidden = false;
      video.classList.remove("is-mirror");
      video.addEventListener("loadeddata", () => {
        if (gen === liveGen && liveSource === "phone") phonePairStatus("Phone video frames are arriving. Egg detection is running.");
      }, { once: true });
      video.play().catch(() => {});
      liveStatus("Scanning...");
      phonePairStatus("Phone media track received. Waiting for video frames...");
      if (phoneLoopGen !== gen) {
        phoneLoopGen = gen;
        liveLoop(gen).finally(() => { if (phoneLoopGen === gen) phoneLoopGen = -1; });
      }
    };
    peer.onconnectionstatechange = async () => {
      if (gen !== liveGen || liveSource !== "phone") return;
      if (peer.connectionState === "connected") {
        liveStatus("Scanning...");
        const activePeer = peer;
        const path = await phoneIcePath(activePeer);
        if (gen !== liveGen || liveSource !== "phone" || phonePeer !== activePeer) return;
        phonePairStatus("WebRTC connected" + (path ? " via " + path : "") + ". Waiting for phone video frames...");
      }
      if (peer.connectionState === "failed" || peer.connectionState === "disconnected") {
        liveStatus("Phone disconnected");
        phonePairStatus("Phone connection interrupted. Check the internet path and TURN relay configuration, then reconnect if needed.");
      }
    };
    peer.oniceconnectionstatechange = async () => {
      if (peer.iceConnectionState === "failed") {
        phonePairStatus("WebRTC could not find a route between the devices. Check STUN/TURN configuration and network access.");
      }
      if (peer.iceConnectionState !== "failed" || phoneIceRestart || !phoneSocket || phoneSocket.readyState !== WebSocket.OPEN) return;
      phoneIceRestart = true;
      try {
        const offer = await peer.createOffer({ iceRestart: true });
        await peer.setLocalDescription(offer);
        phoneSocket.send(JSON.stringify({ type: "offer", description: peer.localDescription }));
      } catch (error) {
        phonePairStatus("Automatic reconnect failed. Check TURN configuration, then create a new pairing code.");
      } finally {
        phoneIceRestart = false;
      }
    };
    const wsScheme = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(wsScheme + "//" + location.host + "/api/camera/phone/signal");
    phoneSocket = ws;
    ws.onopen = async () => {
      if (gen !== liveGen || liveSource !== "phone") return;
      ws.send(JSON.stringify({ type: "join", role: "computer", session_id: pair.session_id }));
      const offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      ws.send(JSON.stringify({ type: "offer", description: peer.localDescription }));
    };
    ws.onmessage = async (event) => {
      if (gen !== liveGen || liveSource !== "phone") return;
      let message;
      try { message = JSON.parse(event.data); } catch (error) { return; }
      if (message.type === "peer_joined") {
        if (phonePairTimer) clearInterval(phonePairTimer);
        phonePairTimer = 0;
        phonePairStatus("Phone joined. Establishing the camera connection...");
      }
      if (message.type === "answer" && message.description) {
        await peer.setRemoteDescription(message.description);
        for (const candidate of phoneRemoteCandidates.splice(0)) await peer.addIceCandidate(candidate);
      }
      if (message.type === "candidate" && message.candidate) {
        if (peer.remoteDescription && peer.signalingState !== "have-local-offer") await peer.addIceCandidate(message.candidate);
        else phoneRemoteCandidates.push(message.candidate);
      }
      if (message.type === "disconnect") {
        phoneSessionEnded("Phone disconnected. Select Start scan to create a new pairing code.");
      }
    };
    ws.onerror = () => {
      if (liveOn && gen === liveGen) phonePairStatus("Could not connect to the signaling service. Check HTTPS and the local network.");
    };
    ws.onclose = () => {
      if (liveOn && gen === liveGen && liveSource === "phone") {
        phoneSessionEnded("Phone signaling ended. Select Start scan to create a new pairing code.");
      }
    };
  } catch (error) {
    if (gen !== liveGen || liveSource !== "phone") return;
    liveStatus("Phone camera unavailable");
    phonePairStatus(error.message || "Phone pairing could not be started.");
    if (renew) renew.disabled = false;
    toast(error.message || "Phone pairing could not be started.", "error");
  } finally {
    if (phonePairRequestGen === gen) phonePairPending = false;
  }
}

function startSelectedCamera() {
  const selected = document.getElementById("camera-source")?.value || "computer";
  if (selected === "esp32") return startEsp();
  if (selected === "phone") return startPhoneCamera();
  return startCamera();
}

async function liveLoopEsp(gen) {
  const img = document.getElementById("esp-view");
  while (liveOn && gen === liveGen && liveSource === "esp32") {
    const started = performance.now();
    const control = new AbortController();
    liveControl = control;
    try {
      const response = await secureFetch("/api/camera/esp/frame", {
        signal: control.signal,
      });
      if (!response.ok) {
        let message = "ESP32-CAM was not found.";
        try {
          const body = await response.json();
          if (body.detail) message = body.detail;
        } catch (parseError) { /* keep the fallback */ }
        const error = new Error(message);
        error.status = response.status;
        throw error;
      }
      const blob = await response.blob();
      if (!liveOn || gen !== liveGen || liveSource !== "esp32") break;
      if (!img) break;
      const url = URL.createObjectURL(blob);
      const previous = img.src;
      img.src = url;
      await img.decode();
      if (!liveOn || gen !== liveGen || liveSource !== "esp32") {
        URL.revokeObjectURL(url);
        break;
      }
      if (previous && previous.startsWith("blob:")) URL.revokeObjectURL(previous);
      const body = new FormData();
      body.append("file", blob, "frame.jpg");
      const data = await api("/api/scan/preview", { method: "POST", body, signal: control.signal });
      if (!liveOn || gen !== liveGen || liveSource !== "esp32") break;
      liveBlob = blob;
      liveOnPreview(data);
    } catch (error) {
      if (!liveOn || gen !== liveGen || liveSource !== "esp32") break;
      liveClearNoEgg();
      if (error.name === "AbortError") break;
      if (img && img.src && img.src.startsWith("blob:")) URL.revokeObjectURL(img.src);
      if (img) img.removeAttribute("src");
      liveTracks = [];
      liveDrawDetections();
      liveSmooth = null;
      liveHistory = [];
      liveLast = null;
      liveClearCard();
      livePipeline(null);
      liveText("live-reason", error.message);
      liveIndicator(error.message, "error");
      if (error.status !== 429 && error.status !== 401 && performance.now() - liveErrorAt > 4000) {
        liveErrorAt = performance.now();
        toast(error.message, "error");
      }
    }
    const elapsed = performance.now() - started;
    if (liveOn && elapsed < 200) await liveDelay(200 - elapsed);
  }
}

livePrepareLayout();
liveRenderSummary();
document.getElementById("btn-cam-hero").onclick = () => startCamera();
document.getElementById("btn-esp").onclick = () => startEsp();
document.getElementById("btn-cam").onclick = () => {
  if (liveOn) stopCamera();
  else startSelectedCamera();
};
document.getElementById("btn-cam-hero").onclick = () => startSelectedCamera();
document.getElementById("camera-source").onchange = () => {
  const selected = document.getElementById("camera-source").value;
  if (liveOn || selected === "phone") startSelectedCamera();
};
document.getElementById("phone-pair-close").onclick = () => {
  if (liveOn && liveSource === "phone") stopCamera();
  else document.getElementById("phone-pair-panel").hidden = true;
};
document.getElementById("phone-pair-renew").onclick = () => startPhoneCamera();
document.getElementById("phone-pair-copy").onclick = async () => {
  const link = document.getElementById("phone-pair-link");
  if (!link || !link.href || link.href.endsWith("#")) return;
  try {
    await navigator.clipboard.writeText(link.href);
    toast("Phone pairing link copied.", "ok");
  } catch (error) {
    toast("Copy the pairing link from the link above.", "error");
  }
};
