"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ChatInput from "@/components/TaskInput";
import Transcript from "@/components/ActionLog";
import ProjectBar from "@/components/ProjectBar";
import JobsPanel from "@/components/JobsPanel";

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
      done: boolean;
    };

type WsStatus = "disconnected" | "connecting" | "connected" | "error";

const STATUS_LABEL: Record<WsStatus, string> = {
  disconnected: "Frånkopplad",
  connecting: "Ansluter",
  connected: "Ansluten",
  error: "Fel",
};

const HERO_SUGGESTIONS = [
  "Granska den här diffen och säg vad som är riskabelt",
  "Kör igenom repo:t och föreslå nästa tekniska steg",
  "Jämför lokala modeller och föreslå rätt standardstack",
  "Öppna projektet, kör testerna och förklara vad som faller",
];

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
    <div className="scrim on" onClick={onClose}>
      <div className="modal narrow" onClick={(e) => e.stopPropagation()}>
        <div className="mh">
          <span>◔</span>
          <span className="nm">Huvudagentens kontext</span>
          <button className="x" onClick={onClose}>✕</button>
        </div>
        <div className="mb">
          <p style={{ color: "var(--dim)", marginBottom: 12 }}>
            Ungefärlig fördelning för den här konversationen just nu.
          </p>
          <div className="ctxbar">
            <span style={{ width: `${(system / 8192) * 100}%`, background: "var(--accent)" }} />
            <span style={{ width: `${(skills / 8192) * 100}%`, background: "var(--violet)" }} />
            <span style={{ width: `${(conversation / 8192) * 100}%`, background: "var(--green)" }} />
          </div>
          <div className="ctxrow"><span className="sw" style={{ background: "var(--accent)" }} /><span className="nm2">System</span><span className="tk">{system} tok</span></div>
          <div className="ctxrow"><span className="sw" style={{ background: "var(--violet)" }} /><span className="nm2">Skills</span><span className="tk">{skills} tok</span></div>
          <div className="ctxrow"><span className="sw" style={{ background: "var(--green)" }} /><span className="nm2">Samtal</span><span className="tk">{conversation} tok</span></div>
          <div className="ctxrow" style={{ borderBottom: "none" }}><span className="sw" style={{ background: "var(--panel-2)" }} /><span className="nm2">Totalt</span><span className="tk">{total} tok · {percent}% av 8k</span></div>
          <p className="ctxhint">
            {compacted ? `Fokusvy aktiv. ${hiddenCount} äldre inlägg är dolda i UI:t, men finns kvar i den underliggande sessionen.` : "Fokusvy visar bara de senaste turerna för att minska visuellt brus utan att kasta bort sessionen."}
          </p>
          <div className="ctxacts">
            <button onClick={onCompactView}>{compacted ? "Uppdatera fokusvy" : "Kompaktera vy"}</button>
            <button className="danger" onClick={onClearContext}>Ny konversation</button>
          </div>
        </div>
      </div>
    </div>
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
}) {
  const [query, setQuery] = useState("");
  const items = transcript.filter((item): item is Extract<TranscriptItem, { kind: "user" }> => item.kind === "user");
  const filtered = items.filter((item) => item.text.toLowerCase().includes(query.toLowerCase()));
  const lastPrompt = items.at(-1);

  return (
    <>
      <div className="drawer-scrim on" onClick={onClose} />
      <aside className="drawer open">
        <div className="dh">
          <div className="t">Session</div>
          <button className="x" onClick={onClose}>✕</button>
        </div>
        <button className="newbtn" onClick={onReset}>＋ Ny konversation</button>
        <div className="dsect">
          <div className="seclabel">Aktiv nu</div>
          <button className="ses-item on dsession" onClick={onOpenControls}>
            <div className="st">{selectedProject?.name ?? "Inget projekt valt"}</div>
            <div className="sm">{selectedProject?.path ?? "Öppna kontrollpanelen för projekt, modell och agent."}</div>
            <div className="pillrow">
              <span className="microtag">{agent === "claude" ? "Claude Code" : "Codex"}</span>
              <span className="microtag">{modelLabel(modelMode, models)}</span>
              <span className="microtag">{routeMode === "auto" ? "Auto route" : routeMode}</span>
            </div>
          </button>
          <div className="dmeta">
            <div className="sessionstat"><span>Status</span><b>{STATUS_LABEL[wsStatus]}</b></div>
            <div className="sessionstat"><span>Turer</span><b>{items.length}</b></div>
            <div className="sessionstat"><span>Senast</span><b>{lastPrompt ? `Tur ${lastPrompt.turn}` : "Tom"}</b></div>
          </div>
        </div>
        <div className="dsect">
          <div className="seclabel">Sök i historik</div>
          <input className="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filtrera tidigare prompts…" />
        </div>
        <div className="list">
          <div className="seclabel">Senaste prompts</div>
          {filtered.map((item) => (
            <button key={item.id} className="ses-item">
              <div className="st">{preview(item.text)}</div>
              <div className="sm">Tur {item.turn}</div>
            </button>
          ))}
        </div>
      </aside>
    </>
  );
}

