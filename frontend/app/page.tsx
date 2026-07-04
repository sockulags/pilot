"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ChatInput from "@/components/TaskInput";
import Transcript from "@/components/ActionLog";
import ProjectBar from "@/components/ProjectBar";
import JobsPanel from "@/components/JobsPanel";
import SettingsPanel from "@/components/SettingsPanel";
import { ToastProvider, useToast } from "@/components/Toast";
import Dialog, { useDialogA11y } from "@/components/Dialog";
import { t } from "@/app/strings";

export type Route = "chat" | "computer" | "code";
export type Project = { id: string; name: string; path: string };
export type ModelOption = { id: string; label: string; hint: string };
export type AgentRoleOption = {
  role: string;
  label: string;
  model: string;
  model_label: string;
  available: boolean;
};

export type JobSchedule = {
  type: "interval" | "daily" | "weekly" | "once";
  interval_seconds?: number;
  time?: string;
  weekdays?: number[];
  date?: string;
};

export type Job = {
  id: string;
  session_id: string | null;
  title: string;
  kind: string;
  payload: string;
  schedule: JobSchedule;
  enabled: boolean;
  next_run: number | null;
  created_ts: number;
  last_run: number | null;
  last_result: string | null;
  summary: string;
  next_run_label: string;
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
    | "memory"
    | "codex_trace"
    | "result"
    | "screenshot"
    | "jobs"
    | "routing_decision"
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
  model?: string;
  model_mode?: string;
  models?: ModelOption[];
  agent_roles?: AgentRoleOption[];
  route_mode?: string;
  jobs?: Job[];
  // Explainability: turn_start carries the picked agent role / model, and
  // routing_decision explains which engine will act and what it may touch.
  agent_role?: string;
  agent_role_model?: string;
  agent_role_fallback?: string | null;
  execution_engine?: string;
  reason?: string;
  required_permissions?: string[];
};

// Why a turn took the route/model it did — populated from turn_start and
// routing_decision, surfaced as a compact expandable line on the assistant turn.
export type RouteInsight = {
  agentRole?: string;
  agentRoleModel?: string;
  agentRoleFallback?: string;
  executionEngine?: string;
  reason?: string;
  requiredPermissions?: string[];
};

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
      model?: string;
      text: string;
      events: TurnEvent[];
      summary?: string;
      cwd?: string;
      codeSessionId?: string;
      codexTrace?: CodexTrace;
      insight?: RouteInsight;
      done: boolean;
    };

type WsStatus = "disconnected" | "connecting" | "connected" | "error";

const STATUS_LABEL: Record<WsStatus, string> = t.status;

const HERO_SUGGESTIONS = t.hero.suggestions;

const RECONNECT_DELAY = 3000;

function wsUrl(): string {
  if (typeof window === "undefined") return "ws://localhost:8000/ws";
  const { protocol, hostname, host, port } = window.location;
  if (port === "3000") return `ws://${hostname}:8000/ws`;
  const proto = protocol === "https:" ? "wss" : "ws";
  return `${proto}://${host}/ws`;
}

function makeSessionId(): string {
  try {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
    if (typeof crypto !== "undefined" && crypto.getRandomValues) {
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
    }
  } catch {}
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
}

function preview(text: string, max = 72) {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length <= max ? flat : `${flat.slice(0, max - 1)}…`;
}

function modelLabel(mode: string, models: ModelOption[]) {
  if (mode === "auto") return "Auto";
  return models.find((model) => model.id === mode)?.label ?? mode;
}

function agentLabel(agent: Agent) {
  return t.agents.find((option) => option.id === agent)?.label ?? agent;
}

function approximateTokens(transcript: TranscriptItem[]) {
  return transcript.reduce((sum, item) => {
    const text = item.kind === "user" ? item.text : `${item.text} ${item.summary ?? ""}`;
    return sum + Math.ceil(text.length / 4);
  }, 1600);
}

