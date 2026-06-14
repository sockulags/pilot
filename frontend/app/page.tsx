"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import TaskInput from "@/components/TaskInput";
import ActionLog from "@/components/ActionLog";
import AbortButton from "@/components/AbortButton";

export type LogEvent = {
  id: number;
  type: "thinking" | "action" | "result" | "screenshot" | "done" | "error";
  content?: string;
  tool?: string;
  args?: Record<string, unknown>;
  image?: string;
  summary?: string;
  ts: number;
};

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";

type WsStatus = "disconnected" | "connecting" | "connected" | "error";

const STATUS_LABEL: Record<WsStatus, string> = {
  disconnected: "Frånkopplad",
  connecting: "Ansluter...",
  connected: "Ansluten",
  error: "Anslutningsfel",
};
const STATUS_COLOR: Record<WsStatus, string> = {
  disconnected: "var(--muted)",
  connecting: "var(--yellow)",
  connected: "var(--green)",
  error: "var(--red)",
};

const RECONNECT_DELAY = 3000;

export default function Home() {
  const [log, setLog] = useState<LogEvent[]>([]);
  const [running, _setRunning] = useState(false);
  const [wsStatus, setWsStatus] = useState<WsStatus>("disconnected");
  const wsRef = useRef<WebSocket | null>(null);
  const idRef = useRef(0);
  const runningRef = useRef(false);

  const addEvent = useCallback((e: Omit<LogEvent, "id" | "ts">) => {
    setLog((prev) => [...prev, { ...e, id: idRef.current++, ts: Date.now() }]);
  }, []);

  // Keeps runningRef in sync so WS callbacks can read current value without stale closure
  const setRunning = useCallback((val: boolean) => {
    runningRef.current = val;
    _setRunning(val);
  }, []);

  useEffect(() => {
    let dead = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    function openWs() {
      if (dead) return;
      setWsStatus("connecting");
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => setWsStatus("connected");

      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data) as LogEvent;
        if (msg.type === "done" || msg.type === "error") setRunning(false);
        addEvent(msg);
      };

      ws.onerror = () => {
        setWsStatus("error");
        if (runningRef.current) {
          setRunning(false);
          addEvent({ type: "error", content: `Anslutning bröts — ${WS_URL}` });
        }
      };

      ws.onclose = () => {
        if (dead) return;
        setWsStatus("disconnected");
        if (runningRef.current) setRunning(false);
        retryTimer = setTimeout(openWs, RECONNECT_DELAY);
      };
    }

    openWs();
    return () => {
      dead = true;
      if (retryTimer) clearTimeout(retryTimer);
      wsRef.current?.close();
    };
  }, [addEvent, setRunning]);

  const handleRun = useCallback(
    (task: string) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        addEvent({ type: "error", content: "Inte ansluten — vänta på Ansluten-status och försök igen" });
        return;
      }
      setLog([]);
      setRunning(true);
      ws.send(JSON.stringify({ type: "run", task }));
    },
    [addEvent, setRunning]
  );

  const handleAbort = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: "abort" }));
    setRunning(false);
  }, [setRunning]);

  return (
    <main style={{ display: "flex", flexDirection: "column", height: "100dvh", padding: "1rem", gap: "0.75rem", maxWidth: 800, margin: "0 auto" }}>
      <header style={{ paddingBottom: "0.5rem", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h1 style={{ fontSize: "1.25rem", fontWeight: 700, color: "var(--accent)" }}>Pilot</h1>
          <p style={{ fontSize: "0.8rem", color: "var(--muted)" }}>Local AI computer agent</p>
        </div>
        <span style={{ fontSize: "0.75rem", fontWeight: 600, color: STATUS_COLOR[wsStatus] }}>
          ● {STATUS_LABEL[wsStatus]}
        </span>
      </header>

      <TaskInput onRun={handleRun} disabled={running} />

      {running && <AbortButton onAbort={handleAbort} />}

      <ActionLog events={log} />
    </main>
  );
}
