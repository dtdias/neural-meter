// MAIN world, classic. Sem logica de dominio: so repassa dados crus via postMessage.
(function () {
  if (window.__nmHooked) return;
  window.__nmHooked = true;
  const orig = window.fetch;
  let seq = 0;
  const post = (o) => {
    try {
      o.__nm = 1;
      window.postMessage(o, "*");
    } catch (e) {}
  };

  const matchers = {
    "claude.ai": /\/completion$/,
    "chatgpt.com": /\/backend-api\/(f\/)?conversation(\?|$)/,
  };
  const rx = matchers[location.hostname];
  const wanted = (url) => {
    if (!rx) return false;
    return rx.test(rx === matchers["claude.ai"] ? url.split("?")[0].split("#")[0] : url);
  };

  async function pump(id, resp) {
    try {
      if (resp.status >= 400) {
        post({ k: "resp", id, status: resp.status, body: await resp.clone().text() });
        return;
      }
      post({ k: "resp", id, status: resp.status });
      const c = resp.clone();
      if (!c.body) return;
      const reader = c.body.getReader();
      const dec = new TextDecoder();
      for (;;) {
        const r = await reader.read();
        if (r.done) break;
        post({ k: "chunk", id, text: dec.decode(r.value, { stream: true }) });
      }
      post({ k: "end", id });
    } catch (e) {}
  }

  window.fetch = function (input, init) {
    const p = orig.apply(this, arguments);
    try {
      // input Request sem init: ignora (nao le body, evita consumir stream)
      const url = typeof input === "string" ? input : (input && input.url) || String(input);
      if (init && typeof init.body === "string" && wanted(url)) {
        const id = "r" + ++seq;
        post({ k: "req", id, url, method: init.method || "GET", body: init.body });
        p.then((resp) => pump(id, resp), () => {});
      }
    } catch (e) {}
    return p;
  };
})();