function ContextModal({
  transcript,
  compacted,
  hiddenCount,
  onCompactView,
  onClearContext,
  onClose,
}: {
  transcript: TranscriptItem[];
  compacted: boolean;
  hiddenCount: number;
  onCompactView: () => void;
  onClearContext: () => void;
  onClose: () => void;
}) {
  const total = approximateTokens(transcript);
  const system = 900;
  const skills = 420;
  const conversation = Math.max(0, total - system - skills);
  const percent = Math.min(100, Math.round((total / 8192) * 100));

  return (
    <Dialog icon="◔" title={t.dialogs.context} className="narrow" onClose={onClose}>
        <div className="mb">
          <p style={{ color: "var(--dim)", marginBottom: 12 }}>
            Uppskattad fördelning – inte exakta siffror. Värdena beräknas lokalt
            i webbläsaren och är till för att ge en känsla för storleken.
          </p>
          <div className="ctxbar">
            <span style={{ width: `${(system / 8192) * 100}%`, background: "var(--accent)" }} />
            <span style={{ width: `${(skills / 8192) * 100}%`, background: "var(--violet)" }} />
            <span style={{ width: `${(conversation / 8192) * 100}%`, background: "var(--green)" }} />
          </div>
          <div className="ctxrow"><span className="sw" style={{ background: "var(--accent)" }} /><span className="nm2">System</span><span className="tk">~{system} tok</span></div>
          <div className="ctxrow"><span className="sw" style={{ background: "var(--violet)" }} /><span className="nm2">Skills</span><span className="tk">~{skills} tok</span></div>
          <div className="ctxrow"><span className="sw" style={{ background: "var(--green)" }} /><span className="nm2">Samtal</span><span className="tk">~{conversation} tok</span></div>
          <div className="ctxrow" style={{ borderBottom: "none" }}><span className="sw" style={{ background: "var(--panel-2)" }} /><span className="nm2">Totalt</span><span className="tk">~{total} tok · ~{percent}% av kontexten</span></div>
          <p className="ctxhint">
            {compacted ? `Fokusvy aktiv. ${hiddenCount} äldre inlägg är dolda i UI:t, men finns kvar i den underliggande sessionen.` : "Fokusvy visar bara de senaste turerna för att minska visuellt brus utan att kasta bort sessionen."}
          </p>
          <div className="ctxacts">
            <button onClick={onCompactView}>{compacted ? "Uppdatera fokusvy" : "Kompaktera vy"}</button>
            <button className="danger" onClick={onClearContext}>Ny konversation</button>
          </div>
        </div>
    </Dialog>
  );
}

