"use client";

import { useState, CSSProperties } from "react";
import type { Project, Agent, ModelOption } from "@/app/page";

interface Props {
  projects: Project[];
  selected: string | null; // selected project path (cwd)
  agent: Agent;
  modelMode: string; // "auto" or a pinned model id
  models: ModelOption[];
  onSelect: (id: string) => void;
  onAdd: (path: string) => void;
  onRemove: (id: string) => void;
  onSelectAgent: (a: Agent) => void;
  onSelectModel: (mode: string) => void;
}

const AGENTS: { id: Agent; label: string }[] = [
  { id: "claude", label: "Claude Code" },
  { id: "codex", label: "Codex" },
];

const btn: CSSProperties = {
  background: "none",
  color: "var(--muted)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  padding: "0.3rem 0.5rem",
  cursor: "pointer",
  fontSize: "0.78rem",
  whiteSpace: "nowrap",
};

const field: CSSProperties = {
  background: "var(--surface)",
  color: "var(--text)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  padding: "0.3rem 0.5rem",
  fontSize: "0.8rem",
};

export default function ProjectBar({ projects, selected, agent, modelMode, models, onSelect, onAdd, onRemove, onSelectAgent, onSelectModel }: Props) {
  const [adding, setAdding] = useState(false);
  const [path, setPath] = useState("");
  const selectedProj = projects.find((p) => p.path === selected) ?? null;

  const submitAdd = () => {
    const v = path.trim();
    if (!v) return;
    onAdd(v);
    setPath("");
    setAdding(false);
  };

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
      <span style={{ color: "var(--muted)", fontSize: "0.8rem" }}>Projekt:</span>
      <select
        value={selectedProj?.id ?? ""}
        onChange={(e) => onSelect(e.target.value)}
        style={{ ...field, maxWidth: 260 }}
      >
        <option value="">— inget valt —</option>
        {projects.map((p) => (
          <option key={p.id} value={p.id} title={p.path}>
            {p.name}
          </option>
        ))}
      </select>

      {selectedProj && (
        <button onClick={() => onRemove(selectedProj.id)} title="Ta bort projekt" style={btn}>
          ✕
        </button>
      )}

      {adding ? (
        <span style={{ display: "flex", gap: "0.35rem", alignItems: "center" }}>
          <input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submitAdd();
              } else if (e.key === "Escape") {
                setAdding(false);
                setPath("");
              }
            }}
            placeholder="C:\\sökväg\\till\\projekt"
            autoFocus
            style={{ ...field, width: 240 }}
          />
          <button onClick={submitAdd} style={{ ...btn, color: "var(--accent)" }}>
            Lägg till
          </button>
        </span>
      ) : (
        <button onClick={() => setAdding(true)} style={btn}>
          ＋ Projekt
        </button>
      )}

      {selectedProj && (
        <span
          title={selectedProj.path}
          style={{ color: "var(--muted)", fontFamily: "monospace", fontSize: "0.72rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 240 }}
        >
          {selectedProj.path}
        </span>
      )}

      <span style={{ marginLeft: "auto", display: "flex", gap: "0.35rem", alignItems: "center" }}>
        <span style={{ color: "var(--muted)", fontSize: "0.8rem" }}>Modell:</span>
        <select
          value={modelMode}
          onChange={(e) => onSelectModel(e.target.value)}
          title="Auto = Pilot väljer bästa lokala modell per fråga. Annars låses modellen."
          style={{ ...field, maxWidth: 200 }}
        >
          <option value="auto">Auto (väljer själv)</option>
          {models.map((m) => (
            <option key={m.id} value={m.id} title={m.hint}>
              {m.label}
            </option>
          ))}
        </select>
      </span>

      <span style={{ display: "flex", gap: "0.25rem", alignItems: "center" }}>
        <span style={{ color: "var(--muted)", fontSize: "0.8rem" }}>Agent:</span>
        {AGENTS.map((a) => (
          <button
            key={a.id}
            onClick={() => onSelectAgent(a.id)}
            title={`Kör kod-uppgifter med ${a.label}`}
            style={{
              ...btn,
              ...(agent === a.id
                ? { color: "var(--text)", borderColor: "var(--accent)", background: "rgba(99,102,241,0.12)" }
                : {}),
            }}
          >
            {a.label}
          </button>
        ))}
      </span>
    </div>
  );
}
