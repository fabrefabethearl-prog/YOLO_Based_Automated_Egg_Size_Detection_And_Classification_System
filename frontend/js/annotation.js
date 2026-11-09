/* Egg annotation and measurement from uploaded images.

This module handles image upload, validation, preview, and automatic egg annotation
with contour and ellipse fitting. It's designed to work independently of camera
hardware for the current phase, and can be reused for ESP32-CAM integration.
*/

let uploadedImageBlob = null;
let annotationData = null;

const VALID_IMAGE_TYPES = ["image/jpeg", "image/jpg", "image/png", "image/webp", "image/bmp"];
const MAX_IMAGE_SIZE = 10 * 1024 * 1024; // 10 MB

function validateImageFile(file) {
  if (!file) {
    return { valid: false, error: "No file selected." };
  }

  if (!VALID_IMAGE_TYPES.includes(file.type)) {
    return { valid: false, error: "Please upload a JPEG, PNG, or WebP image." };
  }

  if (file.size > MAX_IMAGE_SIZE) {
    return { valid: false, error: "Image must be 10 MB or smaller." };
  }

  return { valid: true, error: null };
}

function handleImageUpload(event) {
  const file = event.target.files[0];
  if (!file) return;

  const validation = validateImageFile(file);
  if (!validation.valid) {
    toast(validation.error, "error");
    event.target.value = "";
    return;
  }

  uploadedImageBlob = file;
  toast("Image uploaded. Annotating automatically...", "ok");

  // Show the uploaded image preview (this will trigger automatic annotation)
  showImagePreview(file);

  // Enable re-annotate button
  const annotateBtn = document.getElementById("btn-annotate");
  if (annotateBtn) annotateBtn.disabled = false;
}

function showImagePreview(file) {
  const stage = document.getElementById("stage");
  const liveView = document.getElementById("live-view");
  const snap = document.getElementById("snap");

  if (!stage || !liveView || !snap) return;

  const ctx = snap.getContext("2d");
  const img = new Image();

  img.onload = () => {
    // Set canvas to match image dimensions (preserve aspect ratio)
    snap.width = img.width;
    snap.height = img.height;
    ctx.drawImage(img, 0, 0);

    snap.hidden = false;
    liveView.hidden = true;
    showStage("live");

    const btnCam = document.getElementById("btn-cam");
    if (btnCam) btnCam.textContent = "Stop scan";

    // Automatically trigger annotation after preview is shown
    setTimeout(() => {
      annotateEgg();
    }, 100);
  };

  img.onerror = () => {
    toast("Failed to load image preview.", "error");
  };

  img.src = URL.createObjectURL(file);
}

async function annotateEgg() {
  if (!uploadedImageBlob) {
    toast("Please upload an image first.", "error");
    return;
  }

  const annotateBtn = document.getElementById("btn-annotate");
  if (annotateBtn) annotateBtn.disabled = true;

  try {
    const body = new FormData();
    body.append("file", uploadedImageBlob, "uploaded.jpg");

    const data = await api("/api/scan/annotate", { method: "POST", body });
    annotationData = data;

    // Display annotated image
    displayAnnotatedImage(data.annotated_image);

    // Display measurements
    displayMeasurements(data);

    toast("Egg annotated successfully.", "ok");

    // Log warnings if any
    if (data.warnings && data.warnings.length) {
      data.warnings.forEach((warning) => console.warn(warning));
    }
  } catch (error) {
    if (error.status !== 401) {
      const message = error.message || "Annotation failed. Check the image and try again.";
      toast(message, "error");
      liveText("live-reason", message);
    }
  } finally {
    if (annotateBtn) annotateBtn.disabled = false;
  }
}

function displayAnnotatedImage(dataUrl) {
  const snap = document.getElementById("snap");
  if (!snap) return;

  const ctx = snap.getContext("2d");
  const img = new Image();

  img.onload = () => {
    snap.width = img.width;
    snap.height = img.height;
    ctx.drawImage(img, 0, 0);
  };

  img.src = dataUrl;
}

