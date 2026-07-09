"use client";

import { useEffect, useState } from "react";
import type { Project, Agent, ModelOption, AgentRoleOption } from "@/app/page";
import { useToast } from "@/components/Toast";
import { Badge, Button, Card, CardHead, Field, SectionLabel, SegControl, Select } from "@/components/ui";
import { t } from "@/app/strings";

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

const ROUTE_MODES = t.routeModes;
const AGENTS = t.agents;

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
      setError(t.projects.needPath);
      return;
    }
    if (projects.some((p) => p.path === v)) {
      setError(t.projects.duplicate);
      return;
    }
    onAdd(v);
    const nextRecent = [v, ...recent.filter((p) => p !== v)].slice(0, 5);
    setRecent(nextRecent);
    try {
      localStorage.setItem(RECENT_KEY, JSON.stringify(nextRecent));
    } catch {}
    toast.show(t.projects.adding, { kind: "info" });
    setPath("");
    setError("");
    setAdding(false);
  };

  const submitAdd = () => addPath(path);

  const projectOptions = [
    { value: "", label: "— inget valt —" },
    ...projects.map((p) => ({ value: p.id, label: p.name, title: p.path })),
  ];
  const modelOptions = [
    { value: "auto", label: "Auto (väljer själv)" },
    ...models.map((m) => ({ value: m.id, label: m.label, title: m.hint })),
  ];
  const unseenRecent = recent.filter((p) => !projects.some((proj) => proj.path === p));

  return (
    <div className="control-grid">
      <Card inset>
        <CardHead>
          <SectionLabel>Projekt</SectionLabel>
          {selectedProj && (
            <Button variant="danger" size="sm" onClick={() => onRemove(selectedProj.id)} title="Ta bort projekt">
              Ta bort
            </Button>
          )}
        </CardHead>
        <Select
          options={projectOptions}
          value={selectedProj?.id ?? ""}
          onChange={onSelect}
          fullWidth
          aria-label="Projekt"
        />
        {selectedProj && <div className="pathline" title={selectedProj.path}>{selectedProj.path}</div>}
        {adding ? (
          <>
            <div className="addrow" style={{ marginTop: 10 }}>
              <Field
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
                placeholder="C:\sökväg\till\projekt"
                autoFocus
                invalid={!!error}
                fullWidth
              />
              <Button variant="primary" size="sm" onClick={submitAdd}>Lägg till</Button>
            </div>
            {error && <div className="form-error" role="alert">{error}</div>}
            {unseenRecent.length > 0 && (
              <div className="control-list">
                <SectionLabel>{t.projects.recentPaths}</SectionLabel>
                <div className="control-inline" style={{ marginTop: 8 }}>
                  {unseenRecent.map((p) => (
                    <Button key={p} variant="secondary" size="sm" title={p} onClick={() => addPath(p)}>
                      {p.split(/[\\/]/).filter(Boolean).at(-1) ?? p}
                    </Button>
                  ))}
                </div>
              </div>
            )}
          </>
        ) : (
          <Button variant="ghost" size="sm" onClick={() => setAdding(true)} style={{ marginTop: 10 }}>{t.projects.addProject}</Button>
        )}
      </Card>

      <Card inset>
        <CardHead>
          <SectionLabel title="Auto = Pilot väljer rutt per fråga. Annars tvingas läget.">Rutt</SectionLabel>
        </CardHead>
        <SegControl
          options={ROUTE_MODES.map((r) => ({ value: r.id, label: r.label }))}
          value={routeMode}
          onChange={onSelectRoute}
          wrap
          aria-label="Rutt"
        />
      </Card>

      <Card inset>
        <CardHead>
          <SectionLabel>Modell</SectionLabel>
        </CardHead>
        <Select
          options={modelOptions}
          value={modelMode}
          onChange={onSelectModel}
          fullWidth
          title="Auto = Pilot väljer bästa lokala modell per fråga. Annars låses modellen."
          aria-label="Modell"
        />
        <div className="control-list">
          {agentRoles.map((role) => (
            <div key={role.role} className="mrow">
              <div className="mi">{role.available ? "◔" : "!"}</div>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="mt">{role.label}</div>
                <div className="ms">
                  {role.model_label}
                  {!role.available && ` (${role.model} saknas)`}
                </div>
              </div>
              {!role.available && <Badge variant="soft" tone="amber">saknas</Badge>}
            </div>
          ))}
        </div>
      </Card>

      <Card inset>
        <CardHead>
          <SectionLabel>Agent</SectionLabel>
        </CardHead>
        <SegControl
          options={AGENTS.map((a) => ({ value: a.id, label: a.label }))}
          value={agent}
          onChange={(v) => onSelectAgent(v as Agent)}
          aria-label="Kodagent"
        />
      </Card>
    </div>
  );
}
