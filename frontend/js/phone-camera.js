(function () {
  const video = document.getElementById("phone-preview");
  const status = document.getElementById("phone-status");
  const startButton = document.getElementById("phone-start");
  const stopButton = document.getElementById("phone-stop");
  let stream = null;
  let peer = null;
  let socket = null;
  let remoteCandidates = [];
  let stopped = false;
  let pairingCredentialUsed = false;

  function setStatus(message, failed) {
    status.textContent = message;
    status.classList.toggle("error", !!failed);
  }

  async function selectedIcePath(connection) {
    try {
      const stats = await connection.getStats();
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

  function pairParts() {
    const value = location.hash.slice(1).split(".");
    return value.length === 2 && value[0] && value[1] ? { session_id: value[0], token: value[1] } : null;
  }

  async function validatePairing() {
    const pairing = pairParts();
    if (!pairing) {
      const error = new Error("This pairing link is missing or expired. Create a new code on the computer.");
      error.pairingExpired = true;
      throw error;
    }
    let response;
    try {
      response = await fetch("/api/camera/phone/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pairing),
        cache: "no-store",
      });
    } catch (cause) {
      const error = new Error("Could not reach EggScan. Check the internet connection and public HTTPS link, then try again.");
      error.userMessage = error.message;
      throw error;
    }
    let result = {};
    try { result = await response.json(); } catch (error) { /* use a clear fallback below */ }
    if (!response.ok || !result.valid) {
      const error = new Error(result.detail || "This pairing code is invalid or expired. Create a new code on the computer.");
      error.pairingExpired = response.status === 400;
      throw error;
    }
    return {
      ...pairing,
      iceServers: Array.isArray(result.ice_servers) ? result.ice_servers : [],
      turnAvailable: !!result.turn_available,
    };
  }

  function stopSharing(message) {
    stopped = true;
    if (socket && socket.readyState === WebSocket.OPEN) {
      try { socket.send(JSON.stringify({ type: "disconnect" })); } catch (error) { /* connection is closing */ }
    }
    if (socket) socket.close();
    socket = null;
    if (peer) peer.close();
    peer = null;
    if (stream) stream.getTracks().forEach((track) => track.stop());
    stream = null;
    video.srcObject = null;
    remoteCandidates = [];
    startButton.disabled = pairingCredentialUsed;
    stopButton.disabled = true;
    setStatus(message || (pairingCredentialUsed
      ? "This pairing code has been used. Return to the computer and scan a new code to reconnect."
      : "Sharing stopped."));
  }

  async function addCandidates() {
    if (!peer || !peer.remoteDescription) return;
    for (const candidate of remoteCandidates.splice(0)) await peer.addIceCandidate(candidate);
  }

  async function handleSignal(event) {
    if (stopped || !peer) return;
    let message;
    try { message = JSON.parse(event.data); } catch (error) { return; }
    if (message.type === "offer" && message.description) {
      await peer.setRemoteDescription(message.description);
      await addCandidates();
      for (const track of stream.getTracks()) {
        if (!peer.getSenders().some((sender) => sender.track === track)) peer.addTrack(track, stream);
      }
      const answer = await peer.createAnswer();
      await peer.setLocalDescription(answer);
      socket.send(JSON.stringify({ type: "answer", description: peer.localDescription }));
      setStatus("Connecting to EggScan...");
    } else if (message.type === "candidate" && message.candidate) {
      if (peer.remoteDescription) await peer.addIceCandidate(message.candidate);
      else remoteCandidates.push(message.candidate);
    } else if (message.type === "disconnect") {
      stopSharing("Connection ended. Return to the computer to create a new pairing code.");
    }
  }

  async function startSharing() {
    if (location.protocol !== "https:") {
      setStatus("Open this pairing link over trusted HTTPS before sharing the camera.", true);
      return;
    }
    startButton.disabled = true;
    stopped = false;
    try {
      setStatus("Checking the pairing code...");
      const pairing = await validatePairing();
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        const error = new Error("This browser cannot access the camera. Open the link in your phone's current browser over HTTPS.");
        error.userMessage = error.message;
        throw error;
      }
      setStatus(pairing.turnAvailable
        ? "Pairing is valid. Requesting camera access..."
        : "Pairing is valid. TURN is not configured, so some mobile networks may block the video connection. Requesting camera access...");
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      video.srcObject = stream;
      stopButton.disabled = false;
      peer = new RTCPeerConnection({ iceServers: pairing.iceServers });
      peer.onicecandidate = (event) => {
        if (event.candidate && socket && socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: "candidate", candidate: event.candidate.toJSON() }));
        }
      };
      peer.onconnectionstatechange = async () => {
        if (!peer) return;
        if (peer.connectionState === "connected") {
          const activePeer = peer;
          const path = await selectedIcePath(activePeer);
          if (peer !== activePeer) return;
          setStatus("Connected" + (path ? " via " + path : "") + ". Keep this page open while EggScan is scanning.");
        } else if (peer.connectionState === "failed") {
          setStatus("WebRTC could not connect the video. Check TURN relay setup and both networks.", true);
        } else if (peer.connectionState === "disconnected") {
          setStatus("Connection interrupted. Check internet access and TURN relay setup, then reconnect if needed.", true);
        }
      };
      const scheme = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(scheme + "//" + location.host + "/api/camera/phone/signal");
      socket.onopen = () => {
        pairingCredentialUsed = true;
        socket.send(JSON.stringify({
          type: "join",
          role: "phone",
          session_id: pairing.session_id,
          token: pairing.token,
        }));
      };
      socket.onmessage = (event) => handleSignal(event).catch(() => setStatus("Could not negotiate the video connection. Create a new pairing code and retry.", true));
      socket.onerror = () => setStatus("Could not reach EggScan signaling. Check the public HTTPS link and internet access.", true);
      socket.onclose = (event) => {
        if (!stopped && event.code !== 1000) stopSharing("Pairing expired or the computer disconnected. Create a new pairing code to reconnect.");
      };
      setStatus("Camera ready. Waiting for EggScan to connect...");
    } catch (error) {
      const message = error.userMessage || (error.pairingExpired
        ? error.message
        : error.name === "NotAllowedError"
        ? "Camera permission was denied. Allow camera access in the browser settings, then tap Start sharing."
        : (error.name === "NotFoundError" || error.name === "OverconstrainedError")
          ? "No suitable phone camera was found. Check that a camera is available and try again."
          : "Could not open the phone camera. Check permissions and try again.");
      stopSharing(message);
      startButton.disabled = !!error.pairingExpired || pairingCredentialUsed;
    }
  }

  startButton.addEventListener("click", startSharing);
  stopButton.addEventListener("click", () => stopSharing());
  window.addEventListener("pagehide", () => stopSharing("Sharing stopped."));
  validatePairing().then((pairing) => {
    setStatus(pairing.turnAvailable
      ? "Pairing is ready. Tap Start Sharing and allow camera access when prompted."
      : "Pairing is ready. Tap Start Sharing. No TURN relay is configured, so some mobile networks may not connect.");
    startButton.disabled = false;
  }).catch((error) => {
    setStatus(error.message || "This pairing code is invalid or expired. Create a new code on the computer.", true);
    startButton.disabled = !!error.pairingExpired;
  });
})();
