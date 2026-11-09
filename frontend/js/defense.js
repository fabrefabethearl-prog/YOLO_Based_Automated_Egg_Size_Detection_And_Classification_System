/* Defense connections for the existing EggScan dashboard. */
(function () {
  const STEPS = [
    ["placed", "Egg placed"],
    ["captured", "Image captured"],
    ["detect", "YOLO detects"],
    ["estimate", "Size estimated"],
    ["class", "Class assigned"],
    ["shown", "Result displayed"],
  ];

  window.eggscanSession = { active: false, id: "" };
  window.eggscanRecordCache = {};
  let lastOverview = null;
  let drawerReturnFocus = null;
  let drawerImageUrl = "";
  let recordImageGeneration = 0;
  let recordImageUrls = [];

  function pendingSteps(activeIndex) {
    return STEPS.map(function (pair, index) {
      return {
        id: pair[0],
        label: pair[1],
        status: index < activeIndex ? "done" : index === activeIndex ? "active" : "pending",
      };
    });
  }

  function paintPipeline(steps) {
    const root = document.getElementById("def-pipeline");
    if (!root) return;
    root.innerHTML = steps.map(function (step) {
      const cls = step.status === "done" ? "is-done" : step.status === "active" ? "is-active" : step.status === "fail" ? "is-fail" : "";
      const ms = step.ms === undefined || step.ms === null ? "" : "<small>" + esc(num(step.ms, 1, " ms")) + "</small>";
      const reason = step.status === "fail" && step.reason ? "<small>" + esc(step.reason) + "</small>" : "";
      return '<div class="def-step ' + cls + '"><b>' + esc(step.label) + "</b>" + ms + reason + "</div>";
    }).join("");
  }

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  window.eggscanPipelineStart = function () {
    paintPipeline(pendingSteps(0));
  };

  async function playSteps(steps) {
    for (let index = 0; index < steps.length; index += 1) {
      paintPipeline(steps.map(function (step, cursor) {
        if (cursor < index) return Object.assign({}, step, { status: "done", reason: null });
        if (cursor === index) return Object.assign({}, step, { status: step.status === "fail" ? "fail" : "active" });
        return Object.assign({}, step, { status: "pending", reason: null });
      }));
      await sleep(140);
      if (steps[index].status === "fail") break;
    }
    paintPipeline(steps);
  }

  async function saveClass(id, name) {
    const saved = await api("/api/scans/" + id, { method: "PATCH", json: { actual_class: name || null } });
    toast(name ? "Actual size class saved." : "Actual size class cleared.", "ok");
    await loadOverview();
    await loadRecords();
    return saved;
  }

  async function appendClassForm(data) {
    const box = document.getElementById("live-verify");
    const select = document.getElementById("live-actual-class");
    if (!box || !select || !data.id) return;
    await ensureCategories();
    select.innerHTML = classOptions(data.actual_class || data.predicted_class || "");
    box.hidden = false;
    const button = document.getElementById("live-verify-save");
    button.onclick = async function () {
      if (!select.value) {
        toast("Choose a size class.", "error");
        return;
      }
      try {
        const saved = await saveClass(data.id, select.value);
        if (saved) data.actual_class = saved.actual_class;
      } catch (error) {
        if (error.status !== 401) toast(error.message, "error");
      }
    };
  }

  window.eggscanAfterScan = async function (data) {
    const steps = Array.isArray(data.steps) && data.steps.length ? data.steps : pendingSteps(data.detected ? 5 : 2).map(function (step, index) {
      if (!data.detected && index === 2) return Object.assign({}, step, { status: "fail", reason: data.message || "No egg detected" });
      if (data.detected) return Object.assign({}, step, { status: "done", ms: step.id === "detect" ? data.inference_ms : undefined });
      if (index < 2) return Object.assign({}, step, { status: "done" });
      return Object.assign({}, step, { status: "pending" });
    });
    await playSteps(steps);
    if (!data.detected || data.saved === false) {
      if (!data.detected) {
        const indicator = document.getElementById("live-indicator");
        const indicatorText = document.getElementById("live-indicator-text");
        if (indicator && indicatorText) {
          indicatorText.textContent = data.message || "No egg detected. Position an egg within the camera view.";
          indicator.dataset.state = "empty";
        }
      }
      return;
    }
    appendClassForm(data);
  };

  function renderStatus(data) {
    const root = document.getElementById("def-status");
    if (!root) return;
    const model = data.model || {};
    const mode = String(model.mode || "Unknown").toUpperCase();
    const ready = model.ready ? "ready" : "not ready";
    const camera = state.stream ? "Active in Live scan" : "Inactive outside Live scan";
    const last = data.last_scan_at ? formatWhen(data.last_scan_at) : "No scans yet";
    root.innerHTML = "<span>Camera <b>" + camera + "</b></span><span>Model <b>" + esc(model.name || "—") + " · " + esc(model.version || "—") + " · " + ready + '</b></span><span class="badge ' + (mode === "REAL" && model.ready ? "badge-live" : "badge-mock") + '">' + esc(mode) + "</span><span>Last scan <b>" + esc(last) + "</b></span>";
  }

  function renderProgress(data) {
    const root = document.getElementById("def-progress");
    if (!root) return;
    if (!data.total_scanned) {
      root.innerHTML = '<p class="empty">No data yet</p>';
      return;
    }
    const verified = Number(data.verified_count || 0);
    const unverified = Number(data.unverified_count || 0);
    const width = Math.max(0, Math.min(100, (verified / data.total_scanned) * 100));
    root.innerHTML = '<div class="def-progress-track"><div class="def-progress-bar" style="width:' + width + '%"></div></div><div class="def-progress-meta"><span>Verified ' + verified + '</span><button type="button" id="def-unverified">Unverified ' + unverified + '</button><button type="button" id="def-misses">Misses ' + Number(data.miss_count || 0) + "</button></div>";
  }

  function setBadge(count) {
    ["def-weigh-badge", "def-weigh-count"].forEach(function (id) {
      const node = document.getElementById(id);
      if (!node) return;
      node.hidden = !count;
      node.textContent = String(count);
    });
  }

  async function refreshBadge() {
    try {
      const data = await api("/api/scans?correctness=unlabeled&page_size=1");
      setBadge(data.total || 0);
    } catch (error) {
      if (error.status === 401) return;
    }
  }

  window.eggscanAfterOverview = function (data) {
    lastOverview = data;
    renderStatus(data);
    renderProgress(data);
    refreshBadge();
  };

  window.eggscanCameraChanged = function () {
    if (lastOverview) renderStatus(lastOverview);
  };

  async function openRecords(options) {
    options = options || {};
    await ensureCategories();
    fillClassFilter();
    document.getElementById("rec-q").value = options.q || "";
    document.getElementById("rec-class").value = options.className || "";
    document.getElementById("rec-actual").value = options.actualName || "";
    document.getElementById("rec-correct").value = options.correctness || "all";
    document.getElementById("rec-from").value = options.from || "";
    document.getElementById("rec-to").value = options.to || "";
    state.records.page = 1;
    setPage("records");
  }

  function fieldText(value, digits, suffix) {
    const text = num(value, digits, suffix);
    return text === null ? "—" : text;
  }

  function openDrawer(scan) {
    if (!scan) return;
    const drawer = document.getElementById("def-drawer");
    const backdrop = document.getElementById("def-backdrop");
    if (drawer.hidden) drawerReturnFocus = document.activeElement;
    if (drawerImageUrl) URL.revokeObjectURL(drawerImageUrl);
    drawerImageUrl = "";
    const correct = scan.correct === null ? "Unverified" : scan.correct ? "Correct" : "Miss";
    const options = classOptions(scan.actual_class || "");
    drawer.querySelector("[data-slot='body']").innerHTML = [
      '<img data-slot="photo" alt="Scan image preview" />',
      "<dl>",
      "<dt>Captured</dt><dd>" + esc(formatRecordTimestamp(scan.created_at)) + "</dd>",
      "<dt>Length</dt><dd>" + esc(fieldText(scan.length_mm, 1, " mm")) + "</dd>",
      "<dt>Width</dt><dd>" + esc(fieldText(scan.width_mm, 1, " mm")) + "</dd>",
      "<dt>Area</dt><dd>" + esc(fieldText(scan.area_mm2, 0, " mm²")) + "</dd>",
      "<dt>Predicted class</dt><dd>" + esc(scan.predicted_class) + "</dd>",
      "<dt>Actual class</dt><dd>" + esc(scan.actual_class || "—") + "</dd>",
      "<dt>Result</dt><dd>" + correct + "</dd>",
      "<dt>Confidence</dt><dd>" + esc(pct(scan.confidence) || "—") + "</dd>",
      "<dt>Inference</dt><dd>" + esc(fieldText(scan.inference_ms, 1, " ms")) + "</dd>",
      "<dt>Model</dt><dd>" + esc(scan.model_version || "—") + "</dd>",
      "<dt>Operator</dt><dd>" + esc(scan.operator_email || "—") + "</dd>",
      "</dl>",
      '<label>Verify actual size class <select id="drawer-class" class="def-class" aria-label="Actual size class">' + options + "</select></label>",
      '<div class="button-row">',
      '<button class="btn btn-primary" type="button" data-act="save-class">Save</button>',
      '<button class="btn btn-green" type="button" data-act="again">Re-classify</button>',
      '<button class="btn btn-danger" type="button" data-act="delete">Delete</button>',
      "</div>",
    ].join("");
    drawer.hidden = false;
    backdrop.hidden = false;
    drawer.querySelector("[data-close]").focus({ preventScroll: true });
    fetch("/api/scans/" + scan.id + "/image", { credentials: "same-origin" })
      .then(function (response) { return response.ok ? response.blob() : null; })
      .then(function (blob) {
        const photo = drawer.querySelector("[data-slot='photo']");
        if (drawer.hidden || !photo || !photo.isConnected) return;
        if (blob && photo) {
          drawerImageUrl = URL.createObjectURL(blob);
          photo.src = drawerImageUrl;
        }
        else if (photo) { photo.alt = "Image unavailable for scan " + scan.id; photo.classList.add("is-missing"); }
      })
      .catch(function () {
        const photo = drawer.querySelector("[data-slot='photo']");
        if (!drawer.hidden && photo && photo.isConnected) { photo.alt = "Image unavailable for scan " + scan.id; photo.classList.add("is-missing"); }
      });
    drawer.querySelector("[data-act='save-class']").onclick = async function () {
      const button = drawer.querySelector("[data-act='save-class']");
      const chosen = document.getElementById("drawer-class").value;
      if (!chosen && !scan.actual_class) {
        toast("Choose an actual class before saving.", "error");
        return;
      }
      button.disabled = true;
      button.textContent = "Saving…";
      try {
        const saved = await saveClass(scan.id, chosen);
        if (saved) openDrawer(saved);
      } catch (error) {
        if (error.status !== 401) toast(error.message, "error");
      } finally {
        if (button.isConnected) {
          button.disabled = false;
          button.textContent = "Save";
        }
      }
    };
    drawer.querySelector("[data-act='again']").onclick = async function () {
      try {
        const saved = await api("/api/scans/" + scan.id + "/reclassify", { method: "POST" });
        toast("Scan re-classified.", "ok");
        window.eggscanRecordCache[saved.id] = saved;
        await loadRecords();
        await loadOverview();
        openDrawer(saved);
      } catch (error) {
        if (error.status !== 401) toast(error.message, "error");
      }
    };
    drawer.querySelector("[data-act='delete']").onclick = async function () {
      const yes = await openModal({ title: "Delete scan", body: "Remove scan #" + scan.id + " and its image?", okLabel: "Delete" });
      if (!yes) return;
      try {
        await api("/api/scans/" + scan.id, { method: "DELETE" });
        toast("Scan deleted.", "ok");
        closeDrawer();
        await loadRecords();
        await loadOverview();
      } catch (error) {
        if (error.status !== 401) toast(error.message, "error");
      }
    };
  }

  function closeDrawer() {
    document.getElementById("def-drawer").hidden = true;
    document.getElementById("def-backdrop").hidden = true;
    if (drawerImageUrl) URL.revokeObjectURL(drawerImageUrl);
    drawerImageUrl = "";
    if (drawerReturnFocus && drawerReturnFocus.isConnected && typeof drawerReturnFocus.focus === "function") {
      drawerReturnFocus.focus({ preventScroll: true });
    }
    drawerReturnFocus = null;
  }

  window.eggscanAfterRecords = function (data) {
    const generation = ++recordImageGeneration;
    recordImageUrls.forEach(function (url) { URL.revokeObjectURL(url); });
    recordImageUrls = [];
    window.eggscanRecordCache = {};
    (data.items || []).forEach(function (item) { window.eggscanRecordCache[item.id] = item; });
    document.querySelectorAll(".def-thumb").forEach(function (img) {
      fetch("/api/scans/" + img.dataset.scanId + "/image", { credentials: "same-origin" })
        .then(function (response) { return response.ok ? response.blob() : null; })
        .then(function (blob) {
          if (generation !== recordImageGeneration || !img.isConnected) return;
          if (blob) {
            const url = URL.createObjectURL(blob);
            recordImageUrls.push(url);
            img.src = url;
          }
          else { img.alt = "Image unavailable for scan " + img.dataset.scanId; img.classList.add("is-missing"); }
        })
        .catch(function () { img.alt = "Image unavailable for scan " + img.dataset.scanId; img.classList.add("is-missing"); });
    });
    refreshBadge();
  };

  function bootDefense() {
    paintPipeline(pendingSteps(-1));
    const drawer = document.createElement("aside");
    drawer.id = "def-drawer";
    drawer.className = "def-drawer";
    drawer.hidden = true;
    drawer.innerHTML = '<div class="card-head"><h3>Scan detail</h3><button class="btn btn-ghost" type="button" data-close>Close</button></div><div data-slot="body"></div>';
    const backdrop = document.createElement("div");
    backdrop.id = "def-backdrop";
    backdrop.className = "def-backdrop";
    backdrop.hidden = true;
    document.body.appendChild(backdrop);
    document.body.appendChild(drawer);
    drawer.querySelector("[data-close]").onclick = closeDrawer;
    backdrop.onclick = closeDrawer;

    const sessionButton = document.getElementById("def-session");
    if (sessionButton) {
      sessionButton.onclick = function () {
        if (window.eggscanSession.active) {
          window.eggscanSession.active = false;
          sessionButton.textContent = "Start session";
        } else {
          window.eggscanSession = { active: true, id: "s" + Date.now().toString(36) };
          state.session = { total: 0, byClass: {}, msSum: 0 };
          sessionButton.textContent = "Stop session";
        }
        renderTally();
        if (typeof liveSyncSessionStatus === "function") liveSyncSessionStatus();
      };
    }

    document.addEventListener("keydown", function (event) {
      if (event.code !== "Space" || !window.eggscanSession.active || state.page !== "live") return;
      const tag = (event.target && event.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || event.target.isContentEditable) return;
      const modal = document.getElementById("modal");
      if (modal && !modal.hidden) return;
      event.preventDefault();
      captureFrame();
    });

    document.querySelectorAll("[data-def-go]").forEach(function (card) {
      card.addEventListener("click", function () {
        if (card.dataset.defGo === "records") openRecords({ correctness: card.dataset.defCorrect || "all" });
        else setPage(card.dataset.defGo);
      });
    });
    const days = document.getElementById("chart-days");
    if (days) days.addEventListener("click", function () { openRecords({}); });
    const dist = document.getElementById("dist-list");
    if (dist) {
      dist.addEventListener("click", function (event) {
        const row = event.target.closest("[data-class]");
        if (row) openRecords({ className: row.dataset.class });
      });
    }
    const progress = document.getElementById("def-progress");
    if (progress) {
      progress.addEventListener("click", function (event) {
        if (event.target.closest("#def-misses")) openRecords({ correctness: "miss" });
        if (event.target.closest("#def-unverified")) openRecords({ correctness: "unlabeled" });
      });
    }
    const goScan = document.getElementById("def-go-scan");
    if (goScan) goScan.onclick = function () { setPage("live"); };
    const goWeigh = document.getElementById("def-go-weigh");
    if (goWeigh) goWeigh.onclick = function () { openRecords({ correctness: "unlabeled" }); };
    const exportReport = document.getElementById("def-export");
    if (exportReport) {
      exportReport.onclick = async function () {
        const response = await fetch("/api/scans/export", { credentials: "same-origin" });
        if (!response.ok) {
          toast("Could not export the records.", "error");
          return;
        }
        const blob = await response.blob();
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = "eggscan-records.csv";
        link.click();
        URL.revokeObjectURL(link.href);
      };
    }

    const records = document.getElementById("rec-body");
    if (records) {
      records.addEventListener("click", function (event) {
        const view = event.target.closest("[data-open-record]");
        if (view) {
          openDrawer(window.eggscanRecordCache[view.dataset.openRecord]);
          return;
        }
        if (event.target.closest("button, input, a, label, select")) return;
        const row = event.target.closest("tr[data-id]");
        if (!row) return;
        openDrawer(window.eggscanRecordCache[row.dataset.id]);
      });
      records.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        const view = event.target.closest("[data-open-record]");
        if (!view) return;
        event.preventDefault();
        openDrawer(window.eggscanRecordCache[view.dataset.openRecord]);
      });
      records.addEventListener("change", async function (event) {
        const select = event.target.closest(".def-class");
        if (!select) return;
        try {
          await saveClass(select.dataset.id, select.value);
        } catch (error) {
          if (error.status !== 401) toast(error.message, "error");
        }
      });
    }
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-modal", "true");
    drawer.setAttribute("aria-labelledby", "def-drawer-title");
    drawer.tabIndex = -1;
    drawer.querySelector("h3").id = "def-drawer-title";
    document.addEventListener("keydown", function (event) {
      if (drawer.hidden) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeDrawer();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(drawer.querySelectorAll("button:not(:disabled), select:not(:disabled), input:not(:disabled), [tabindex]:not([tabindex='-1'])"));
      if (!focusable.length) { event.preventDefault(); drawer.focus(); return; }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });

    const weighTab = document.getElementById("rec-weigh");
    if (weighTab) weighTab.onclick = function () { openRecords({ correctness: "unlabeled" }); };

    const matrix = document.getElementById("model-matrix");
    if (matrix) {
      matrix.addEventListener("click", function (event) {
        const cell = event.target.closest("[data-actual]");
        if (!cell) return;
        openRecords({ className: cell.dataset.predicted, actualName: cell.dataset.actual });
      });
    }

  }

  bootDefense();
})();
