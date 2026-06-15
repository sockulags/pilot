"use client";

import { useState, CSSProperties } from "react";
import type { Job, JobSchedule } from "@/app/page";

interface Props {
  jobs: Job[];
  onClose: () => void;
  onAdd: (payload: string, schedule: JobSchedule, title: string, kind: string) => void;
  onPause: (id: string) => void;
  onResume: (id: string) => void;
  onDelete: (id: string) => void;
}

type SType = "interval" | "daily" | "weekly" | "once";

const WEEKDAYS = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"];
const UNIT_SECONDS: Record<string, number> = { min: 60, h: 3600, dygn: 86400 };

const btn: CSSProperties = {
  background: "none",
  color: "var(--muted)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  padding: "0.3rem 0.55rem",
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

function todayStr(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function JobsPanel({ jobs, onClose, onAdd, onPause, onResume, onDelete }: Props) {
  const [stype, setStype] = useState<SType>("daily");
  const [intervalN, setIntervalN] = useState(10);
  const [unit, setUnit] = useState<keyof typeof UNIT_SECONDS>("min");
  const [time, setTime] = useState("09:00");
  const [weekdays, setWeekdays] = useState<number[]>([]);
  const [date, setDate] = useState(todayStr());
  const [kind, setKind] = useState<"reminder" | "task">("reminder");
  const [payload, setPayload] = useState("");

  const toggleDay = (d: number) =>
    setWeekdays((prev) => (prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d].sort((a, b) => a - b)));

  const buildSchedule = (): JobSchedule | null => {
    if (stype === "interval") {
      const secs = Math.max(1, Math.round(intervalN)) * UNIT_SECONDS[unit];
      return { type: "interval", interval_seconds: secs };
    }
    if (stype === "daily") return { type: "daily", time };
    if (stype === "weekly") {
      if (weekdays.length === 0) return null;
      return { type: "weekly", time, weekdays };
    }
    return { type: "once", date, time };
  };

  const submit = () => {
    const text = payload.trim();
    const schedule = buildSchedule();
    if (!text || !schedule) return;
    onAdd(text, schedule, text.slice(0, 60), kind);
    setPayload("");
  };

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
        display: "flex", alignItems: "flex-start", justifyContent: "center",
        zIndex: 50, padding: "2rem 1rem", overflowY: "auto",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg, #111)", border: "1px solid var(--border)",
          borderRadius: 10, padding: "1.1rem", width: "100%", maxWidth: 560,
          display: "flex", flexDirection: "column", gap: "0.9rem",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h2 style={{ fontSize: "1.05rem", fontWeight: 700, color: "var(--accent)" }}>Schemalagda jobb</h2>
          <button onClick={onClose} style={btn}>Stäng</button>
        </div>

        {/* List */}
        <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}>
          {jobs.length === 0 && (
            <p style={{ color: "var(--muted)", fontSize: "0.82rem" }}>Inga jobb ännu.</p>
          )}
          {jobs.map((j) => (
            <div
              key={j.id}
              style={{
                display: "flex", alignItems: "center", gap: "0.5rem",
                border: "1px solid var(--border)", borderRadius: 8, padding: "0.45rem 0.6rem",
                opacity: j.enabled ? 1 : 0.55,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ color: "var(--text)", fontSize: "0.84rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {j.title}
                </div>
                <div style={{ color: "var(--muted)", fontSize: "0.72rem" }}>
                  {j.kind === "task" ? "uppgift · " : ""}{j.summary} · nästa {j.next_run_label}
                  {!j.enabled && " · pausad"}
                </div>
              </div>
              <button
                onClick={() => (j.enabled ? onPause(j.id) : onResume(j.id))}
                title={j.enabled ? "Pausa" : "Återuppta"}
                style={btn}
              >
                {j.enabled ? "⏸" : "▶"}
              </button>
              <button onClick={() => onDelete(j.id)} title="Ta bort" style={{ ...btn, color: "var(--red)" }}>
                ✕
              </button>
            </div>
          ))}
        </div>

        {/* New job form */}
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: "0.8rem", display: "flex", flexDirection: "column", gap: "0.55rem" }}>
          <span style={{ color: "var(--muted)", fontSize: "0.8rem", fontWeight: 600 }}>Nytt jobb</span>

          <div style={{ display: "flex", gap: "0.3rem", alignItems: "center" }}>
            {([["reminder", "Påminnelse"], ["task", "Uppgift (kör Pilot)"]] as const).map(([k, label]) => (
              <button
                key={k}
                onClick={() => setKind(k)}
                title={k === "task" ? "Pilot utför instruktionen på schemat och levererar resultatet" : "Levererar texten som en påminnelse"}
                style={{
                  ...btn,
                  ...(kind === k
                    ? { color: "var(--text)", borderColor: "var(--accent)", background: "rgba(99,102,241,0.12)" }
                    : {}),
                }}
              >
                {label}
              </button>
            ))}
          </div>

          <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", alignItems: "center" }}>
            <select value={stype} onChange={(e) => setStype(e.target.value as SType)} style={field}>
              <option value="interval">Intervall</option>
              <option value="daily">Dagligen</option>
              <option value="weekly">Veckodagar</option>
              <option value="once">En gång</option>
            </select>

            {stype === "interval" && (
              <>
                <span style={{ color: "var(--muted)", fontSize: "0.8rem" }}>var</span>
                <input
                  type="number" min={1} value={intervalN}
                  onChange={(e) => setIntervalN(Number(e.target.value))}
                  style={{ ...field, width: 70 }}
                />
                <select value={unit} onChange={(e) => setUnit(e.target.value as keyof typeof UNIT_SECONDS)} style={field}>
                  <option value="min">min</option>
                  <option value="h">timme</option>
                  <option value="dygn">dygn</option>
                </select>
              </>
            )}

            {stype === "once" && (
              <input type="date" value={date} onChange={(e) => setDate(e.target.value)} style={field} />
            )}

            {(stype === "daily" || stype === "weekly" || stype === "once") && (
              <input type="time" value={time} onChange={(e) => setTime(e.target.value)} style={field} />
            )}
          </div>

          {stype === "weekly" && (
            <div style={{ display: "flex", gap: "0.3rem", flexWrap: "wrap" }}>
              {WEEKDAYS.map((label, d) => (
                <button
                  key={d}
                  onClick={() => toggleDay(d)}
                  style={{
                    ...btn,
                    ...(weekdays.includes(d)
                      ? { color: "var(--text)", borderColor: "var(--accent)", background: "rgba(99,102,241,0.12)" }
                      : {}),
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          )}

          <div style={{ display: "flex", gap: "0.4rem" }}>
            <input
              value={payload}
              onChange={(e) => setPayload(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } }}
              placeholder={kind === "task" ? "Instruktion till Pilot…" : "Påminnelsetext…"}
              style={{ ...field, flex: 1 }}
            />
            <button
              onClick={submit}
              disabled={!payload.trim() || (stype === "weekly" && weekdays.length === 0)}
              style={{ ...btn, color: "var(--accent)", borderColor: "var(--accent)" }}
            >
              Lägg till
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
