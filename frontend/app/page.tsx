"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import ChatInput from "@/components/TaskInput";
import Transcript from "@/components/ActionLog";
import AbortButton from "@/components/AbortButton";
import ProjectBar from "@/components/ProjectBar";

export type Route = "chat" | "computer" | "code";

export type Project = { id: string; name: string; path: string };

// A selectable local model offered by the backend (plus the "auto" pseudo-mode).
export type ModelOption = { id: string; label: string; hint: string };

// Raw event coming over the WebSocket from the backend.
export type ServerEvent = {
  type:
    | "history"
    | "projects"
    | "turn_start"
    | "assistant_delta"
    | "thinking"
    | "context"
    | "action"
    | "consult"
    | "expert_delta"
    | "codex_trace"
    | "result"
    | "screenshot"
    | "done"
    | "error"
    | "reset_ok";
  turn?: number;
  route?: Route;
  content?: string;
  tool?: string;
  args?: Record<string, unknown>;
  image?: string;
  summary?: string;
  thinking?: string;
  messages?: StoredMessage[];
  projects?: Project[];
  selected?: string | null;
  agent?: Agent;
  trace?: CodexTrace;
  model?: string; // turn_start: the local model that answers this turn
  model_mode?: string; // projects: "auto" or a pinned model id
  models?: ModelOption[]; // projects: selectable model catalog
};

export type Agent = "claude" | "codex";

export type CodexTrace = {
  codex_session_id?: string;
  codex_log_path?: string;
  codex_prompt?: string;
  codex_tool_call_count?: number;
  codex_shell_call_count?: number;
  codex_mcp_call_count?: number;
  codex_tool_calls?: { name?: string; namespace?: string; arguments?: string }[];
  codex_final_summary?: string;
  codex_error_summary?: string;
};

type StoredMessage = {
  role: string;
  content: string;
  cwd?: string;
  code_session_id?: string;
  codex_trace?: CodexTrace;
};

// A single activity row inside an assistant turn's details panel.
export type TurnEvent = {
  id: number;
  type: string;
  content?: string;
  tool?: string;
  args?: Record<string, unknown>;
  image?: string;
};

export type TranscriptItem =
  | { kind: "user"; id: number; turn: number; text: string }
  | {
      kind: "assistant";
      id: number;
      turn: number;
      route?: Route;
      model?: string; // local model that answered this turn (auto-picked or pinned)
      text: string; // streamed reply (chat / code)
      events: TurnEvent[]; // thinking / action / result / screenshot / error
      summary?: string; // final result (computer / code)
      cwd?: string;
      codeSessionId?: string;
      codexTrace?: CodexTrace;
      done: boolean;
    };

// Derive the WS URL from the page origin so one build works on localhost, LAN
// and over a Tailscale HTTPS hostname. The Next dev server runs the UI on :3000
// while the backend WS is on :8000 of the same host — detect that and redirect.
// (No NEXT_PUBLIC_ env: it would be inlined into the static build and break the
// single-origin/Tailscale case.)
function wsUrl(): string {
  if (typeof window === "undefined") return "ws://localhost:8000/ws";
  const { protocol, hostname, host, port } = window.location;
  if (port === "3000") return `ws://${hostname}:8000/ws`; // Next dev
  const proto = protocol === "https:" ? "wss" : "ws";
  return `${proto}://${host}/ws`;
}

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

// crypto.randomUUID requires a secure context (missing over http on the LAN,
// i.e. the mobile case). Fall back to getRandomValues, then Math.random.
function makeSessionId(): string {
  try {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
    if (typeof crypto !== "undefined" && crypto.getRandomValues) {
      const b = crypto.getRandomValues(new Uint8Array(16));
      return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
    }
  } catch {
    // fall through
  }
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
}

