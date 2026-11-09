/* Draw an oval around each egg in an uploaded image. Coordinates are stored in the original image. */

const ANNOTATE_SLUG = {
  small: "small",
  medium: "medium",
  large: "large",
  "extra large": "extra_large",
  jumbo: "jumbo",
};

let annotate = {
  key: "",
  slug: "",
  name: "",
  images: [],
  index: 0,
  ovals: [],
  scale: 0,
  selected: -1,
  draft: null,
  drag: null,
  savedCount: 0,
  savedJson: "[]",
  lastSnapshot: "[]",
  reviewed: false,
  savedReviewed: false,
  url: "",
  dirty: false,
  referenceClass: "",
  referenceLabelSource: "",
  detectionWarnings: [],
  busy: false,
  showRequest: 0,
};

function annotateRefreshControls() {
  const busy = annotate.busy;
  ["annotate-close", "annotate-auto", "annotate-reviewed", "annotate-reset", "annotate-prev", "annotate-save", "annotate-delete", "annotate-next"]
    .forEach((id) => {
      const button = document.getElementById(id);
      if (button) button.disabled = busy;
    });
  const reset = document.getElementById("annotate-reset");
  if (reset && !busy) reset.disabled = !annotate.savedCount;
  const image = document.getElementById("annotate-image");
  if (image) image.style.pointerEvents = busy ? "none" : "";
  const canvas = document.getElementById("annotate-canvas");
  if (canvas) canvas.style.pointerEvents = busy ? "none" : "";
  const select = document.getElementById("annotate-reference-class");
  if (select) select.disabled = busy;
}

async function annotateExclusive(action, buttonId, busyLabel) {
  if (annotate.busy) return;
  annotate.busy = true;
  const button = buttonId && document.getElementById(buttonId);
  const originalLabel = button && button.textContent;
  if (button && busyLabel) button.textContent = busyLabel;
  annotateRefreshControls();
  try {
    await action();
  } catch (error) {
    if (error.status !== 401) toast(error.message || "The annotation action failed.", "error");
  } finally {
    if (button) {
      if (buttonId === "annotate-save") button.textContent = annotate.savedCount ? "Update annotation" : "Save annotation";
      else if (buttonId === "annotate-reviewed") button.textContent = annotate.reviewed ? "Reviewed ✓" : "Mark reviewed";
      else button.textContent = originalLabel;
    }
    annotate.busy = false;
    annotateRefreshControls();
  }
}

function annotateSlug(key) {
  return ANNOTATE_SLUG[key] || key;
}

function annotateOpen(key) {
  if (annotate.busy) return;
  annotate.key = key;
  annotate.slug = annotateSlug(key);
  annotate.index = 0;
  const modal = document.getElementById("annotate-modal");
  modal.hidden = false;
  modal.dataset.annotateClass = key;
  document.getElementById("annotate-title").textContent = "Annotate: " + (key.charAt(0).toUpperCase() + key.slice(1));
  annotateExclusive(annotateLoadList);
}

async function annotateLoadList() {
  try {
    const settings = await api("/api/settings");
    annotate.scale = Number(settings.pixels_per_mm) > 0 ? Number(settings.pixels_per_mm) : 0;
  } catch (error) {
    annotate.scale = 0;
    if (error.status === 401) throw error;
    toast("Camera scale could not be loaded. Enter verified ruler measurements for each oval.", "error");
  }
  const scaleNote = document.getElementById("annotate-scale");
  if (scaleNote) {
    scaleNote.textContent = annotate.scale > 0
      ? "Click an egg to refine its contour, or drag to add an oval. Physical measurements use the " + annotate.scale + " px/mm camera scale when valid; pixel annotations can still be saved for YOLO."
      : "Click an egg to refine its contour, or drag to add an oval. Camera scale is unavailable; pixel annotations can still be saved for YOLO. Calibrate to record millimetres.";
  }
  const data = await api("/api/dataset/annotate/" + annotate.slug);
  annotate.images = data.images || [];
  if (data.class) document.getElementById("annotate-title").textContent = "Annotate: " + data.class;
  if (!annotate.images.length) {
    annotateClear();
    document.getElementById("annotate-progress").textContent = "No images in this class yet.";
    document.getElementById("annotate-state").textContent = "Not annotated";
    return;
  }
  if (annotate.index >= annotate.images.length) annotate.index = annotate.images.length - 1;
  await annotateShow();
  if (!annotate.savedCount) await annotateAuto();
}

