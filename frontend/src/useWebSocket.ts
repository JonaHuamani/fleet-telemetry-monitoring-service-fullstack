import { useEffect, useRef } from "react";

type Dispatch = (action: any) => void;

interface Options {
  onReconnect?: () => void;
}

export function useFleetWebSocket(dispatch: Dispatch, options: Options = {}) {
  const reconnectAttempts = useRef(0);
  const hasDisconnected = useRef(false);
  const onReconnectRef = useRef(options.onReconnect);

  useEffect(() => {
    onReconnectRef.current = options.onReconnect;
  }, [options.onReconnect]);

  useEffect(() => {
    let stop = false;
    let ws: WebSocket | null = null;
    let reconnectTimer: number | null = null;

    function connect() {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${protocol}//${window.location.host}/ws/stream`;
      dispatch({ type: "ws_status", status: "connecting" });
      ws = new WebSocket(url);

      ws.onopen = () => {
        reconnectAttempts.current = 0;
        dispatch({ type: "ws_status", status: "open" });
        // Re-seed via HTTP after every reconnect so the dashboard recovers
        // from any deltas that may have been missed while the socket was down.
        if (hasDisconnected.current && onReconnectRef.current) {
          onReconnectRef.current();
        }
      };

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg && msg.type) {
            dispatch({ type: msg.type, payload: msg.payload });
          }
        } catch (e) {
          console.error("ws parse failed", e);
        }
      };

      ws.onclose = () => {
        hasDisconnected.current = true;
        dispatch({ type: "ws_status", status: "closed" });
        if (stop) return;
        const delay = Math.min(10_000, 500 * 2 ** reconnectAttempts.current);
        reconnectAttempts.current += 1;
        reconnectTimer = window.setTimeout(connect, delay);
      };

      ws.onerror = () => {
        // onclose will follow.
      };
    }

    connect();

    return () => {
      stop = true;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [dispatch]);
}
