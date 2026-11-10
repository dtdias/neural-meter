// Parser SSE incremental (puro).
export function createSseParser() {
  let buf = "";

  function parseBlock(block) {
    let event = "message";
    const data = [];
    let has = false;
    for (const line of block.split("\n")) {
      if (line === "" || line.startsWith(":")) continue;
      const i = line.indexOf(":");
      const field = i === -1 ? line : line.slice(0, i);
      let value = i === -1 ? "" : line.slice(i + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value;
      else if (field === "data") {
        data.push(value);
        has = true;
      }
    }
    return has ? { event, data: data.join("\n") } : null;
  }

  function drain(final) {
    buf = buf.replace(/\r\n/g, "\n").replace(/\r(?!$)/g, "\n");
    const out = [];
    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const ev = parseBlock(buf.slice(0, idx));
      buf = buf.slice(idx + 2);
      if (ev) out.push(ev);
    }
    if (final && buf.trim() !== "") {
      const ev = parseBlock(buf.replace(/\r/g, ""));
      if (ev) out.push(ev);
      buf = "";
    }
    return out;
  }

  return {
    push(text) {
      buf += text;
      return drain(false);
    },
    flush() {
      return drain(true);
    },
  };
}
