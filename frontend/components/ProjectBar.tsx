"use client";

import { useEffect, useState } from "react";
import type { Project, Agent, ModelOption, AgentRoleOption } from "@/app/page";
import { useToast } from "@/components/Toast";

const RECENT_KEY = "pilot_recent_paths";

function loadRecent(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

interface Props {
  projects: Project[];
  selected: string | null; // selected project path (cwd)
  agent: Agent;
  modelMode: string; // "auto" or a pinned model id
  models: ModelOption[];
  agentRoles: AgentRoleOption[];
  routeMode: string; // "auto" or a forced route
  onSelect: (id: string) => void;
  onAdd: (path: string) => void;
  onRemove: (id: string) => void;
  onSelectAgent: (a: Agent) => void;
  onSelectModel: (mode: string) => void;
  onSelectRoute: (mode: string) => void;
}

const ROUTE_MODES: { id: string; label: string }[] = [
  { id: "auto", label: "Auto" },
  { id: "chat", label: "Chatt" },
  { id: "computer", label: "Dator" },
  { id: "code", label: "Kod" },
];

const AGENTS: { id: Agent; label: string }[] = [
  { id: "claude", label: "Claude Code" },
  { id: "codex", label: "Codex" },
];

export default function ProjectBar({ projects, selected, agent, modelMode, models, agentRoles, routeMode, onSelect, onAdd, onRemove, onSelectAgent, onSelectModel, onSelectRoute }: Props) {
  const [adding, setAdding] = useState(false);
  const [path, setPath] = useState("");
  const [error, setError] = useState("");
  const [recent, setRecent] = useState<string[]>([]);
  const toast = useToast();
  const selectedProj = projects.find((p) => p.path === selected) ?? null;

  useEffect(() => {
    setRecent(loadRecent());
  }, []);

  const addPath = (raw: string) => {
    const v = raw.trim();
    if (!v) {
      setError("Ange en sökväg till projektet.");
      return;
    }
    if (projects.some((p) => p.path === v)) {
      setError("Projektet finns redan i listan.");
      return;
    }
    onAdd(v);
    const nextRecent = [v, ...recent.filter((p) => p !== v)].slice(0, 5);
    setRecent(nextRecent);
    try {
      localStorage.setItem(RECENT_KEY, JSON.stringify(nextRecent));
    } catch {}
    toast.show("Lägger till projekt…", { kind: "info" });
    setPath("");
    setError("");
    setAdding(false);
  };

  const submitAdd = () => addPath(path);

  return (
    <div className="control-grid">
      <section className="control-card">
        <div className="control-head">
          <span className="seclabel">Projekt</span>
          {selectedProj && (
            <button className="control-pill danger" onClick={() => onRemove(selectedProj.id)} title="Ta bort projekt">
              Ta bort
            </button>
          )}
        </div>
        <select
          value={selectedProj?.id ?? ""}
          onChange={(e) => onSelect(e.target.value)}
          className="fld"
        >
          <option value="">— inget valt —</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id} title={p.path}>
              {p.name}
            </option>
          ))}
        </select>
        {selectedProj && <div className="pathline" title={selectedProj.path}>{selectedProj.path}</div>}
        {adding ? (
          <>
            <div className="addrow">
              <input
                value={path}
                onChange={(e) => {
                  setPath(e.target.value);
                  if (error) setError("");
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    submitAdd();
                  } else if (e.key === "Escape") {
                    setAdding(false);
                    setPath("");
                    setError("");
                  }
                }}
                placeholder="C:\\sökväg\\till\\projekt"
                autoFocus
                aria-invalid={error ? true : undefined}
                className="fld"
              />
              <button className="control-pill on" onClick={submitAdd}>Lägg till</button>
            </div>
            {error && <div className="form-error" role="alert">{error}</div>}
            {recent.filter((p) => !projects.some((proj) => proj.path === p)).length > 0 && (
              <div className="control-list">
                <span className="seclabel">Senaste sökvägar</span>
                <div className="control-inline">
                  {recent
                    .filter((p) => !projects.some((proj) => proj.path === p))
                    .map((p) => (
                      <button key={p} className="control-pill" title={p} onClick={() => addPath(p)}>
                        {p.split(/[\\/]/).filter(Boolean).at(-1) ?? p}
                      </button>
                    ))}
                </div>
              </div>
            )}
          </>
        ) : (
          <button className="control-pill" onClick={() => setAdding(true)}>＋ Lägg till projekt</button>
        )}
      </section>

      <section className="control-card">
        <div className="control-head">
          <span className="seclabel" title="Auto = Pilot väljer rutt per fråga. Annars tvingas läget.">Rutt</span>
        </div>
        <div className="control-inline">
          {ROUTE_MODES.map((r) => (
            <button
              key={r.id}
              onClick={() => onSelectRoute(r.id)}
              className={`control-pill${routeMode === r.id ? " on" : ""}`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </section>

      <section className="control-card">
        <div className="control-head">
          <span className="seclabel">Modell</span>
        </div>
        <select
          value={modelMode}
          onChange={(e) => onSelectModel(e.target.value)}
          title="Auto = Pilot väljer bästa lokala modell per fråga. Annars låses modellen."
          className="fld"
        >
          <option value="auto">Auto (väljer själv)</option>
          {models.map((m) => (
            <option key={m.id} value={m.id} title={m.hint}>
              {m.label}
            </option>
          ))}
        </select>
        <div className="control-list">
          {agentRoles.map((role) => (
            <div key={role.role} className="mrow">
              <div className="mi">{role.available ? "◔" : "!"}</div>
              <div>
                <div className="mt">{role.label}</div>
                <div className="ms">
                  {role.model_label}
                  {!role.available && ` (${role.model} saknas)`}
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="control-card">
        <div className="control-head">
          <span className="seclabel">Agent</span>
        </div>
        <div className="control-inline">
          {AGENTS.map((a) => (
            <button
              key={a.id}
              onClick={() => onSelectAgent(a.id)}
              title={`Kör kod-uppgifter med ${a.label}`}
              className={`control-pill${agent === a.id ? " on" : ""}`}
            >
              {a.label}
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