function annotateClear() {
  annotate.showRequest += 1;
  const image = document.getElementById("annotate-image");
  const canvas = document.getElementById("annotate-canvas");
  if (annotate.url) URL.revokeObjectURL(annotate.url);
  annotate.url = "";
  annotate.ovals = [];
  annotate.selected = -1;
  annotate.draft = null;
  annotate.drag = null;
  annotate.savedCount = 0;
  annotate.savedJson = "[]";
  annotate.lastSnapshot = "[]";
  annotate.dirty = false;
  annotate.reviewed = false;
  annotate.savedReviewed = false;
  annotate.referenceClass = "";
  annotate.referenceLabelSource = "";
  annotate.detectionWarnings = [];
  document.getElementById("annotate-reference-class").value = "";
  document.getElementById("annotate-reset").disabled = true;
  document.getElementById("annotate-reference-source").textContent =
    "Filename label is separate from the dataset folder class and is not a verified physical size.";
  document.getElementById("annotate-measurements").hidden = true;
  if (image) image.removeAttribute("src");
  const empty = document.getElementById("annotate-empty");
  if (empty) empty.hidden = annotate.images.length > 0;
  if (canvas) {
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
}

function annotateFromMark(mark) {
  const source = Array.isArray(mark.ovals) && mark.ovals.length ? mark.ovals : [mark];
  return source.filter((item) => item && item.measurement).map((item) => ({
    x1: item.measurement.start_x,
    y1: item.measurement.start_y,
    x2: item.measurement.end_x,
    y2: item.measurement.end_y,
    length_mm: item.length_mm == null ? "" : item.length_mm,
    width_mm: item.width_mm == null ? "" : item.width_mm,
    calibration: item.calibration || "manual",
    geometry: item.geometry || null,
  }));
}

function annotateSnapshot() {
  return JSON.stringify({
    ovals: annotate.ovals.map((oval) => ({
      x1: oval.x1,
      y1: oval.y1,
      x2: oval.x2,
      y2: oval.y2,
      length_mm: oval.length_mm || "",
      width_mm: oval.width_mm || "",
      calibration: oval.calibration || "manual",
      geometry: oval.geometry || null,
    })),
    reference_class: annotate.referenceClass || null,
    reference_label_source: annotate.referenceLabelSource || null,
  });
}

function annotateMarkDirty() {
  const snapshot = annotateSnapshot();
  if (snapshot !== annotate.lastSnapshot) annotate.reviewed = false;
  annotate.lastSnapshot = snapshot;
  annotate.dirty = snapshot !== annotate.savedJson || annotate.reviewed !== annotate.savedReviewed;
  document.getElementById("annotate-reviewed").textContent = annotate.reviewed ? "Reviewed ✓" : "Mark reviewed";
  annotateStatus();
}

async function annotateShow() {
  const item = annotate.images[annotate.index];
  if (!item) return false;
  const request = ++annotate.showRequest;
  const empty = document.getElementById("annotate-empty");
  const response = await secureFetch("/api/dataset/annotate/" + annotate.slug + "/" + encodeURIComponent(item.name));
  if (!response.ok) throw new Error("That image could not be opened.");
  const blob = await response.blob();
  let mark = null;
  try {
    mark = await api("/api/dataset/annotate/" + annotate.slug + "/" + encodeURIComponent(item.name) + "/mark");
  } catch (error) {
    if (error.status !== 404) throw error;
  }
  if (request !== annotate.showRequest) return false;
  if (empty) empty.hidden = true;
  document.getElementById("annotate-progress").textContent = "Image " + (annotate.index + 1) + " of " + annotate.images.length;
  if (annotate.url) URL.revokeObjectURL(annotate.url);
  annotate.url = URL.createObjectURL(blob);
  const image = document.getElementById("annotate-image");
  const imageReady = new Promise((resolve, reject) => {
    image.onload = () => {
      if (request === annotate.showRequest) annotateFit();
      resolve();
    };
    image.onerror = () => reject(new Error("That image could not be decoded for annotation."));
  });
  image.src = annotate.url;
  await imageReady;
  if (request !== annotate.showRequest) return false;
  requestAnimationFrame(annotateFit);
  annotate.ovals = [];
  annotate.selected = -1;
  annotate.draft = null;
  annotate.savedCount = 0;
  annotate.dirty = false;
  annotate.reviewed = false;
  annotate.savedReviewed = false;
  annotate.referenceClass = "";
  annotate.referenceLabelSource = "";
  let savedReviewed = Boolean(mark && mark.reviewed === true);
  annotate.detectionWarnings = [];
  if (mark) {
    annotate.ovals = annotateFromMark(mark);
    annotate.savedCount = annotate.ovals.length;
    annotate.selected = annotate.ovals.length - 1;
    annotate.referenceClass = mark.reference_label && mark.reference_label.class_name || "";
    annotate.referenceLabelSource = mark.reference_label && mark.reference_label.source || "";
    if (annotate.ovals.some((oval) => oval.calibration === "pixels")) {
      annotate.detectionWarnings = ["Pixel outline saved for YOLO. Physical millimetre measurements are unavailable until camera calibration is valid."];
    }
  }
  if (!annotate.referenceClass) {
    annotate.referenceClass = annotateFilenameLabel(item.name) || "";
    annotate.referenceLabelSource = annotate.referenceClass ? "filename" : "";
  }
  document.getElementById("annotate-reference-class").value = annotate.referenceClass;
  annotateUpdateReferenceLabel();
  annotate.reviewed = savedReviewed;
  annotate.savedReviewed = savedReviewed;
  annotateUpdateMeasurements();
  annotate.savedJson = annotateSnapshot();
  annotate.lastSnapshot = annotate.savedJson;
  annotate.dirty = false;
  document.getElementById("annotate-save").textContent = annotate.savedCount ? "Update annotation" : "Save annotation";
  document.getElementById("annotate-auto").disabled = false;
  document.getElementById("annotate-reset").disabled = !annotate.savedCount;
  document.getElementById("annotate-reviewed").textContent = annotate.reviewed ? "Reviewed ✓" : "Mark reviewed";
  annotateStatus();
  annotatePaint();
  return true;
}

function annotateFit() {
  const image = document.getElementById("annotate-image");
  const canvas = document.getElementById("annotate-canvas");
  const stage = document.getElementById("annotate-stage");
  const frame = image && image.parentElement;
  if (!image || !canvas || !image.naturalWidth) return;
  const availableWidth = stage.clientWidth;
  const availableHeight = stage.clientHeight;
  if (!availableWidth || !availableHeight) return;
  // Size the image itself to the available stage, including upscaling small
  // photos. The canvas shares the exact same frame so clicks and outlines stay aligned.
  const fit = Math.min(availableWidth / image.naturalWidth, availableHeight / image.naturalHeight);
  const width = Math.max(1, Math.round(image.naturalWidth * fit));
  const height = Math.max(1, Math.round(image.naturalHeight * fit));
  frame.style.width = width + "px";
  frame.style.height = height + "px";
  canvas.width = width;
  canvas.height = height;
  annotatePaint();
}

function annotatePoint(event, clampToImage = false) {
  const image = document.getElementById("annotate-image");
  const rect = image.getBoundingClientRect();
  if (!rect.width || !rect.height) return { x: 0, y: 0 };
  if (!clampToImage && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) return null;
  const clientX = Math.max(rect.left, Math.min(rect.right, event.clientX));
  const clientY = Math.max(rect.top, Math.min(rect.bottom, event.clientY));
  const x = (clientX - rect.left) * (image.naturalWidth / rect.width);
  const y = (clientY - rect.top) * (image.naturalHeight / rect.height);
  return {
    x: Math.max(0, Math.min(image.naturalWidth - 1, x)),
    y: Math.max(0, Math.min(image.naturalHeight - 1, y)),
  };
}

function annotateSides(box) {
  if (!box) return null;
  const wide = Math.abs(box.x2 - box.x1);
  const tall = Math.abs(box.y2 - box.y1);
  if (wide < 2 || tall < 2) return null;
  return { length: Math.max(wide, tall), width: Math.min(wide, tall) };
}

function annotateValidOval(oval) {
  const image = document.getElementById("annotate-image");
  if (!image || !oval) return false;
  const values = [oval.x1, oval.y1, oval.x2, oval.y2].map(Number);
  if (!values.every(Number.isFinite)) return false;
  const [x1, y1, x2, y2] = values;
  const left = Math.min(x1, x2);
  const right = Math.max(x1, x2);
  const top = Math.min(y1, y2);
  const bottom = Math.max(y1, y2);
  const withinImage = !image.naturalWidth || !image.naturalHeight ||
    (left >= 0 && top >= 0 && right <= image.naturalWidth && bottom <= image.naturalHeight);
  return withinImage &&
    Math.abs(x2 - x1) >= 2 && Math.abs(y2 - y1) >= 2;
}

function annotateFillFields() {
  document.getElementById("annotate-reset").disabled = !annotate.savedCount;
}

function annotateApplyFields() {
  const oval = annotate.ovals[annotate.selected];
  if (!oval) return;
  const sides = annotateContourMeasurements(oval) || annotateSides(oval);
  if (sides && annotate.scale > 0) {
    oval.length_mm = Number((sides.length / annotate.scale).toFixed(1));
    oval.width_mm = Number((sides.width / annotate.scale).toFixed(1));
    oval.calibration = "scale";
  } else {
    oval.length_mm = oval.length_mm != null && Number.isFinite(Number(oval.length_mm)) ? Number(oval.length_mm) : "";
    oval.width_mm = oval.width_mm != null && Number.isFinite(Number(oval.width_mm)) ? Number(oval.width_mm) : "";
    oval.calibration = "manual";
  }
  annotateMarkDirty();
}

function annotateFilenameLabel(filename) {
  const name = String(filename || "").replaceAll("\\", "/").split("/").pop() || "";
  const stem = name.replace(/\.[^.]*$/, "");
  const tokens = stem.toLowerCase().split(/[\s_-]+/);
  const classes = ["small", "medium", "large", "jumbo"];
  const matches = [];
  for (let index = 0; index < tokens.length; index += 1) {
    if (tokens[index] === "extra" && tokens[index + 1] === "large") {
      matches.push("Extra Large");
      index += 1;
    } else if (classes.includes(tokens[index])) {
      matches.push(tokens[index][0].toUpperCase() + tokens[index].slice(1));
    }
  }
  return matches.length === 1 ? matches[0] : null;
}

function annotateContourMeasurements(oval) {
  const geometry = oval && oval.geometry;
  const ellipse = geometry && (geometry.ellipse || geometry);
  const points = geometry && geometry.contour;
  if (!ellipse || !Array.isArray(points) || points.length < 3) return null;
  const angle = Number(ellipse.angle_deg) * Math.PI / 180;
  const major = [Math.cos(angle), Math.sin(angle)];
  const minor = [-major[1], major[0]];
  const centerX = Number(ellipse.center_x);
  const centerY = Number(ellipse.center_y);
  const along = [];
  const across = [];
  points.forEach(([x, y]) => {
    const dx = Number(x) - centerX;
    const dy = Number(y) - centerY;
    along.push(dx * major[0] + dy * major[1]);
    across.push(dx * minor[0] + dy * minor[1]);
  });
  return {
    length: Math.max(...along) - Math.min(...along),
    width: Math.max(...across) - Math.min(...across),
  };
}

function annotateUpdateReferenceLabel() {
  const select = document.getElementById("annotate-reference-class");
  const status = document.getElementById("annotate-reference-source");
  const filenameLabel = annotateFilenameLabel(annotate.images[annotate.index] && annotate.images[annotate.index].name);
  const selected = select ? select.value : "";
  annotate.referenceClass = selected;
  annotate.referenceLabelSource = selected && selected === filenameLabel ? "filename" : selected ? "manual" : "";
  if (status) {
    const datasetName = annotate.key
      .split(" ")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");
    if (!selected) {
      status.textContent = "Choose Small, Medium, Large, Extra Large, or Jumbo. The filename did not provide one unambiguous reference class.";
    } else if (annotate.referenceLabelSource === "filename") {
      status.textContent = `Reference label: ${selected} (from filename). Dataset folder label: ${datasetName}. Verify mismatches; labels are not physical-size verification.`;
    } else {
      status.textContent = `Reference label: ${selected} (manually assigned). Dataset folder label: ${datasetName}. This is not a verified physical size.`;
    }
  }
  annotateMarkDirty();
}

function annotateUpdateMeasurements() {
  const section = document.getElementById("annotate-measurements");
  const grid = document.getElementById("annotate-measurement-grid");
  const warning = document.getElementById("annotate-detection-warning");
  const oval = annotate.ovals[annotate.selected];
  const geometry = oval && oval.geometry;
  if (!section || !grid) return;
  if (!geometry) {
    section.hidden = annotate.detectionWarnings.length === 0;
    grid.replaceChildren();
    if (warning) {
      warning.hidden = annotate.detectionWarnings.length === 0;
      warning.textContent = annotate.detectionWarnings.join(" ");
    }
    return;
  }
  section.hidden = false;
  grid.replaceChildren();
  const ellipse = geometry.ellipse || (geometry.center_x == null ? null : geometry);
  if (!ellipse) {
    const note = document.createElement("p");
    note.textContent = "Automatic geometry is unavailable for this oval. Drag to redraw it or enter its measurements.";
    grid.appendChild(note);
    if (warning) {
      warning.hidden = annotate.detectionWarnings.length === 0;
      warning.textContent = annotate.detectionWarnings.join(" ");
    }
    return;
  }
  const projected = annotateContourMeasurements(oval);
  const metrics = [
    ["Contour length", projected ? projected.length : null, "px"],
    ["Contour width", projected ? projected.width : null, "px"],
  ];
  metrics.forEach(([label, value, unit]) => {
    const cell = document.createElement("div");
    const title = document.createElement("span");
    const content = document.createElement("strong");
    title.textContent = label;
    content.textContent = value == null
      ? "—"
      : typeof value === "number"
        ? `${value.toFixed(1)} ${unit}`
        : `${value} ${unit}`;
    cell.append(title, content);
    grid.appendChild(cell);
  });
  const warnings = [...annotate.detectionWarnings];
  if (warning) {
    warning.hidden = warnings.length === 0;
    warning.textContent = warnings.join(" ");
  }
}

function annotateHit(point) {
  for (let index = annotate.ovals.length - 1; index >= 0; index -= 1) {
    const oval = annotate.ovals[index];
    const rx = Math.abs(oval.x2 - oval.x1) / 2;
    const ry = Math.abs(oval.y2 - oval.y1) / 2;
    if (rx < 1 || ry < 1) continue;
    const nx = (point.x - (oval.x1 + oval.x2) / 2) / rx;
    const ny = (point.y - (oval.y1 + oval.y2) / 2) / ry;
    if (nx * nx + ny * ny <= 1) return index;
  }
  return -1;
}

function annotateStatus() {
  const total = annotate.ovals.length;
  const selected = annotate.ovals[annotate.selected];
  const sides = annotateSides(annotate.draft) || annotateSides(selected);
  const pixels = document.getElementById("annotate-pixels");
  if (annotate.draft && sides) {
    pixels.textContent = "Drawing · " + sides.length.toFixed(1) + " × " + sides.width.toFixed(1) + " px";
  } else if (total && sides) {
    pixels.textContent = total + (total === 1 ? " oval · " : " ovals · ") + sides.length.toFixed(1) + " × " + sides.width.toFixed(1) + " px";
  } else if (total) {
    pixels.textContent = total + (total === 1 ? " oval" : " ovals");
  } else {
    pixels.textContent = "Oval: —";
  }
  const state = document.getElementById("annotate-state");
  if (!total) state.textContent = "Not annotated";
  else if (!annotate.reviewed) state.textContent = total + (total === 1 ? " oval · review required" : " ovals · review required");
  else if (annotate.dirty) state.textContent = total + (total === 1 ? " oval · not saved" : " ovals · not saved");
  else state.textContent = "Reviewed ✓";
}

function annotateDrawOval(ctx, box, sx, sy, selected, number) {
  const geometry = box.geometry;
  if (geometry && Array.isArray(geometry.contour) && geometry.contour.length > 2) {
    ctx.beginPath();
    geometry.contour.forEach(([x, y], index) => {
      if (index === 0) ctx.moveTo(x * sx, y * sy);
      else ctx.lineTo(x * sx, y * sy);
    });
    ctx.closePath();
    ctx.fillStyle = selected ? "rgba(217, 154, 43, 0.12)" : "rgba(36, 199, 106, 0.04)";
    ctx.fill();
    ctx.strokeStyle = selected ? "#d99a2b" : "#24c76a";
    ctx.lineWidth = selected ? 3 : 2;
    ctx.stroke();

    const ellipse = geometry.ellipse || geometry;
    const cx = Number(ellipse.center_x) * sx;
    const cy = Number(ellipse.center_y) * sy;
    const rx = Number(ellipse.major_px) * sx / 2;
    const ry = Number(ellipse.minor_px) * sy / 2;
    const angle = Number(ellipse.angle_deg) * Math.PI / 180;
    ctx.beginPath();
    ctx.ellipse(cx, cy, rx, ry, angle, 0, Math.PI * 2);
    ctx.strokeStyle = "#48a9ff";
    ctx.lineWidth = selected ? 2.5 : 1.5;
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx - Math.cos(angle) * rx, cy - Math.sin(angle) * rx);
    ctx.lineTo(cx + Math.cos(angle) * rx, cy + Math.sin(angle) * rx);
    ctx.moveTo(cx + Math.sin(angle) * ry, cy - Math.cos(angle) * ry);
    ctx.lineTo(cx - Math.sin(angle) * ry, cy + Math.cos(angle) * ry);
    ctx.strokeStyle = "#f4d35e";
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, cy, 4, 0, Math.PI * 2);
    ctx.fillStyle = "#e34545";
    ctx.fill();
    return;
  }
  const left = Math.min(box.x1, box.x2) * sx;
  const top = Math.min(box.y1, box.y2) * sy;
  const wide = Math.abs(box.x2 - box.x1) * sx;
  const tall = Math.abs(box.y2 - box.y1) * sy;
  const rx = Math.max(wide / 2, 0.5);
  const ry = Math.max(tall / 2, 0.5);
  const cx = left + wide / 2;
  const cy = top + tall / 2;
  ctx.beginPath();
  ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
  ctx.fillStyle = selected ? "rgba(217, 154, 43, 0.22)" : "rgba(217, 154, 43, 0.1)";
  ctx.fill();
  ctx.strokeStyle = selected ? "#d99a2b" : "rgba(217, 154, 43, 0.7)";
  ctx.lineWidth = selected ? 3 : 2;
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(cx - rx, cy);
  ctx.lineTo(cx + rx, cy);
  ctx.moveTo(cx, cy - ry);
  ctx.lineTo(cx, cy + ry);
  ctx.strokeStyle = selected ? "rgba(217, 154, 43, 0.9)" : "rgba(217, 154, 43, 0.45)";
  ctx.lineWidth = 1.5;
  ctx.stroke();
  if (number) {
    ctx.font = "600 14px sans-serif";
    ctx.fillStyle = "#f6f1e5";
    ctx.strokeStyle = "#13372c";
    ctx.lineWidth = 3;
    const label = String(number);
    ctx.strokeText(label, cx - 4, cy + 5);
    ctx.fillText(label, cx - 4, cy + 5);
  }
}

