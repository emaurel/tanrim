import type { WireEvent } from "../types";

type Listener = (e: WireEvent) => void;

const listeners = new Set<Listener>();
let socket: WebSocket | null = null;
let reconnectTimer: number | null = null;

function ensureConnected(): void {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }
  const url = `ws://${location.hostname}:${location.port}/ws`;
  socket = new WebSocket(url);
  socket.onmessage = (msg) => {
    try {
      const e = JSON.parse(msg.data) as WireEvent;
      for (const fn of listeners) fn(e);
    } catch (err) {
      console.error("bad ws frame", err);
    }
  };
  socket.onclose = () => {
    socket = null;
    if (reconnectTimer === null) {
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        if (listeners.size > 0) ensureConnected();
      }, 1000);
    }
  };
  socket.onerror = () => socket?.close();
}

export function subscribe(fn: Listener): () => void {
  listeners.add(fn);
  ensureConnected();
  return () => listeners.delete(fn);
}
