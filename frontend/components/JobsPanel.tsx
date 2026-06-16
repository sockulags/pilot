"use client";

import { useState } from "react";
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

  const toggleDay = (d: number) => {
    setWeekdays((prev) => (prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d].sort((a, b) => a - b)));
  };

  const buildSchedule = (): JobSchedule | null => {
    if (stype === "interval") {
      return { type: "interval", interval_seconds: Math.max(1, Math.round(intervalN)) * UNIT_SECONDS[unit] };
    }
    if (stype === "daily") return { type: "daily", time };
    if (stype === "weekly") return weekdays.length ? { type: "weekly", time, weekdays } : null;
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
    <div className="scrim on" onClick={onClose}>
      <div className="modal narrow" onClick={(e) => e.stopPropagation()}>
        <div className="mh">
          <span>⏰</span>
          <span className="nm">Schemalagda jobb</span>
          <button className="x" onClick={onClose}>✕</button>
        </div>
        <div className="mb">
          {jobs.length === 0 ? (
            <p style={{ color: "var(--dim)" }}>Inga jobb ännu.</p>
          ) : (
            jobs.map((job) => (
              <div key={job.id} className="jrow" style={{ opacity: job.enabled ? 1 : 0.55 }}>
                <div className="ji">{job.kind === "task" ? "✦" : "⏰"}</div>
                <div>
                  <div className="jt">{job.title}</div>
                  <div className="js">
                    {job.kind === "task" ? "uppgift · " : ""}
                    {job.summary} · nästa {job.next_run_label}
                    {!job.enabled ? " · pausad" : ""}
                  </div>
                </div>
                <div className="jx">
                  <button onClick={() => (job.enabled ? onPause(job.id) : onResume(job.id))}>
                    {job.enabled ? "⏸" : "▶"}
                  </button>
                  <button onClick={() => onDelete(job.id)}>✕</button>
                </div>
              </div>
            ))
          )}

          <div className="seclabel">Nytt jobb</div>
          <div className="seg2" style={{ marginBottom: 10 }}>
            <button className={kind === "reminder" ? "on" : ""} onClick={() => setKind("reminder")}>Påminnelse</button>
            <button className={kind === "task" ? "on" : ""} onClick={() => setKind("task")}>Uppgift</button>
          </div>

          <div className="jadd" style={{ marginBottom: 10 }}>
            <select className="fld" value={stype} onChange={(e) => setStype(e.target.value as SType)}>
              <option value="interval">Intervall</option>
              <option value="daily">Dagligen</option>
              <option value="weekly">Veckodagar</option>
              <option value="once">En gång</option>
            </select>

            {stype === "interval" && (
              <>
                <input className="fld" type="number" min={1} value={intervalN} onChange={(e) => setIntervalN(Number(e.target.value))} style={{ width: 90 }} />
                <select className="fld" value={unit} onChange={(e) => setUnit(e.target.value as keyof typeof UNIT_SECONDS)}>
                  <option value="min">min</option>
                  <option value="h">timme</option>
                  <option value="dygn">dygn</option>
                </select>
              </>
            )}

            {stype === "once" && <input className="fld" type="date" value={date} onChange={(e) => setDate(e.target.value)} />}
            {(stype === "daily" || stype === "weekly" || stype === "once") && (
              <input className="fld" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
            )}
          </div>

          {stype === "weekly" && (
            <div className="seg2" style={{ marginBottom: 10, flexWrap: "wrap" }}>
              {WEEKDAYS.map((label, idx) => (
                <button key={label} className={weekdays.includes(idx) ? "on" : ""} onClick={() => toggleDay(idx)}>
                  {label}
                </button>
              ))}
            </div>
          )}

          <div className="jadd">
            <input
              className="fld"
              value={payload}
              onChange={(e) => setPayload(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder={kind === "task" ? "Instruktion till Pilot…" : "Påminnelsetext…"}
              style={{ flex: 1 }}
            />
            <button className="addbtn" onClick={submit}>Lägg till</button>
          </div>
        </div>
      </div>
    </div>
  );
}