function annotatePaint() {
  const canvas = document.getElementById("annotate-canvas");
  const image = document.getElementById("annotate-image");
  if (!canvas || !image || !image.naturalWidth) return;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const sx = canvas.width / image.naturalWidth;
  const sy = canvas.height / image.naturalHeight;
  annotate.ovals.forEach((oval, index) => {
    annotateDrawOval(ctx, oval, sx, sy, index === annotate.selected && !annotate.draft, index + 1);
  });
  if (annotate.draft) annotateDrawOval(ctx, annotate.draft, sx, sy, true, 0);
  annotateStatus();
  annotateUpdateMeasurements();
}

function annotateOvalFromDetection(egg, result) {
  const bbox = egg.bbox;
  const ellipse = egg.ellipse;
  const geometry = {
    ...ellipse,
    ellipse: { ...ellipse },
    bbox: { ...bbox },
    area_px: egg.area_px,
    perimeter_px: egg.perimeter_px,
    length_px: egg.length_px,
    width_px: egg.width_px,
    contour: egg.contour,
    cross_sections: egg.cross_sections || [],
  };
  return {
    x1: bbox.x,
    y1: bbox.y,
    x2: bbox.x + bbox.width,
    y2: bbox.y + bbox.height,
    length_mm: egg.length_mm == null ? "" : egg.length_mm,
    width_mm: egg.width_mm == null ? "" : egg.width_mm,
    calibration: result.pixels_per_mm && egg.length_mm != null && egg.width_mm != null ? "scale" : "manual",
    geometry,
  };
}