function Drawer({
  transcript,
  selectedProject,
  agent,
  modelMode,
  models,
  routeMode,
  wsStatus,
  onClose,
  onOpenControls,
  onReset,
  onJump,
}: {
  transcript: TranscriptItem[];
  selectedProject: Project | null;
  agent: Agent;
  modelMode: string;
  models: ModelOption[];
  routeMode: string;
  wsStatus: WsStatus;
  onClose: () => void;
  onOpenControls: () => void;
  onReset: () => void;
  onJump: (id: number) => void;
}) {
  const [query, setQuery] = useState("");
  const drawerRef = useDialogA11y(onClose);
  const items = transcript.filter((item): item is Extract<TranscriptItem, { kind: "user" }> => item.kind === "user");
  const filtered = items.filter((item) => item.text.toLowerCase().includes(query.toLowerCase()));
  const lastPrompt = items.at(-1);

  return (
    <>
      <div className="drawer-scrim on" onClick={onClose} />
      <aside className="drawer open" ref={drawerRef} role="dialog" aria-modal="true" aria-label="Session" tabIndex={-1}>
        <div className="dh">
          <div className="t">{t.drawer.session}</div>
          <button className="x" onClick={onClose} aria-label={t.common.close}>✕</button>
        </div>
        <button className="newbtn" onClick={onReset}>＋ {t.header.newConversation}</button>
        <div className="dsect">
          <div className="seclabel">{t.drawer.activeNow}</div>
          <button className="ses-item on dsession" onClick={onOpenControls}>
            <div className="st">{selectedProject?.name ?? t.drawer.noProject}</div>
            <div className="sm">{selectedProject?.path ?? t.drawer.openControls}</div>
            <div className="pillrow">
              <span className="microtag">{agentLabel(agent)}</span>
              <span className="microtag">{modelLabel(modelMode, models)}</span>
              <span className="microtag">{routeMode === "auto" ? "Auto route" : routeMode}</span>
            </div>
          </button>
          <div className="dmeta">
            <div className="sessionstat"><span>{t.drawer.statusLabel}</span><b>{STATUS_LABEL[wsStatus]}</b></div>
            <div className="sessionstat"><span>{t.drawer.turns}</span><b>{items.length}</b></div>
            <div className="sessionstat"><span>{t.drawer.last}</span><b>{lastPrompt ? `${t.drawer.turn} ${lastPrompt.turn}` : t.drawer.empty}</b></div>
          </div>
        </div>
        <div className="dsect">
          <div className="seclabel">{t.drawer.searchHistory}</div>
          <input className="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t.drawer.searchPlaceholder} />
        </div>
        <div className="list">
          <div className="seclabel">{t.drawer.recentPrompts}</div>
          {filtered.length === 0 ? (
            <div className="ses-empty">{query ? t.drawer.noMatches : t.drawer.noPrompts}</div>
          ) : (
            filtered.map((item) => (
              <button
                key={item.id}
                className="ses-item"
                onClick={() => {
                  onJump(item.id);
                  onClose();
                }}
              >
                <div className="st">{preview(item.text)}</div>
                <div className="sm">{t.drawer.turn} {item.turn}</div>
              </button>
            ))
          )}
        </div>
      </aside>
    </>
  );
}

export default function Home() {
  return (
    <ToastProvider>
      <Workspace />
    </ToastProvider>
  );
}