export default function Home() {
  const [transcript, setTranscript] = useState<TranscriptItem[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [agent, setAgent] = useState<Agent>("claude");
  const [modelMode, setModelMode] = useState<string>("auto");
  const [models, setModels] = useState<ModelOption[]>([]);
  const [running, _setRunning] = useState(false);
  const [wsStatus, setWsStatus] = useState<WsStatus>("disconnected");
  const wsRef = useRef<WebSocket | null>(null);
  const idRef = useRef(0);
  const turnRef = useRef(0); // mirrors the backend's per-message turn counter
  const runningRef = useRef(false);
  const transcriptRef = useRef<TranscriptItem[]>([]);
  const sessionIdRef = useRef<string>("");
  const tokenRef = useRef<string>("");

  // Keep a synchronous mirror of the transcript so WS callbacks can read its
  // current length without a stale closure.
  useEffect(() => {
    transcriptRef.current = transcript;
  }, [transcript]);

  // Stable per-browser session id so the backend can resume the conversation
  // across reconnects / reloads.
  useEffect(() => {
    let id = localStorage.getItem("pilot_session_id");
    if (!id) {
      id = makeSessionId();
      localStorage.setItem("pilot_session_id", id);
    }
    sessionIdRef.current = id;
  }, []);

  // Auth token: ?token=… is captured once into localStorage (then stripped from
  // the URL) and sent on every hello. Empty when the backend has no token set.
  useEffect(() => {
    const url = new URL(window.location.href);
    const fromQuery = url.searchParams.get("token");
    if (fromQuery) {
      localStorage.setItem("pilot_token", fromQuery);
      url.searchParams.delete("token");
      window.history.replaceState({}, "", url.toString());
    }
    tokenRef.current = localStorage.getItem("pilot_token") || "";
  }, []);

  const setRunning = useCallback((val: boolean) => {
    runningRef.current = val;
    _setRunning(val);
  }, []);

  // Fold an incoming server event into the assistant turn it belongs to.
  const applyEvent = useCallback((ev: ServerEvent) => {
    if (ev.type === "reset_ok") return;
    const turn = ev.turn ?? turnRef.current;

    setTranscript((prev) => {
      const next = [...prev];
      let idx = next.findIndex((i) => i.kind === "assistant" && i.turn === turn);
      if (idx === -1) {
        next.push({ kind: "assistant", id: idRef.current++, turn, text: "", events: [], done: false });
        idx = next.length - 1;
      }
      const item = next[idx];
      if (item.kind !== "assistant") return prev;
      const updated = { ...item, events: [...item.events] };

      switch (ev.type) {
        case "turn_start":
          updated.route = ev.route;
          if (ev.model) updated.model = ev.model;
          if (ev.thinking) updated.events.push({ id: idRef.current++, type: "thinking", content: ev.thinking });
          break;
        case "assistant_delta":
          updated.text += ev.content ?? "";
          break;
        case "context":
          updated.events.push({ id: idRef.current++, type: "context", content: ev.content });
          if (ev.content?.startsWith("Working directory: ")) {
            updated.cwd = ev.content.replace("Working directory: ", "");
          }
          break;
        case "codex_trace":
          updated.codexTrace = ev.trace;
          break;
        case "done":
          updated.done = true;
          if (ev.summary) updated.summary = ev.summary;
          break;
        case "screenshot":
          updated.events.push({ id: idRef.current++, type: "screenshot", image: ev.image });
          break;
        case "action":
          updated.events.push({ id: idRef.current++, type: "action", tool: ev.tool, args: ev.args });
          break;
        case "consult":
          // Header row for an expert hand-off, then a buffer the expert streams into.
          updated.events.push({ id: idRef.current++, type: "consult", tool: ev.model, content: ev.content });
          updated.events.push({ id: idRef.current++, type: "expert", tool: ev.model, content: "" });
          break;
        case "expert_delta": {
          const evs = updated.events;
          const last = evs[evs.length - 1];
          if (last && last.type === "expert" && last.tool === ev.model) {
            evs[evs.length - 1] = { ...last, content: (last.content ?? "") + (ev.content ?? "") };
          } else {
            evs.push({ id: idRef.current++, type: "expert", tool: ev.model, content: ev.content });
          }
          break;
        }
        default: // thinking | result | error
          updated.events.push({ id: idRef.current++, type: ev.type, content: ev.content });
      }

      next[idx] = updated;
      return next;
    });
  }, []);

  useEffect(() => {
    let dead = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    function openWs() {
      if (dead) return;
      setWsStatus("connecting");
      const ws = new WebSocket(wsUrl());
      wsRef.current = ws;

      ws.onopen = () => {
        setWsStatus("connected");
        // Resume (or register) this browser's session; token is "" unless set.
        ws.send(JSON.stringify({ type: "hello", session_id: sessionIdRef.current, token: tokenRef.current }));
      };

      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data) as ServerEvent;
        if (msg.type === "history") {
          turnRef.current = msg.turn ?? 0;
          // Only rebuild on a fresh page (empty transcript); a mid-session
          // reconnect keeps the richer local transcript intact.
          if (transcriptRef.current.length === 0 && msg.messages?.length) {
            setTranscript(
              msg.messages.map((m) =>
                m.role === "user"
                  ? { kind: "user", id: idRef.current++, turn: 0, text: m.content }
                  : {
                      kind: "assistant",
                      id: idRef.current++,
                      turn: 0,
                      text: m.content,
                      events: [],
                      done: true,
                      cwd: m.cwd,
                      codeSessionId: m.code_session_id,
                      codexTrace: m.codex_trace,
                    }
              )
            );
          }
          return;
        }
        if (msg.type === "projects") {
          setProjects(msg.projects ?? []);
          setSelectedProject(msg.selected ?? null);
          if (msg.agent) setAgent(msg.agent);
          if (msg.model_mode) setModelMode(msg.model_mode);
          if (msg.models) setModels(msg.models);
          return;
        }
        if (msg.type === "done" || msg.type === "error") setRunning(false);
        applyEvent(msg);
      };

      ws.onerror = () => {
        setWsStatus("error");
        if (runningRef.current) setRunning(false);
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
  }, [applyEvent, setRunning]);

  const handleSend = useCallback(
    (text: string) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      turnRef.current += 1; // matches backend turn_counter increment order
      setTranscript((prev) => [...prev, { kind: "user", id: idRef.current++, turn: turnRef.current, text }]);
      setRunning(true);
      ws.send(JSON.stringify({ type: "message", text }));
    },
    [setRunning]
  );

  const handleAbort = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: "abort" }));
    setRunning(false);
  }, [setRunning]);

  const handleReset = useCallback(() => {
    const ws = wsRef.current;
    const wasRunning = runningRef.current;
    const nextSessionId = makeSessionId();
    localStorage.setItem("pilot_session_id", nextSessionId);
    sessionIdRef.current = nextSessionId;
    turnRef.current = 0;
    idRef.current = 0;
    setTranscript([]);
    setRunning(false);
    if (ws?.readyState === WebSocket.OPEN) {
      if (wasRunning) ws.send(JSON.stringify({ type: "abort" }));
      ws.send(JSON.stringify({ type: "hello", session_id: nextSessionId, token: tokenRef.current }));
      const selected = projects.find((p) => p.path === selectedProject);
      if (selected) ws.send(JSON.stringify({ type: "select_project", id: selected.id }));
      ws.send(JSON.stringify({ type: "select_agent", agent }));
      ws.send(JSON.stringify({ type: "select_model", model_mode: modelMode }));
    }
  }, [agent, modelMode, projects, selectedProject, setRunning]);

  const selectProject = useCallback((id: string) => {
    wsRef.current?.send(JSON.stringify({ type: "select_project", id }));
  }, []);
  const addProject = useCallback((path: string) => {
    wsRef.current?.send(JSON.stringify({ type: "add_project", path }));
  }, []);
  const removeProject = useCallback((id: string) => {
    wsRef.current?.send(JSON.stringify({ type: "remove_project", id }));
  }, []);
  const selectAgent = useCallback((a: Agent) => {
    wsRef.current?.send(JSON.stringify({ type: "select_agent", agent: a }));
  }, []);
  const selectModel = useCallback((mode: string) => {
    wsRef.current?.send(JSON.stringify({ type: "select_model", model_mode: mode }));
  }, []);

  return (
    <main style={{ display: "flex", flexDirection: "column", height: "100dvh", padding: "1rem", gap: "0.75rem", maxWidth: 800, margin: "0 auto" }}>
      <header style={{ paddingBottom: "0.5rem", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h1 style={{ fontSize: "1.25rem", fontWeight: 700, color: "var(--accent)" }}>Pilot</h1>
          <p style={{ fontSize: "0.8rem", color: "var(--muted)" }}>Local AI chat &amp; computer agent</p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <button
            onClick={handleReset}
            style={{ fontSize: "0.75rem", color: "var(--muted)", background: "none", border: "1px solid var(--border)", borderRadius: 6, padding: "0.25rem 0.6rem", cursor: "pointer" }}
          >
            Ny konversation
          </button>
          <span style={{ fontSize: "0.75rem", fontWeight: 600, color: STATUS_COLOR[wsStatus] }}>
            ● {STATUS_LABEL[wsStatus]}
          </span>
        </div>
      </header>

      <ProjectBar
        projects={projects}
        selected={selectedProject}
        agent={agent}
        modelMode={modelMode}
        models={models}
        onSelect={selectProject}
        onAdd={addProject}
        onRemove={removeProject}
        onSelectAgent={selectAgent}
        onSelectModel={selectModel}
      />

      <Transcript items={transcript} />

      {running && <AbortButton onAbort={handleAbort} />}

      <ChatInput onSend={handleSend} disabled={wsStatus !== "connected"} />
    </main>
  );
}