function annotateUndo() {
  if (annotate.draft) {
    annotate.draft = null;
    annotate.drag = null;
    annotatePaint();
    return;
  }
  if (!annotate.ovals.length) {
    toast("Nothing to undo.", "error");
    return;
  }
  annotate.ovals.pop();
  annotate.selected = annotate.ovals.length - 1;
  annotateFillFields();
  annotateMarkDirty();
  annotatePaint();
}

async function annotateAuto() {
  const item = annotate.images[annotate.index];
  if (!item) return;
  const empty = document.getElementById("annotate-empty");
  if (empty) empty.hidden = true;
  const button = document.getElementById("annotate-auto");
  if (button) button.disabled = true;
  try {
    const result = await api(
      "/api/dataset/annotate/" + annotate.slug + "/" + encodeURIComponent(item.name) + "/auto",
      { method: "POST" },
    );
    const eggs = Array.isArray(result.eggs) ? result.eggs : [];
    if (!eggs.length || eggs.length > 40 || eggs.some((egg) => !egg || !egg.bbox || !egg.ellipse || !Array.isArray(egg.contour))) {
      throw new Error("No reliable egg contours were returned. Click inside an egg to guide detection, or draw its oval manually.");
    }
    const detectedOvals = eggs.map((egg) => annotateOvalFromDetection(egg, result));
    if (detectedOvals.some((oval) => !annotateValidOval(oval))) {
      throw new Error("The detected contour was invalid. Click inside an egg to guide detection, or draw its oval manually.");
    }
    annotate.detectionWarnings = [...(result.warnings || [])];
    annotate.ovals = detectedOvals;
    annotate.selected = annotate.ovals.length ? 0 : -1;
    const label = result.reference_label || {};
    annotate.referenceClass = annotate.referenceClass || label.class_name || "";
    annotate.referenceLabelSource = annotate.referenceClass
      ? (annotateFilenameLabel(item.name) === annotate.referenceClass ? "filename" : "manual")
      : label.source || "";
    document.getElementById("annotate-reference-class").value = annotate.referenceClass;
    annotateUpdateReferenceLabel();
    annotateFillFields();
    document.getElementById("annotate-progress").textContent =
      "Image " + (annotate.index + 1) + " of " + annotate.images.length +
      " · " + annotate.ovals.length + " egg(s) detected in " + result.filename;
    annotateStatus();
    annotatePaint();
  } catch (error) {
    annotate.detectionWarnings = [error.message || "Automatic egg detection failed."];
    const warning = document.getElementById("annotate-detection-warning");
    if (warning) {
      warning.hidden = false;
      warning.textContent = annotate.detectionWarnings[0] + " Click inside an egg to guide detection, or draw the oval manually.";
    }
    toast(annotate.detectionWarnings[0], "error");
    annotateUpdateMeasurements();
  } finally {
    if (button) button.disabled = false;
  }
}