export default function Home() {
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
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [controlsOpen, setControlsOpen] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [agentMenuOpen, setAgentMenuOpen] = useState(false);
  const [focusView, setFocusView] = useState<number | null>(null);
  const [running, _setRunning] = useState(false);
  const [wsStatus, setWsStatus] = useState<WsStatus>("disconnected");
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

  useEffect(() => {
    if (typeof window === "undefined") return;
    const apply = () => document.body.classList.toggle("m", window.innerWidth < 820);
    apply();
    window.addEventListener("resize", apply);
    return () => window.removeEventListener("resize", apply);
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

  const applyEvent = useCallback((ev: ServerEvent) => {
    if (ev.type === "reset_ok") return;
    const turn = ev.turn ?? turnRef.current;

    setTranscript((prev) => {
      const next = [...prev];
      let idx = next.findIndex((item) => item.kind === "assistant" && item.turn === turn);
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
          updated.events.push({ id: idRef.current++, type: "consult", tool: ev.model, content: ev.content });
          break;
        case "expert_delta":
          updated.events.push({ id: idRef.current++, type: "expert", tool: ev.model, content: ev.content });
          break;
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
    wsRef.current?.send(JSON.stringify({ type: "delete_job", id }));
  }, []);

  const selectedProjectObject = projects.find((project) => project.path === selectedProject) ?? null;
  const hasConversation = transcript.length > 0;
  const visibleTranscript = focusView ? transcript.slice(-focusView) : transcript;
  const hiddenCount = transcript.length - visibleTranscript.length;

  return (
    <>
      <div className="hairline" />
      <div className="shell">
        <header className="top">
          <button className="ic" onClick={() => setDrawerOpen(true)} title="Öppna session">
            ☰
          </button>
          <div className="mk">✦</div>
          <div className="nm">Pilot</div>
          <div className="headpills">
          <button className="crumb" onClick={() => setControlsOpen(true)}>
            {selectedProjectObject?.name ?? "Välj projekt"}
          </button>
            <button className="crumb soft" onClick={() => setControlsOpen(true)}>
              {routeMode === "auto" ? "Auto route" : routeMode}
            </button>
          </div>
          <div className="sp" />
          <button className="brain model" onClick={() => setContextOpen(true)}>
            <span className="orb" />
            <span id="brainTxt">{modelMode === "auto" ? "auto orchestration" : modelLabel(modelMode, models)}</span>
          </button>
          <div className={`agent${agentMenuOpen ? " open" : ""}`}>
            <button className="agent-trigger" onClick={() => setAgentMenuOpen((value) => !value)}>
              {agent === "claude" ? "Claude Code" : "Codex"}
            </button>
            <div className="menu">
              <button className={agent === "claude" ? "on" : ""} onClick={() => selectAgent("claude")}>Claude Code</button>
              <button className={agent === "codex" ? "on" : ""} onClick={() => selectAgent("codex")}>Codex</button>
            </div>
          </div>
          <button className="ic" onClick={() => setJobsOpen(true)} title="Schemalagda jobb">
            ⏰
            {jobs.length > 0 && <span className="badge">{jobs.length}</span>}
          </button>
          <button className="ic reset" onClick={handleReset} title="Ny konversation">⟲</button>
          <div className="brain status" title={STATUS_LABEL[wsStatus]}>
            <span className="conn" style={{ background: wsStatus === "error" ? "var(--del)" : wsStatus === "connecting" ? "var(--amber)" : "var(--green)" }} />
            <span>{STATUS_LABEL[wsStatus]}</span>
          </div>
        </header>

        <div className="scroll">
          {!hasConversation ? (
            <section className="hero">
              <h1 className="greet">
                Bygg, granska och kör <span className="g">lokala agentflöden</span>.
              </h1>
              <p className="tag">
                Pilot håller ihop chatt, kod, datorstyrning och modellval i ett enda arbetsflöde.
              </p>
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
              <Transcript items={visibleTranscript} />
            </section>
          )}
        </div>

        {hasConversation && (
          <div className="dock">
            <ChatInput onSend={handleSend} onAbort={handleAbort} onOpenContext={() => setContextOpen(true)} disabled={wsStatus !== "connected"} running={running} />
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
          onReset={handleReset}
        />
      )}

      {controlsOpen && (
        <div className="scrim on" onClick={() => setControlsOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="mh">
              <span>⌘</span>
              <span className="nm">Projekt, modell och agent</span>
              <button className="x" onClick={() => setControlsOpen(false)}>✕</button>
            </div>
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
          </div>
        </div>
      )}

      {contextOpen && (
        <ContextModal
          transcript={transcript}
          compacted={focusView !== null}
          hiddenCount={Math.max(0, hiddenCount)}
          onCompactView={() => setFocusView(Math.min(8, transcript.length))}
          onClearContext={() => {
            setContextOpen(false);
            handleReset();
          }}
          onClose={() => setContextOpen(false)}
        />
      )}
      {jobsOpen && <JobsPanel jobs={jobs} onClose={() => setJobsOpen(false)} onAdd={addJob} onPause={pauseJob} onResume={resumeJob} onDelete={deleteJob} />}
    </>
  );
}
