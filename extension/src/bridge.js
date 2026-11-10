// ISOLATED world, classic. Recebe dados crus do page_hook, usa lógica pura, envia ao SW.
(function () {
  if (location.hostname !== "claude.ai") return; // chatgpt: próxima etapa
  const debug = (() => {
    try {
      return localStorage.neuralmeterDebug === "1";
    } catch (e) {
      return false;
    }
  })();
  const queue = [];
  let capture = null;

  function emit(m) {
    try {
      chrome.runtime.sendMessage(m).catch(() => {});
    } catch (e) {}
  }
  function onUnknown(type, info) {
    if (debug) console.debug("[NeuralMeter]", "unknown", type, info);
  }

  function handle(d) {
    try {
      if (d.k === "req") capture.onRequest(d.id, d.url, d.method, d.body);
      else if (d.k === "resp") capture.onResponse(d.id, d.status, d.body);
      else if (d.k === "chunk") capture.onChunk(d.id, d.text);
      else if (d.k === "end") capture.onEnd(d.id);
    } catch (e) {}
  }

  window.addEventListener("message", (ev) => {
    const d = ev.data;
    if (ev.source !== window || !d || d.__nm !== 1) return;
    if (capture) handle(d);
    else queue.push(d);
  });

  (async () => {
    try {
      const url = (p) => chrome.runtime.getURL(p);
      const [cap, trk] = await Promise.all([
        import(url("src/claude_capture.js")),
        import(url("src/context_tracker.js")),
      ]);
      capture = cap.createClaudeCapture({
        emit,
        tracker: trk.createContextTracker({ max: 50 }),
        onUnknown,
        now: Date.now,
      });
      while (queue.length) handle(queue.shift());
    } catch (e) {}
  })();
})();