async function annotateGuide(point, existingOval) {
  const item = annotate.images[annotate.index];
  if (!item) return;
  const empty = document.getElementById("annotate-empty");
  if (empty) empty.hidden = true;
  try {
    const result = await api(
      "/api/dataset/annotate/" + annotate.slug + "/" + encodeURIComponent(item.name) + "/guide",
      { method: "POST", json: { x: point.x, y: point.y } },
    );
    const egg = result.eggs && result.eggs[0];
    if (!egg) throw new Error("No egg boundary was found at that point.");
    const oval = annotateOvalFromDetection(egg, result);
    if (!annotateValidOval(oval)) throw new Error("The selected egg returned an invalid contour. Try a point farther inside the egg.");
    if (existingOval >= 0 && existingOval < annotate.ovals.length) {
      annotate.ovals[existingOval] = oval;
      annotate.selected = existingOval;
    } else {
      annotate.ovals.push(oval);
      annotate.selected = annotate.ovals.length - 1;
    }
    const label = result.reference_label || {};
    annotate.referenceClass = annotate.referenceClass || label.class_name || "";
    annotate.referenceLabelSource = annotate.referenceClass
      ? (annotateFilenameLabel(item.name) === annotate.referenceClass ? "filename" : "manual")
      : label.source || "";
    document.getElementById("annotate-reference-class").value = annotate.referenceClass;
    annotateUpdateReferenceLabel();
    annotate.detectionWarnings = [];
    const warning = document.getElementById("annotate-detection-warning");
    if (warning) warning.hidden = true;
    annotateFillFields();
    annotateMarkDirty();
    document.getElementById("annotate-progress").textContent =
      "Image " + (annotate.index + 1) + " of " + annotate.images.length +
      " · selected egg measured at (" + Math.round(point.x) + ", " + Math.round(point.y) + ")";
    annotatePaint();
  } catch (error) {
    annotatePaint();
    toast(error.message || "The egg could not be separated at that point. Try another point inside the egg.", "error");
  }
}

