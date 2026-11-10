import assert from "node:assert/strict";
import test from "node:test";

import { createWsClient } from "../src/ws_client.js";

class FakeWebSocket {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.sent = [];
    this.closed = false;
    FakeWebSocket.instances.push(this);
  }
  send(d) {
    this.sent.push(JSON.parse(d));
  }
  close() {
    this.closed = true;
  }
  open() {
    this.onopen?.();
  }
  drop() {
    this.onclose?.();
  }
  message(data) {
    this.onmessage?.({ data });
  }
}

function setup(extra = {}) {
  FakeWebSocket.instances = [];
  let t = 0;
  let id = 0;
  const timeouts = new Map();
  const intervals = new Map();
  const states = [];
  const delays = [];
  const client = createWsClient({
    url: "ws://x",
    WebSocketImpl: FakeWebSocket,
    setTimeout: (fn, ms) => {
      timeouts.set(++id, { fn, at: t + ms });
      delays.push(ms);
      return id;
    },
    clearTimeout: (h) => timeouts.delete(h),
    setInterval: (fn, ms) => {
      intervals.set(++id, { fn, ms, next: t + ms });
      return id;
    },
    clearInterval: (h) => intervals.delete(h),
    now: () => t,
    rand: () => 0.5,
    version: "1.2.3",
    onState: (s) => states.push(s),
    ...extra,
  });
  function advance(ms) {
    const end = t + ms;
    for (;;) {
      const cands = [
        ...[...timeouts].map(([h, v]) => ({ h, at: v.at, kind: "t" })),
        ...[...intervals].map(([h, v]) => ({ h, at: v.next, kind: "i" })),
      ].filter((c) => c.at <= end);
      if (!cands.length) break;
      cands.sort((a, b) => a.at - b.at);
      const c = cands[0];
      t = c.at;
      if (c.kind === "t") {
        const { fn } = timeouts.get(c.h);
        timeouts.delete(c.h);
        fn();
      } else {
        const v = intervals.get(c.h);
        v.next += v.ms;
        v.fn();
      }
    }
    t = end;
  }
  const last = () => FakeWebSocket.instances.at(-1);
  return { client, advance, states, delays, last, timeouts };
}

test("hello e a primeira mensagem, depois status e fila", () => {
  const { client, last } = setup();
  client.start();
  client.send({ type: "usage", n: 1 });
  client.setTabs({ claude: true, chatgpt: false });
  last().open();
  assert.deepEqual(last().sent, [
    { type: "hello", v: 1, client: "neuralmeter-bridge", version: "1.2.3" },
    { type: "status", tabs: { claude: true, chatgpt: false } },
    { type: "usage", n: 1 },
  ]);
});

test("fila na ordem e limite de 100 descarta as mais antigas", () => {
  const { client, last } = setup();
  client.start();
  for (let i = 0; i < 105; i++) client.send({ type: "usage", i });
  last().open();
  const usage = last().sent.slice(1);
  assert.equal(usage.length, 100);
  assert.equal(usage[0].i, 5);
  assert.equal(usage[99].i, 104);
});

test("send online vai direto", () => {
  const { client, last } = setup();
  client.start();
  last().open();
  client.send({ type: "usage", i: 1 });
  assert.deepEqual(last().sent.at(-1), { type: "usage", i: 1 });
});

test("ping a cada 20s online", () => {
  const { client, last, advance } = setup();
  client.start();
  last().open();
  advance(19999);
  assert.equal(last().sent.filter((m) => m.type === "ping").length, 0);
  advance(1);
  assert.equal(last().sent.filter((m) => m.type === "ping").length, 1);
});

test("close agenda reconexao com delay crescente; status reenviado", () => {
  const { client, last, advance, delays } = setup();
  client.start();
  client.setTabs({ claude: true, chatgpt: true });
  last().open();
  last().drop();
  assert.equal(client.state, "offline");
  advance(1000);
  assert.equal(FakeWebSocket.instances.length, 2);
  last().drop(); // falha sem abrir
  advance(1999);
  assert.equal(FakeWebSocket.instances.length, 2);
  advance(1);
  assert.equal(FakeWebSocket.instances.length, 3);
  assert.deepEqual(delays.filter((d) => d !== 20000), [1000, 2000]);
  last().open();
  assert.deepEqual(last().sent[1], { type: "status", tabs: { claude: true, chatgpt: true } });
});

test("attempt zera ao abrir", () => {
  const { client, last, advance, delays } = setup();
  client.start();
  last().open();
  last().drop();
  advance(1000);
  last().open();
  last().drop();
  assert.deepEqual(delays.filter((d) => d !== 20000), [1000, 1000]);
  advance(1000);
});

test("stop nao reconecta", () => {
  const { client, last, advance } = setup();
  client.start();
  last().open();
  const s = last();
  client.stop();
  assert.ok(s.closed);
  s.drop();
  advance(60000);
  assert.equal(FakeWebSocket.instances.length, 1);
  assert.equal(client.state, "offline");
});

test("setTabs repetido nao reenvia", () => {
  const { client, last } = setup();
  client.start();
  last().open();
  client.setTabs({ claude: true, chatgpt: false });
  client.setTabs({ claude: true, chatgpt: false });
  assert.equal(last().sent.filter((m) => m.type === "status").length, 1);
  client.setTabs({ claude: false, chatgpt: false });
  assert.equal(last().sent.filter((m) => m.type === "status").length, 2);
});

test("onState connecting -> online -> offline", () => {
  const { client, last, states } = setup();
  client.start();
  last().open();
  last().drop();
  assert.deepEqual(states, ["connecting", "online", "offline"]);
});

test("mensagens invalidas ignoradas; validas vao ao onMessage", () => {
  const got = [];
  const { client, last } = setup({ onMessage: (m) => got.push(m) });
  client.start();
  last().open();
  last().message("nao json");
  last().message('{"type":"pong"}');
  assert.deepEqual(got, [{ type: "pong" }]);
});
