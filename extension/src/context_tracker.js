// Contexto acumulado por conversa (LRU, puro).
export function createContextTracker({ max = 50 } = {}) {
  const map = new Map();
  return {
    get(convId) {
      if (!convId || !map.has(convId)) return 0;
      const v = map.get(convId);
      map.delete(convId);
      map.set(convId, v);
      return v;
    },
    add(convId, chars) {
      if (!convId) return;
      const v = (map.get(convId) || 0) + chars;
      map.delete(convId);
      map.set(convId, v);
      while (map.size > max) map.delete(map.keys().next().value);
    },
  };
}