function annotateRestoreSaved() {
  let saved;
  try {
    saved = JSON.parse(annotate.savedJson);
  } catch (error) {
    toast("Could not restore the saved annotation.", "error");
    return;
  }
  annotate.ovals = Array.isArray(saved.ovals) ? saved.ovals : [];
  annotate.referenceClass = saved.reference_class || "";
  annotate.referenceLabelSource = saved.reference_label_source || "";
  annotate.reviewed = annotate.savedReviewed;
  annotate.detectionWarnings = [];
  annotate.selected = annotate.ovals.length - 1;
  annotate.draft = null;
  annotate.drag = null;
  document.getElementById("annotate-reference-class").value = annotate.referenceClass;
  annotateUpdateReferenceLabel();
  annotate.reviewed = annotate.savedReviewed;
  annotate.lastSnapshot = annotateSnapshot();
  annotateFillFields();
  annotate.dirty = false;
  document.getElementById("annotate-reviewed").textContent = annotate.reviewed ? "Reviewed ✓" : "Mark reviewed";
  annotatePaint();
  toast("Saved annotation restored.", "ok");
}

async function annotateSave() {
  const item = annotate.images[annotate.index];
  if (!item || !annotate.ovals.length) {
    toast("Draw an oval around each egg first.", "error");
    return;
  }
  if (!annotate.referenceClass) {
    toast("Choose Small, Medium, Large, Extra Large, or Jumbo as the reference label.", "error");
    return;
  }
  if (!annotate.reviewed) {
    toast("Review the detected contour and reference label before saving.", "error");
    return;
  }
  const ovals = [];
  let pixelOnlyCount = 0;
  for (const oval of annotate.ovals) {
    if (!annotateValidOval(oval)) {
      toast("An oval is outside the image or too small. Redraw it before saving.", "error");
      return;
    }
    const sides = annotateContourMeasurements(oval) || annotateSides(oval);
    const lengthValue = oval.length_mm;
    const widthValue = oval.width_mm;
    const lengthProvided = lengthValue !== "" && lengthValue != null;
    const widthProvided = widthValue !== "" && widthValue != null;
    const lengthNumber = Number(lengthValue);
    const widthNumber = Number(widthValue);
    if ((lengthProvided && (!Number.isFinite(lengthNumber) || lengthNumber <= 0)) ||
        (widthProvided && (!Number.isFinite(widthNumber) || widthNumber <= 0))) {
      toast("Length and width must be finite numbers greater than 0 mm.", "error");
      return;
    }
    let calibration = String(oval.calibration || "manual");
    const calibrationValid = ["manual", "scale", "pixels"].includes(calibration);
    const hasLength = calibrationValid && lengthProvided;
    const hasWidth = calibrationValid && widthProvided;
    if (!sides || hasLength !== hasWidth) {
      toast("Each oval needs pixel dimensions; provide both millimetre measurements or leave both unavailable.", "error");
      return;
    }
    let length = hasLength ? Number(lengthValue) : null;
    let width = hasWidth ? Number(widthValue) : null;
    const scaleLength = annotate.scale > 0 ? sides.length / annotate.scale : 0;
    const scaleWidth = annotate.scale > 0 ? sides.width / annotate.scale : 0;
    if (!calibrationValid) {
      length = null;
      width = null;
      calibration = "pixels";
    }
    if (length != null && calibration === "scale" && !(annotate.scale > 0)) {
      length = null;
      width = null;
      calibration = "pixels";
    }
    if (length == null) {
      calibration = "pixels";
      pixelOnlyCount += 1;
    } else {
      const lengthClose = scaleLength && Math.abs(scaleLength - length) <= Math.max(0.5, scaleLength * 0.05);
      const widthClose = scaleWidth && Math.abs(scaleWidth - width) <= Math.max(0.5, scaleWidth * 0.05);
      calibration = lengthClose && widthClose ? "scale" : "manual";
    }
    ovals.push({
      start_x: oval.x1,
      start_y: oval.y1,
      end_x: oval.x2,
      end_y: oval.y2,
      length_mm: length,
      width_mm: width,
      calibration: calibration,
      geometry: oval.geometry || undefined,
    });
  }
  const saved = await api("/api/dataset/annotate/" + annotate.slug + "/" + encodeURIComponent(item.name) + "/mark", {
    method: "PUT",
    json: {
      ovals: ovals,
      reference_class: annotate.referenceClass,
      reference_label_source: annotate.referenceLabelSource || "manual",
      reviewed: annotate.reviewed,
    },
  });
  const savedOvals = annotateFromMark(saved);
  if (savedOvals.length !== ovals.length || saved.reviewed !== annotate.reviewed) {
    throw new Error("The server returned an incomplete save response. Reload this image to verify its saved annotation.");
  }
  annotate.ovals = savedOvals;
  annotate.referenceClass = saved.reference_label && saved.reference_label.class_name || annotate.referenceClass;
  annotate.referenceLabelSource = saved.reference_label && saved.reference_label.source || annotate.referenceLabelSource;
  annotate.savedCount = annotate.ovals.length;
  annotate.selected = annotate.ovals.length - 1;
  annotate.savedJson = annotateSnapshot();
  annotate.lastSnapshot = annotate.savedJson;
  annotate.savedReviewed = annotate.reviewed;
  annotate.dirty = false;
  if (pixelOnlyCount) {
    annotate.detectionWarnings = ["Calibration or physical measurement is unavailable, invalid, or unreliable. It was not used; the pixel outline remains saved. Correct the calibration to record millimetres."];
    annotateUpdateMeasurements();
  }
  document.getElementById("annotate-reference-class").value = annotate.referenceClass;
  annotateUpdateReferenceLabel();
  item.annotated = true;
  item.ovals = annotate.savedCount;
  annotateFillFields();
  document.getElementById("annotate-reset").disabled = false;
  document.getElementById("annotate-save").textContent = "Update annotation";
  annotateStatus();
  annotatePaint();
  toast(pixelOnlyCount
    ? (pixelOnlyCount === 1 ? "Pixel annotation saved." : pixelOnlyCount + " pixel annotations saved.") + " Physical measurements need valid calibration. The original photo is retained; prepare the dataset to export YOLO image/label pairs."
    : "Annotation saved. The original photo is retained. Prepare the dataset to export the YOLO image/label pair.", "ok");
  if (typeof loadDataset === "function") loadDataset().catch(() => {});
}

