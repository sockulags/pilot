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

export default function Home() {
  const [log, setLog] = useState<LogEvent[]>([]);
  const [running, setRunning] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const idRef = useRef(0);

  const addEvent = useCallback((e: Omit<LogEvent, "id" | "ts">) => {
    setLog((prev) => [...prev, { ...e, id: idRef.current++, ts: Date.now() }]);
  }, []);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return wsRef.current;
    const ws = new WebSocket(WS_URL);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data) as LogEvent;
      if (msg.type === "done" || msg.type === "error") setRunning(false);
      addEvent(msg);
    };
    ws.onerror = () => addEvent({ type: "error", content: "WebSocket connection failed" });
    wsRef.current = ws;
    return ws;
  }, [addEvent]);

  const handleRun = useCallback(
    (task: string) => {
      setLog([]);
      setRunning(true);
      const ws = connect();
      const send = () => ws.send(JSON.stringify({ type: "run", task }));
      if (ws.readyState === WebSocket.OPEN) send();
      else ws.onopen = send;
    },
    [connect]
  );

  const handleAbort = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: "abort" }));
    setRunning(false);
  }, []);

  useEffect(() => () => wsRef.current?.close(), []);

  return (
    <main style={{ display: "flex", flexDirection: "column", height: "100dvh", padding: "1rem", gap: "0.75rem", maxWidth: 800, margin: "0 auto" }}>
      <header style={{ paddingBottom: "0.5rem", borderBottom: "1px solid var(--border)" }}>
        <h1 style={{ fontSize: "1.25rem", fontWeight: 700, color: "var(--accent)" }}>Pilot</h1>
        <p style={{ fontSize: "0.8rem", color: "var(--muted)" }}>Local AI computer agent</p>
      </header>

      <TaskInput onRun={handleRun} disabled={running} />

      {running && <AbortButton onAbort={handleAbort} />}

      <ActionLog events={log} />
    </main>
  );
}