function Workspace() {
  const toast = useToast();
  const [transcript, setTranscript] = useState<TranscriptItem[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [agent, setAgent] = useState<Agent>("claude");
  const [modelMode, setModelMode] = useState("auto");
  const [models, setModels] = useState<ModelOption[]>([]);
  const [agentRoles, setAgentRoles] = useState<AgentRoleOption[]>([]);
  const [routeMode, setRouteMode] = useState("auto");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobsOpen, setJobsOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [controlsOpen, setControlsOpen] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [agentMenuOpen, setAgentMenuOpen] = useState(false);
  const [focusView, setFocusView] = useState<number | null>(null);
  const [running, _setRunning] = useState(false);
  const [wsStatus, setWsStatus] = useState<WsStatus>("disconnected");
  const [showJump, setShowJump] = useState(false);
  const [reconnectNonce, setReconnectNonce] = useState(0);
  const [authFailed, setAuthFailed] = useState(false);
  const [composerSeed, setComposerSeed] = useState("");
  const [composerKey, setComposerKey] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const atBottomRef = useRef(true);
  const wsRef = useRef<WebSocket | null>(null);
  const idRef = useRef(0);
  const turnRef = useRef(0);
  const runningRef = useRef(false);
  const transcriptRef = useRef<TranscriptItem[]>([]);
  const sessionIdRef = useRef("");
  const tokenRef = useRef("");

  useEffect(() => {
    transcriptRef.current = transcript;
  }, [transcript]);

  useEffect(() => {
    document.body.classList.toggle("busy", running);
  }, [running]);

  // Global shortcut: Cmd/Ctrl+K opens the project/model/agent controls.
  // (Escape-to-close for overlays is handled by the Dialog/drawer a11y hook.)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setControlsOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    let id = localStorage.getItem("pilot_session_id");
    if (!id) {
      id = makeSessionId();
      localStorage.setItem("pilot_session_id", id);
    }
    sessionIdRef.current = id;
  }, []);

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

  const setRunning = useCallback((value: boolean) => {
    runningRef.current = value;
    _setRunning(value);
  }, []);

  // Finalize any still-open assistant turn so its spinner can't hang forever
  // after a mid-stream disconnect (review 2026-07-04).
  const finalizeOpenTurns = useCallback((note: string) => {
    setTranscript((prev) => {
      if (!prev.some((it) => it.kind === "assistant" && !it.done)) return prev;
      return prev.map((it) =>
        it.kind === "assistant" && !it.done
          ? { ...it, done: true, summary: it.summary ?? note }
          : it
      );
    });
  }, []);

  // Track whether the user is parked at the bottom of the feed. We only
  // auto-follow new content when they are, so scrolling up to read history
  // isn't yanked back down on every streamed token.
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    // Only touch state when the threshold is actually crossed, not on every
    // scroll event, to avoid re-rendering while the user is scrolling.
    if (atBottom !== atBottomRef.current) {
      atBottomRef.current = atBottom;
      setShowJump(!atBottom);
    }
  }, []);

  const jumpToLatest = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    atBottomRef.current = true;
    setShowJump(false);
  }, []);

  const jumpToMessage = useCallback((id: number) => {
    // Focus view hides older turns, so reveal the full transcript first, then
    // scroll once the target anchor has rendered.
    setFocusView(null);
    requestAnimationFrame(() => {
      document.getElementById(`msg-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, []);

  // Load a previous prompt into the composer for editing (re-key to reseed).
  const editPrompt = useCallback((text: string) => {
    setComposerSeed(text);
    setComposerKey((k) => k + 1);
  }, []);

  useEffect(() => {
    if (!atBottomRef.current) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [transcript]);

  const applyEvent = useCallback((ev: ServerEvent) => {
    if (ev.type === "reset_ok") return;
    const turn = ev.turn ?? turnRef.current;

    setTranscript((prev) => {
      const next = [...prev];
      let idx = next.findIndex((item) => item.kind === "assistant" && item.turn === turn);
      if (idx === -1) {
        // A turn-less error/done event (e.g. an auth or transport error before
        // any turn_start) must not fabricate a phantom forever-'working'
        // assistant bubble. Only create the assistant row for events that
        // actually begin/continue a turn (review 2026-07-04).
        if (ev.type === "error" || ev.type === "done") return prev;
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
          // Fold the routing/model rationale onto the turn so the UI can offer a
          // "why this route/model" affordance instead of discarding it.
          updated.insight = {
            ...updated.insight,
            agentRole: ev.agent_role,
            agentRoleModel: ev.agent_role_model,
            agentRoleFallback: ev.agent_role_fallback ?? undefined,
          };
          if (ev.thinking) updated.events.push({ id: idRef.current++, type: "thinking", content: ev.thinking });
          break;
        case "routing_decision":
          updated.insight = {
            ...updated.insight,
            executionEngine: ev.execution_engine,
            reason: ev.reason,
            requiredPermissions: ev.required_permissions,
          };
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
          updated.events.push({ id: idRef.current++, type: "consult", tool: ev.model, content: ev.content });
          break;
        case "expert_delta": {
          // Coalesce token-by-token expert stream into a single event (like
          // assistant_delta), instead of one event per chunk — which flooded
          // the timeline with fragments and made rendering O(n²) (review
          // 2026-07-04).
          const last = updated.events[updated.events.length - 1];
          if (last && last.type === "expert" && last.tool === ev.model) {
            updated.events = [
              ...updated.events.slice(0, -1),
              { ...last, content: (last.content ?? "") + (ev.content ?? "") },
            ];
          } else {
            updated.events.push({ id: idRef.current++, type: "expert", tool: ev.model, content: ev.content });
          }
          break;
        }
        default:
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
        ws.send(JSON.stringify({ type: "hello", session_id: sessionIdRef.current, token: tokenRef.current }));
      };

      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data) as ServerEvent;
        // A rejected hello closes the socket after this error; stop the retry
        // loop and surface a terminal state instead of reconnecting every 3s
        // forever (review 2026-07-04).
        if (msg.type === "error" && msg.content === "unauthorized") {
          dead = true;
          setAuthFailed(true);
          setWsStatus("error");
          setRunning(false);
          if (retryTimer) clearTimeout(retryTimer);
          try { ws.close(); } catch {}
          return;
        }
        if (msg.type === "history") {
          turnRef.current = msg.turn ?? 0;
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
          if (msg.agent_roles) setAgentRoles(msg.agent_roles);
          if (msg.route_mode) setRouteMode(msg.route_mode);
          return;
        }
        if (msg.type === "jobs") {
          setJobs(msg.jobs ?? []);
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
        if (runningRef.current) {
          setRunning(false);
          finalizeOpenTurns(t.status.disconnected);
        }
        retryTimer = setTimeout(openWs, RECONNECT_DELAY);
      };
    }

    openWs();
    return () => {
      dead = true;
      if (retryTimer) clearTimeout(retryTimer);
      wsRef.current?.close();
    };
  }, [applyEvent, setRunning, reconnectNonce, finalizeOpenTurns]);

  // Force an immediate reconnect (tears down and re-runs the socket effect).
  const reconnect = useCallback(() => {
    setAuthFailed(false);
    tokenRef.current = localStorage.getItem("pilot_token") || "";
    setWsStatus("connecting");
    setReconnectNonce((n) => n + 1);
  }, []);

  const handleSend = useCallback((text: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    turnRef.current += 1;
    setFocusView(null);
    setTranscript((prev) => [...prev, { kind: "user", id: idRef.current++, turn: turnRef.current, text }]);
    setRunning(true);
    ws.send(JSON.stringify({ type: "message", text }));
  }, [setRunning]);

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
    setFocusView(null);
    setTranscript([]);
    setRunning(false);
    if (ws?.readyState === WebSocket.OPEN) {
      if (wasRunning) ws.send(JSON.stringify({ type: "abort" }));
      ws.send(JSON.stringify({ type: "hello", session_id: nextSessionId, token: tokenRef.current }));
      const selected = projects.find((project) => project.path === selectedProject);
      if (selected) ws.send(JSON.stringify({ type: "select_project", id: selected.id }));
      ws.send(JSON.stringify({ type: "select_agent", agent }));
      ws.send(JSON.stringify({ type: "select_model", model_mode: modelMode }));
      ws.send(JSON.stringify({ type: "select_route", route_mode: routeMode }));
    }
    setDrawerOpen(false);
  }, [agent, modelMode, projects, routeMode, selectedProject, setRunning]);

  // Guard the conversation wipe behind a confirm toast so a stray click can't
  // discard an active conversation. Empty conversations reset immediately.
  const requestReset = useCallback(() => {
    if (transcriptRef.current.length === 0) {
      handleReset();
      return;
    }
    toast.show(t.confirm.reset, {
      action: { label: t.confirm.resetAction, onClick: handleReset },
    });
  }, [handleReset, toast]);

  const selectProject = useCallback((id: string) => {
    wsRef.current?.send(JSON.stringify({ type: "select_project", id }));
  }, []);
  const addProject = useCallback((path: string) => {
    wsRef.current?.send(JSON.stringify({ type: "add_project", path }));
  }, []);
  const removeProject = useCallback((id: string) => {
    wsRef.current?.send(JSON.stringify({ type: "remove_project", id }));
  }, []);
  const selectAgent = useCallback((value: Agent) => {
    wsRef.current?.send(JSON.stringify({ type: "select_agent", agent: value }));
    setAgentMenuOpen(false);
  }, []);
  const selectModel = useCallback((mode: string) => {
    wsRef.current?.send(JSON.stringify({ type: "select_model", model_mode: mode }));
  }, []);
  const selectRoute = useCallback((mode: string) => {
    wsRef.current?.send(JSON.stringify({ type: "select_route", route_mode: mode }));
  }, []);
  const addJob = useCallback((payload: string, schedule: JobSchedule, title: string, kind: string) => {
    wsRef.current?.send(JSON.stringify({ type: "add_job", payload, schedule, title, kind }));
  }, []);
  const pauseJob = useCallback((id: string) => {
    wsRef.current?.send(JSON.stringify({ type: "pause_job", id }));
  }, []);
  const resumeJob = useCallback((id: string) => {
    wsRef.current?.send(JSON.stringify({ type: "resume_job", id }));
  }, []);
  const deleteJob = useCallback((id: string) => {
    toast.show(t.confirm.deleteJob, {
      action: {
        label: t.confirm.deleteJobAction,
        onClick: () => wsRef.current?.send(JSON.stringify({ type: "delete_job", id })),
      },
    });
  }, [toast]);

  const selectedProjectObject = projects.find((project) => project.path === selectedProject) ?? null;
  const hasConversation = transcript.length > 0;
  const liveAnnouncement = transcript.findLast((item) => item.kind === "assistant" && item.done)?.text ?? "";
  const visibleTranscript = focusView ? transcript.slice(-focusView) : transcript;
  const hiddenCount = transcript.length - visibleTranscript.length;

  return (
    <>
      <a className="skip-link" href="#main">{t.a11y.skipToContent}</a>
      <div className="hairline" />
      <div className="shell">
        <header className="top">
          <button className="ic" onClick={() => setDrawerOpen(true)} title={t.header.openSession} aria-label={t.header.openSession}>
            ☰
          </button>
          <div className="mk">✦</div>
          <div className="nm">{t.appName}</div>
          <div className="headpills">
          <button className="crumb" onClick={() => setControlsOpen(true)} title={t.header.controlsHint}>
            {selectedProjectObject?.name ?? t.header.chooseProject}
          </button>
            <button className="crumb soft" onClick={() => setControlsOpen(true)}>
              {routeMode === "auto" ? t.header.autoRoute : routeMode}
            </button>
          </div>
          <div className="sp" />
          <button className="brain model" onClick={() => setContextOpen(true)}>
            <span className="orb" />
            <span id="brainTxt">{modelMode === "auto" ? t.header.autoOrchestration : modelLabel(modelMode, models)}</span>
          </button>
          <div className={`agent${agentMenuOpen ? " open" : ""}`}>
            <button className="agent-trigger" onClick={() => setAgentMenuOpen((value) => !value)}>
              {agentLabel(agent)}
            </button>
            <div className="menu">
              {t.agents.map((option) => (
                <button
                  key={option.id}
                  className={agent === option.id ? "on" : ""}
                  onClick={() => selectAgent(option.id as Agent)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
          <button className="ic" onClick={() => setJobsOpen(true)} title={t.header.scheduledJobs} aria-label={t.header.scheduledJobs}>
            ⏰
            {jobs.length > 0 && <span className="badge">{jobs.length}</span>}
          </button>
          <button className="ic" onClick={() => setSettingsOpen(true)} title={t.settings.open} aria-label={t.settings.open}>
            ⚙
          </button>
          <button className="ic reset" onClick={requestReset} title={t.header.newConversation} aria-label={t.header.newConversation}>⟲</button>
          <div className="brain status" title={STATUS_LABEL[wsStatus]}>
            <span className="conn" style={{ background: wsStatus === "error" ? "var(--del)" : wsStatus === "connecting" ? "var(--amber)" : "var(--green)" }} />
            <span>{STATUS_LABEL[wsStatus]}</span>
          </div>
        </header>

        {wsStatus !== "connected" && (
          <div className={`connbanner ${wsStatus}`} role="status">
            <span className="cb-dot" />
            <span className="cb-msg">
              {authFailed
                ? t.connection.unauthorized
                : wsStatus === "connecting"
                ? t.connection.connecting
                : t.connection.dropped}
            </span>
            {wsStatus !== "connecting" && (
              <button className="cb-retry" onClick={reconnect}>{t.connection.retry}</button>
            )}
          </div>
        )}

        <div className="sr-only" aria-live="polite" aria-atomic="true">{liveAnnouncement}</div>

        <main id="main" className="scroll" ref={scrollRef} onScroll={handleScroll} tabIndex={-1}>
          {!hasConversation ? (
            <section className="hero">
              <h1 className="greet">
                {t.hero.titleLead}<span className="g">{t.hero.titleAccent}</span>.
              </h1>
              <p className="tag">{t.hero.tagline}</p>
              <div className="ghosts">
                {HERO_SUGGESTIONS.map((suggestion) => (
                  <button key={suggestion} className="ghost" onClick={() => handleSend(suggestion)}>
                    {suggestion}
                  </button>
                ))}
              </div>
              <ChatInput onSend={handleSend} onAbort={handleAbort} onOpenContext={() => setContextOpen(true)} disabled={wsStatus !== "connected"} running={running} />
            </section>
          ) : (
            <section className="conv on">
              <Transcript items={visibleTranscript} onEdit={editPrompt} onResend={handleSend} />
            </section>
          )}
        </main>

        {hasConversation && showJump && (
          <button className="jump-latest" onClick={jumpToLatest} aria-label={t.a11y.jumpToLatest}>
            {t.jumpLatest}
          </button>
        )}

        {hasConversation && (
          <div className="dock">
            <ChatInput key={composerKey} initialValue={composerSeed} onSend={handleSend} onAbort={handleAbort} onOpenContext={() => setContextOpen(true)} disabled={wsStatus !== "connected"} running={running} />
          </div>
        )}
      </div>

      {drawerOpen && (
        <Drawer
          transcript={transcript}
          selectedProject={selectedProjectObject}
          agent={agent}
          modelMode={modelMode}
          models={models}
          routeMode={routeMode}
          wsStatus={wsStatus}
          onClose={() => setDrawerOpen(false)}
          onOpenControls={() => {
            setControlsOpen(true);
            setDrawerOpen(false);
          }}
          onReset={requestReset}
          onJump={jumpToMessage}
        />
      )}

      {controlsOpen && (
        <Dialog icon="⌘" title={t.dialogs.controls} onClose={() => setControlsOpen(false)}>
          <div className="mb">
            <ProjectBar
              projects={projects}
              selected={selectedProject}
              agent={agent}
              modelMode={modelMode}
              models={models}
              agentRoles={agentRoles}
              routeMode={routeMode}
              onSelect={selectProject}
              onAdd={addProject}
              onRemove={removeProject}
              onSelectAgent={selectAgent}
              onSelectModel={selectModel}
              onSelectRoute={selectRoute}
            />
          </div>
        </Dialog>
      )}

      {contextOpen && (
        <ContextModal
          transcript={transcript}
          compacted={focusView !== null}
          hiddenCount={Math.max(0, hiddenCount)}
          onCompactView={() => setFocusView(Math.min(8, transcript.length))}
          onClearContext={() => {
            setContextOpen(false);
            requestReset();
          }}
          onClose={() => setContextOpen(false)}
        />
      )}
      {jobsOpen && <JobsPanel jobs={jobs} onClose={() => setJobsOpen(false)} onAdd={addJob} onPause={pauseJob} onResume={resumeJob} onDelete={deleteJob} />}
      {settingsOpen && <SettingsPanel onClose={() => setSettingsOpen(false)} />}
    </>
  );
}