async function annotateDelete() {
  const item = annotate.images[annotate.index];
  if (!item || (!annotate.savedCount && !annotate.ovals.length)) {
    toast("This image has no saved oval.", "error");
    return;
  }
  const yes = typeof openModal === "function"
    ? await openModal({ title: "Delete annotation", body: "Remove every oval on this image?", okLabel: "Delete" })
    : window.confirm("Remove every oval on this image?");
  if (!yes) return;
  if (annotate.savedCount) {
    await api("/api/dataset/annotate/" + annotate.slug + "/" + encodeURIComponent(item.name) + "/mark", { method: "DELETE" });
  }
  annotate.ovals = [];
  annotate.selected = -1;
  annotate.draft = null;
  annotate.savedCount = 0;
  annotate.savedJson = annotateSnapshot();
  annotate.lastSnapshot = annotate.savedJson;
  annotate.reviewed = false;
  annotate.savedReviewed = false;
  annotate.dirty = false;
  item.annotated = false;
  item.ovals = 0;
  annotateFillFields();
  document.getElementById("annotate-save").textContent = "Save annotation";
  annotateStatus();
  annotatePaint();
  toast("Ovals removed.", "ok");
  if (typeof loadDataset === "function") loadDataset().catch(() => {});
}

async function annotateMove(step) {
  if (annotate.dirty) {
    const yes = typeof openModal === "function"
      ? await openModal({ title: "Unsaved ovals", body: "Leave this image without saving the ovals?", okLabel: "Leave" })
      : window.confirm("Leave this image without saving the ovals?");
    if (!yes) return;
  }
  const next = annotate.index + step;
  if (next < 0 || next >= annotate.images.length) return;
  const previous = annotate.index;
  annotate.index = next;
  let loaded;
  try {
    loaded = await annotateShow();
  } catch (error) {
    annotate.index = previous;
    throw error;
  }
  if (!loaded) {
    annotate.index = previous;
    return;
  }
  if (!annotate.savedCount) await annotateAuto();
}

