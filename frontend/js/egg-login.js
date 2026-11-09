(function () {
  const EGGS = [
    ["14px", "18px"],
    ["18px", "23px"],
    ["22px", "28px"],
    ["25px", "31px"],
    ["28px", "34px"],
  ];
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const view = document.getElementById("login-view");
  const stage = document.getElementById("egg-stage");
  const list = document.getElementById("lg-sizes");
  let mounted = false;
  let raf = 0;
  let renderer = null;
  let resources = [];
  let resizeObserver = null;
  let cycleTimer = 0;
  let cycleIndex = 0;
  let cardsPaused = false;
  let dragging = false;
  let yaw = 0.35;
  let pitch = 0.06;
  let velYaw = 0;
  let velPitch = 0;
  let camZ = 5.15;
  let egg = null;
  let camera = null;
  let scene = null;
  let lastX = 0;
  let lastY = 0;

  function track(item) {
    resources.push(item);
    return item;
  }

  function paintCards(labels) {
    if (!list || list.childElementCount) return;
    const names = labels && labels.length ? labels : [];
    if (!names.length) return;
    list.innerHTML = names.map((label, index) => {
      const [w, h] = EGGS[index] || EGGS[EGGS.length - 1];
      return `<li class="lg-size"><span class="lg-size-icon" style="--lg-egg-w:${w};--lg-egg-h:${h}" aria-hidden="true"></span><span>${label}</span></li>`;
    }).join("");
    list.addEventListener("mouseenter", () => { cardsPaused = true; });
    list.addEventListener("mouseleave", () => { cardsPaused = false; });
    highlight(0);
    if (!reduced) cycleTimer = window.setInterval(cycle, 2500);
  }

  function highlight(index) {
    if (!list) return;
    [...list.children].forEach((item, itemIndex) => item.classList.toggle("is-on", itemIndex === index && !reduced));
  }

  function cycle() {
    if (cardsPaused || !view || view.hidden) return;
    cycleIndex = (cycleIndex + 1) % Math.max(list.children.length, 1);
    highlight(cycleIndex);
  }

  function paintStage() {
    stage.innerHTML = `<div class="lg-webgl"></div>
      <div class="lg-fx" aria-hidden="true">
        <span class="lg-arc"></span>
        <span class="lg-bracket lg-b1"></span>
        <span class="lg-bracket lg-b2"></span>
        <span class="lg-bracket lg-b3"></span>
        <span class="lg-bracket lg-b4"></span>
        <span class="lg-scan"></span>
      </div>
      <div class="lg-fallback" hidden><div class="lg-fallback-egg" role="img" aria-label="Egg"></div></div>`;
  }

  function showFallback() {
    const fallback = stage.querySelector(".lg-fallback");
    const webgl = stage.querySelector(".lg-webgl");
    if (fallback) fallback.hidden = false;
    if (webgl) webgl.hidden = true;
  }

  function speckles() {
    const canvas = document.createElement("canvas");
    canvas.width = 256;
    canvas.height = 256;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#d7a67c";
    ctx.fillRect(0, 0, 256, 256);
    for (let i = 0; i < 1400; i += 1) {
      const shade = 150 + Math.floor(Math.random() * 70);
      ctx.fillStyle = `rgba(${shade - 30}, ${shade - 55}, ${shade - 80}, ${0.15 + Math.random() * 0.35})`;
      ctx.fillRect(Math.random() * 256, Math.random() * 256, Math.random() * 2.2, Math.random() * 2.2);
    }
    const texture = track(new THREE.CanvasTexture(canvas));
    if (THREE.sRGBEncoding) texture.encoding = THREE.sRGBEncoding;
    return texture;
  }

  function mount() {
    if (!stage || !view || view.hidden || mounted) return;
    mounted = true;
    paintStage();
    if (!window.THREE) {
      showFallback();
      return;
    }
    const host = stage.querySelector(".lg-webgl");
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch (error) {
      showFallback();
      mounted = false;
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);
    if (THREE.ACESFilmicToneMapping) {
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 0.92;
    }
    if (THREE.sRGBEncoding) renderer.outputEncoding = THREE.sRGBEncoding;
    host.appendChild(renderer.domElement);

    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(30, 1, 0.1, 30);
    camera.position.set(0, 0.22, camZ);

    const map = speckles();
    const shell = track(new THREE.SphereGeometry(1, 64, 64));
    const position = shell.attributes.position;
    for (let i = 0; i < position.count; i += 1) {
      const x = position.getX(i);
      const y = position.getY(i);
      const z = position.getZ(i);
      const narrower = 1 - 0.18 * Math.max(y, 0);
      position.setXYZ(i, x * 0.72 * narrower, y * 1.18, z * 0.72 * narrower);
    }
    shell.computeVertexNormals();
    const Material = THREE.MeshPhysicalMaterial || THREE.MeshStandardMaterial;
    const material = track(new Material({
      color: 0xc98f63,
      map,
      bumpMap: map,
      bumpScale: 0.04,
      roughness: 0.5,
      metalness: 0.02,
      clearcoat: 0.12,
      clearcoatRoughness: 0.5,
    }));
    egg = new THREE.Mesh(shell, material);
    egg.position.y = 0.22;
    egg.scale.setScalar(0.86);
    scene.add(egg);

    scene.add(new THREE.AmbientLight(0xfff4e8, 0.26));
    scene.add(new THREE.HemisphereLight(0xfff6ea, 0x16382c, 0.38));
    const key = new THREE.DirectionalLight(0xfff1df, 0.7);
    key.position.set(2.4, 3.6, 3.4);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xc98f63, 0.16);
    fill.position.set(-2.2, 0.4, 2);
    scene.add(fill);
    const rim = new THREE.DirectionalLight(0x8dffc2, 0.42);
    rim.position.set(-3.4, 1.4, -2.6);
    scene.add(rim);

    const podium = new THREE.Mesh(
      track(new THREE.CylinderGeometry(1.02, 1.16, 0.16, 48)),
      track(new THREE.MeshStandardMaterial({ color: 0x10281f, roughness: 0.72, metalness: 0.08 }))
    );
    podium.position.y = -0.92;
    scene.add(podium);
    const ring = new THREE.Mesh(
      track(new THREE.TorusGeometry(1.05, 0.018, 12, 64)),
      track(new THREE.MeshStandardMaterial({ color: 0xe9b44c, roughness: 0.35, metalness: 0.4, emissive: 0x6a4a12, emissiveIntensity: 0.25 }))
    );
    ring.rotation.x = Math.PI / 2;
    ring.position.y = -0.84;
    scene.add(ring);
    const glow = new THREE.Mesh(
      track(new THREE.CircleGeometry(0.92, 40)),
      track(new THREE.MeshBasicMaterial({ color: 0x3c9a6e, transparent: true, opacity: 0.28 }))
    );
    glow.rotation.x = -Math.PI / 2;
    glow.position.y = -0.83;
    scene.add(glow);
    const shadow = new THREE.Mesh(
      track(new THREE.CircleGeometry(0.72, 32)),
      track(new THREE.MeshBasicMaterial({ color: 0x04140f, transparent: true, opacity: 0.38 }))
    );
    shadow.rotation.x = -Math.PI / 2;
    shadow.position.y = -0.8;
    shadow.scale.set(1, 0.72, 1);
    scene.add(shadow);

    stage.addEventListener("pointerdown", onDown);
    stage.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    stage.addEventListener("wheel", onWheel, { passive: false });
    resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(stage);
    resize();
    loop();
  }

  function resize() {
    if (!renderer || !camera || !stage.isConnected) return;
    const width = stage.clientWidth || 320;
    const height = stage.clientHeight || 280;
    renderer.setSize(width, height, false);
    camera.aspect = width / Math.max(height, 1);
    camera.updateProjectionMatrix();
  }

  function onDown(event) {
    dragging = true;
    lastX = event.clientX;
    lastY = event.clientY;
    velYaw = 0;
    velPitch = 0;
  }

  function onMove(event) {
    if (!dragging) return;
    const dx = event.clientX - lastX;
    const dy = event.clientY - lastY;
    lastX = event.clientX;
    lastY = event.clientY;
    velYaw = dx * 0.012;
    velPitch = dy * 0.006;
    yaw += velYaw;
    pitch = Math.max(-0.5, Math.min(0.55, pitch + velPitch));
    if (egg) {
      egg.rotation.y = yaw;
      egg.rotation.x = pitch;
    }
    if (renderer && scene && camera) renderer.render(scene, camera);
  }

  function onUp() {
    dragging = false;
  }

  function onWheel(event) {
    event.preventDefault();
    camZ = Math.max(3.5, Math.min(6.3, camZ + event.deltaY * 0.002));
  }

  function loop() {
    raf = requestAnimationFrame(loop);
    if (!mounted || !renderer || document.hidden || view.hidden) return;
    if (!dragging) {
      yaw += velYaw;
      pitch = Math.max(-0.5, Math.min(0.55, pitch + velPitch));
      velYaw *= 0.94;
      velPitch *= 0.94;
      if (!reduced && Math.abs(velYaw) < 0.0008) yaw += 0.004;
    }
    if (egg) {
      egg.rotation.y = yaw;
      egg.rotation.x = pitch;
    }
    camera.position.z = camZ;
    camera.lookAt(0, -0.08, 0);
    renderer.render(scene, camera);
  }

  function dispose() {
    cancelAnimationFrame(raf);
    raf = 0;
    if (resizeObserver) resizeObserver.disconnect();
    resizeObserver = null;
    if (stage) {
      stage.removeEventListener("pointerdown", onDown);
      stage.removeEventListener("pointermove", onMove);
      stage.removeEventListener("wheel", onWheel);
    }
    window.removeEventListener("pointerup", onUp);
    resources.forEach((item) => item.dispose && item.dispose());
    resources = [];
    if (renderer) {
      renderer.dispose();
      renderer.forceContextLoss && renderer.forceContextLoss();
      renderer = null;
    }
    egg = null;
    camera = null;
    scene = null;
    mounted = false;
    if (stage) stage.innerHTML = "";
  }

  const previousStop = window.stopEgg;
  window.startEgg = function () {
    if (view && !view.hidden) mount();
  };
  window.stopEgg = function () {
    dispose();
  };

  paintCards(["Small", "Medium", "Large", "Extra Large", "Jumbo"]);
  if (typeof previousStop === "function") previousStop();
  const leftover = stage && stage.querySelector("canvas");
  if (leftover) {
    const gl = leftover.getContext("webgl2") || leftover.getContext("webgl");
    const lose = gl && gl.getExtension("WEBGL_lose_context");
    if (lose) lose.loseContext();
    leftover.remove();
  }
  document.addEventListener("visibilitychange", () => {
    if (!mounted) return;
    if (document.hidden) {
      cancelAnimationFrame(raf);
      raf = 0;
    } else if (!raf && view && !view.hidden) {
      loop();
    }
  });
  if (!view || !view.hidden) mount();

  window.lgDisposeLoginEgg = dispose;
})();