function displayMeasurements(data) {
  const measurements = data.measurements;
  const unit = data.unit || "px";
  const ppm = data.pixels_per_mm;

  // Update live scan display
  liveStatus("Annotated");
  liveText("live-class", "Egg detected");
  liveText("live-size", `${measurements.length_px} × ${measurements.width_px} ${unit}`);
  liveText("live-conf", `${(measurements.score * 100).toFixed(1)}%`);
  liveText("live-ms", "—");
  liveText(
    "live-reason",
    `Contour (green) and ellipse (blue) shown. ${data.definition}`
  );

  // Display detailed measurements in the result panel
  const resultPanel = document.getElementById("result-panel");
  if (!resultPanel) return;

  // Remove existing measurement details if any
  const existing = resultPanel.querySelector(".measurement-details");
  if (existing) existing.remove();

  const existingCross = resultPanel.querySelector(".cross-section-details");
  if (existingCross) existingCross.remove();

  const measurementDetails = document.createElement("div");
  measurementDetails.className = "measurement-details";
  measurementDetails.innerHTML = `
    <h4>Geometric Measurements</h4>
    <div class="measurement-grid">
      <div><span>Length</span><strong>${measurements.length_px} ${unit}</strong></div>
      <div><span>Width</span><strong>${measurements.width_px} ${unit}</strong></div>
      <div><span>Area</span><strong>${measurements.area_px} ${unit}²</strong></div>
      <div><span>Perimeter</span><strong>${measurements.perimeter_px} ${unit}</strong></div>
      <div><span>Major axis</span><strong>${measurements.ellipse.major_px} ${unit}</strong></div>
      <div><span>Minor axis</span><strong>${measurements.ellipse.minor_px} ${unit}</strong></div>
      <div><span>Orientation</span><strong>${measurements.ellipse.angle_deg}°</strong></div>
      <div><span>Center</span><strong>(${measurements.ellipse.center_x.toFixed(1)}, ${measurements.ellipse.center_y.toFixed(1)})</strong></div>
    </div>
    ${ppm
      ? `<p class="hint">Calibration: ${ppm} pixels/mm</p>`
      : `<p class="hint">Measurements are in pixels until a camera scale is configured.</p>`
    }
    ${data.warnings && data.warnings.length
      ? `<p class="callout">${data.warnings.join(" ")}</p>`
      : ""
    }
  `;

  resultPanel.appendChild(measurementDetails);

  // Display cross-sections if available
  if (measurements.cross_sections && measurements.cross_sections.length) {
    const crossSectionDetails = document.createElement("div");
    crossSectionDetails.className = "cross-section-details";
    crossSectionDetails.innerHTML = `
      <h4>Cross-sectional Widths</h4>
      <div class="cross-section-list">
        ${measurements.cross_sections
          .map(
            (cs) => `
          <div>
            <span>Position ${cs.position}</span>
            <strong>${cs.width_px} ${unit}</strong>
            ${cs.width_mm ? `<small>${cs.width_mm} mm</small>` : ""}
          </div>
        `
          )
          .join("")}
      </div>
    `;
    resultPanel.appendChild(crossSectionDetails);
  }
}

function initializeAnnotationUI() {
  const fileInput = document.getElementById("file-input");
  if (fileInput) {
    fileInput.addEventListener("change", handleImageUpload);
  }

  // Annotate button is now optional (for manual retry)
  // Annotation happens automatically on upload
  const fileBtn = document.querySelector(".file-btn");
  if (fileBtn) {
    const annotateBtn = document.createElement("button");
    annotateBtn.id = "btn-annotate";
    annotateBtn.className = "btn btn-ghost";
    annotateBtn.type = "button";
    annotateBtn.textContent = "Re-annotate";
    annotateBtn.onclick = annotateEgg;
    annotateBtn.disabled = true;
    annotateBtn.style.marginLeft = "8px";

    fileBtn.parentNode.insertBefore(annotateBtn, fileBtn.nextSibling);
  }
}

// Initialize when DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeAnnotationUI);
} else {
  initializeAnnotationUI();
}