function annotateClose() {
  if (annotate.busy) return;
  const modal = document.getElementById("annotate-modal");
  modal.hidden = true;
  delete modal.dataset.annotateClass;
  annotateClear();
}

document.querySelectorAll("[data-annotate]").forEach((button) => {
  button.addEventListener("click", () => annotateOpen(button.dataset.annotate));
});

const annotateCanvas = document.getElementById("annotate-canvas");
if (annotateCanvas) {
  annotateCanvas.addEventListener("pointerdown", (event) => {
    if (annotate.busy) return;
    if (!document.getElementById("annotate-image").naturalWidth) return;
    if (annotate.ovals.length >= 40) {
      toast("An image can have at most 40 ovals.", "error");
      return;
    }
    const point = annotatePoint(event);
    if (!point) return;
    annotate.drag = point;
    annotate.pointerStart = { x: event.clientX, y: event.clientY };
    annotate.draft = { x1: point.x, y1: point.y, x2: point.x, y2: point.y };
    try { annotateCanvas.setPointerCapture(event.pointerId); } catch (error) { /* the pointer can end before capture */ }
    annotatePaint();
  });
  annotateCanvas.addEventListener("pointermove", (event) => {
    if (annotate.busy || !annotate.drag || !annotate.draft) return;
    const point = annotatePoint(event, true);
    annotate.draft.x2 = point.x;
    annotate.draft.y2 = point.y;
    annotatePaint();
  });
  annotateCanvas.addEventListener("pointerup", (event) => {
    const draft = annotate.draft;
    const origin = annotate.drag;
    const pointerStart = annotate.pointerStart;
    const end = annotatePoint(event, true);
    annotate.drag = null;
    annotate.pointerStart = null;
    annotate.draft = null;
    if (!draft || !origin) {
      annotatePaint();
      return;
    }
    if (annotate.busy) {
      annotatePaint();
      return;
    }
    draft.x2 = end.x;
    draft.y2 = end.y;
    const moved = pointerStart && Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) >= 5;
    if (!moved) {
      const hit = annotateHit(origin);
      annotateExclusive(() => annotateGuide(origin, hit));
      return;
    }
    const sides = annotateSides(draft);
    if (!sides || !annotateValidOval(draft)) {
      annotatePaint();
      return;
    }
    const length = annotate.scale > 0 ? sides.length / annotate.scale : 0;
    const width = annotate.scale > 0 ? sides.width / annotate.scale : 0;
    annotate.ovals.push({
      x1: draft.x1,
      y1: draft.y1,
      x2: draft.x2,
      y2: draft.y2,
      length_mm: length > 0 ? Number(length.toFixed(1)) : "",
      width_mm: width > 0 ? Number(width.toFixed(1)) : "",
      calibration: length > 0 && width > 0 ? "scale" : "manual",
    });
    annotate.selected = annotate.ovals.length - 1;
    annotateFillFields();
    annotateMarkDirty();
    annotatePaint();
  });
}

document.getElementById("annotate-close").onclick = () => annotateClose();
document.getElementById("annotate-prev").onclick = () => annotateExclusive(() => annotateMove(-1), "annotate-prev", "Loading…");
document.getElementById("annotate-next").onclick = () => annotateExclusive(() => annotateMove(1), "annotate-next", "Loading…");
document.getElementById("annotate-save").onclick = () => annotateExclusive(annotateSave, "annotate-save", "Saving…");
document.getElementById("annotate-delete").onclick = () => annotateExclusive(annotateDelete, "annotate-delete", "Deleting…");
document.getElementById("annotate-auto").onclick = () => annotateExclusive(annotateAuto, "annotate-auto", "Detecting…");
document.getElementById("annotate-reset").onclick = () => annotateExclusive(annotateRestoreSaved, "annotate-reset", "Restoring…");
document.getElementById("annotate-reviewed").onclick = () => annotateExclusive(() => {
  if (!annotate.ovals.length || !annotate.referenceClass) {
    toast("Add an egg annotation and choose its reference label before review.", "error");
    return;
  }
  annotate.reviewed = true;
  annotateMarkDirty();
  annotatePaint();
}, "annotate-reviewed", "Updating…");
document.getElementById("annotate-reference-class").addEventListener("change", () => {
  annotateUpdateReferenceLabel();
});
const annotateStage = document.getElementById("annotate-stage");
if (annotateStage && typeof ResizeObserver !== "undefined") {
  new ResizeObserver(() => {
    if (!document.getElementById("annotate-modal").hidden) annotateFit();
  }).observe(annotateStage);
} else {
  window.addEventListener("resize", () => {
    if (!document.getElementById("annotate-modal").hidden) annotateFit();
  });
}
window.addEventListener("keydown", (event) => {
  const modal = document.getElementById("annotate-modal");
  const confirm = document.getElementById("modal");
  if (!modal || modal.hidden) return;
  if (confirm && !confirm.hidden) {
    if (
      event.key === "Enter" &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.shiftKey &&
      !event.altKey &&
      document.getElementById("modal-title").textContent === "Unsaved ovals"
    ) {
      event.preventDefault();
      document.getElementById("modal-ok").click();
    }
    return;
  }
  if (event.key === "ArrowRight" && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
    const tag = event.target && event.target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || event.target.isContentEditable) return;
    event.preventDefault();
    document.getElementById("annotate-next").click();
    return;
  }
  if (!(event.ctrlKey || event.metaKey) || event.shiftKey || event.altKey) return;
  if (event.key.toLowerCase() !== "z" && event.code !== "KeyZ") return;
  event.preventDefault();
  annotateUndo();
});
