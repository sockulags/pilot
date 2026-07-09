"use client";

import { useState } from "react";
import type { Job, JobSchedule } from "@/app/page";
import Dialog from "@/components/Dialog";
import { useToast } from "@/components/Toast";
import { Button, cn, Field, SectionLabel, SegControl, Select } from "@/components/ui";
import { t } from "@/app/strings";

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
  const [error, setError] = useState("");
  const toast = useToast();

  const toggleDay = (d: number) => {
    setWeekdays((prev) => (prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d].sort((a, b) => a - b)));
    setError("");
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
    if (!text) {
      setError(kind === "task" ? t.jobs.needInstruction : t.jobs.needReminder);
      return;
    }
    if (stype === "weekly" && weekdays.length === 0) {
      setError(t.jobs.needWeekday);
      return;
    }
    const schedule = buildSchedule();
    if (!schedule) {
      setError(t.jobs.badSchedule);
      return;
    }
    setError("");
    onAdd(text, schedule, text.slice(0, 60), kind);
    setPayload("");
    toast.show(t.jobs.added, { kind: "success" });
  };

  return (
    <Dialog icon="⏰" title={t.header.scheduledJobs} className="narrow" onClose={onClose}>
        <div className="mb">
          {jobs.length === 0 ? (
            <p style={{ color: "var(--dim)" }}>{t.jobs.none}</p>
          ) : (
            jobs.map((job) => (
              <div key={job.id} className="jrow" style={{ opacity: job.enabled ? 1 : 0.55 }}>
                <div className="ji">{job.kind === "task" ? "✦" : "⏰"}</div>
                <div>
                  <div className="jt">{job.title}</div>
                  <div className="js">
                    {job.kind === "task" ? t.jobs.taskPrefix : ""}
                    {job.summary} · nästa {job.next_run_label}
                    {!job.enabled ? t.jobs.paused : ""}
                  </div>
                </div>
                <div className="jx">
                  <button
                    onClick={() => (job.enabled ? onPause(job.id) : onResume(job.id))}
                    aria-label={job.enabled ? t.jobs.pause : t.jobs.resume}
                  >
                    {job.enabled ? "⏸" : "▶"}
                  </button>
                  <button onClick={() => onDelete(job.id)} aria-label={t.jobs.delete}>✕</button>
                </div>
              </div>
            ))
          )}

          <SectionLabel style={{ display: "block", margin: "16px 0 9px" }}>{t.jobs.newJob}</SectionLabel>
          <div style={{ marginBottom: 10 }}>
            <SegControl
              options={[{ value: "reminder", label: t.jobs.reminderKind }, { value: "task", label: t.jobs.taskKind }]}
              value={kind}
              onChange={(v) => setKind(v as "reminder" | "task")}
              aria-label="Jobbtyp"
            />
          </div>

          <div className="jadd" style={{ marginBottom: 10 }}>
            <Select
              options={[
                { value: "interval", label: "Intervall" },
                { value: "daily", label: "Dagligen" },
                { value: "weekly", label: "Veckodagar" },
                { value: "once", label: "En gång" },
              ]}
              value={stype}
              onChange={(v) => setStype(v as SType)}
              aria-label="Schematyp"
            />

            {stype === "interval" && (
              <>
                <Field type="number" min={1} value={intervalN} onChange={(e) => setIntervalN(Number(e.target.value))} style={{ width: 90 }} aria-label="Antal" />
                <Select
                  options={[
                    { value: "min", label: "min" },
                    { value: "h", label: "timme" },
                    { value: "dygn", label: "dygn" },
                  ]}
                  value={unit}
                  onChange={(v) => setUnit(v as keyof typeof UNIT_SECONDS)}
                  aria-label="Enhet"
                />
              </>
            )}

            {stype === "once" && <Field type="date" value={date} onChange={(e) => setDate(e.target.value)} aria-label="Datum" />}
            {(stype === "daily" || stype === "weekly" || stype === "once") && (
              <Field type="time" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Tid" />
            )}
          </div>

          {stype === "weekly" && (
            <div className="ds-seg ds-seg--wrap" role="group" aria-label="Veckodagar" style={{ marginBottom: 10 }}>
              {WEEKDAYS.map((label, idx) => (
                <button
                  key={label}
                  type="button"
                  className={cn("ds-seg__opt", weekdays.includes(idx) && "is-active")}
                  aria-pressed={weekdays.includes(idx)}
                  onClick={() => toggleDay(idx)}
                >
                  {label}
                </button>
              ))}
            </div>
          )}

          <div className="jadd">
            <Field
              value={payload}
              onChange={(e) => {
                setPayload(e.target.value);
                if (error) setError("");
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  submit();
                }
              }}
              placeholder={kind === "task" ? t.jobs.instructionPlaceholder : t.jobs.reminderPlaceholder}
              invalid={!!error}
              style={{ flex: 1 }}
            />
            <Button variant="primary" size="md" onClick={submit}>{t.common.add}</Button>
          </div>
          {error && <div className="form-error" role="alert">{error}</div>}
        </div>
    </Dialog>
  );
}
