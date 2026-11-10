import { nextBackoffDelay } from "./backoff.js";

export const PING_INTERVAL_MS = 20000;
export const MAX_QUEUE = 100;

function deepEqual(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function createWsClient({
  url,
  WebSocketImpl,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  now = Date.now,
  rand = Math.random,
  version = "0",
  onState = () => {},
  onMessage = null,
}) {
  let state = "offline";
  let ws = null;
  let attempt = 0;
  let reconnectTimer = null;
  let pingTimer = null;
  let stopped = true;
  let lastTabs = null;
  let lastSentTabs = null;
  const queue = [];

  function setState(s) {
    if (state === s) return;
    state = s;
    onState(s);
  }

  function clearTimers() {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (pingTimer !== null) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
  }

  function rawSend(obj) {
    ws.send(JSON.stringify(obj));
  }

  function scheduleReconnect() {
    if (stopped || reconnectTimer !== null) return;
    const delay = nextBackoffDelay(attempt++, rand);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, delay);
  }

  function handleDown(socket) {
    if (socket !== ws) return;
    ws = null;
    if (pingTimer !== null) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
    setState("offline");
    scheduleReconnect();
  }

  function connect() {
    if (stopped || ws) return;
    setState("connecting");
    let socket;
    try {
      socket = new WebSocketImpl(url);
    } catch {
      setState("offline");
      scheduleReconnect();
      return;
    }
    ws = socket;
    socket.onopen = () => {
      if (socket !== ws) return;
      attempt = 0;
      setState("online");
      rawSend({ type: "hello", v: 1, client: "neuralmeter-bridge", version });
      if (lastTabs) {
        rawSend({ type: "status", tabs: lastTabs });
        lastSentTabs = lastTabs;
      }
      while (queue.length) rawSend(queue.shift());
      pingTimer = setInterval(() => {
        if (state === "online") rawSend({ type: "ping" });
      }, PING_INTERVAL_MS);
    };
    socket.onclose = () => handleDown(socket);
    socket.onerror = () => handleDown(socket);
    socket.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (onMessage) onMessage(msg);
    };
  }

  return {
    get state() {
      return state;
    },
    start() {
      if (!stopped && (ws || reconnectTimer !== null)) {
        // já ativo; se há reconexão agendada, antecipa
        if (reconnectTimer !== null && !ws) {
          clearTimeout(reconnectTimer);
          reconnectTimer = null;
          connect();
        }
        return;
      }
      stopped = false;
      connect();
    },
    stop() {
      stopped = true;
      clearTimers();
      const s = ws;
      ws = null;
      if (s) {
        try {
          s.close();
        } catch {
          /* ignore */
        }
      }
      setState("offline");
    },
    send(obj) {
      if (state === "online" && ws) {
        rawSend(obj);
        return;
      }
      queue.push(obj);
      while (queue.length > MAX_QUEUE) queue.shift();
    },
    setTabs(tabs) {
      if (lastSentTabs && deepEqual(lastSentTabs, tabs)) return;
      lastTabs = tabs;
      if (state === "online" && ws) {
        rawSend({ type: "status", tabs });
        lastSentTabs = tabs;
      }
    },
  };
}
