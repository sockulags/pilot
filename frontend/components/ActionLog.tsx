"use client";

import { useEffect, useRef } from "react";
import type { LogEvent } from "@/app/page";

const ICONS: Record<string, string> = {
  thinking: "💭",
  action: "⚡",
  result: "✓",
  screenshot: "📸",
  error: "❌",
};

const COLORS: Record<string, string> = {
  thinking: "var(--muted)",
  action: "var(--accent)",
  result: "var(--green)",
  screenshot: "var(--blue)",
  error: "var(--red)",
};

function ResultCard({ event }: { event: LogEvent }) {
  return (
    <div style={{
      background: "rgba(34, 197, 94, 0.08)",
      border: "1px solid var(--green)",
      borderRadius: 10,
      padding: "0.875rem 1rem",
      marginBottom: "0.75rem",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", marginBottom: "0.5rem" }}>
        <span style={{ fontSize: "1rem" }}>✅</span>
        <span style={{ fontSize: "0.75rem", fontWeight: 700, color: "var(--green)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
          Resultat
        </span>
      </div>
      <p style={{ fontSize: "0.9rem", color: "var(--text)", whiteSpace: "pre-wrap", lineHeight: 1.6, margin: 0 }}>
        {event.summary ?? "Klar"}
      </p>
    </div>
  );
}

function EventRow({ event }: { event: LogEvent }) {
  const icon = ICONS[event.type] ?? "•";
  const color = COLORS[event.type] ?? "var(--text)";

  let text = "";
  if (event.type === "action") {
    const argsStr = event.args ? " " + JSON.stringify(event.args) : "";
    text = `${event.tool}${argsStr}`;
  } else {
    text = event.content ?? "";
  }

  return (
    <div style={{ display: "flex", gap: "0.5rem", padding: "0.375rem 0", borderBottom: "1px solid var(--border)", alignItems: "flex-start" }}>
      <span style={{ fontSize: "0.85rem", flexShrink: 0, marginTop: 1 }}>{icon}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <span style={{ fontSize: "0.7rem", color: "var(--muted)", marginRight: "0.5rem", textTransform: "uppercase", fontWeight: 600 }}>
          {event.type}
        </span>
        {event.type === "screenshot" && event.image ? (
          <img
            src={`data:image/png;base64,${event.image}`}
            alt="screenshot"
            style={{ width: "100%", borderRadius: 6, marginTop: 4, border: "1px solid var(--border)" }}
          />
        ) : (
          <span style={{ color, fontSize: "0.875rem", wordBreak: "break-word", whiteSpace: "pre-wrap" }}>{text}</span>
        )}
      </div>
    </div>
  );
}

interface Props {
  events: LogEvent[];
}

export default function ActionLog({ events }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events]);

  if (events.length === 0) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)", fontSize: "0.875rem" }}>
        Ingen aktivitet. Ge agenten en uppgift ovan.
      </div>
    );
  }

  const doneEvents = events.filter((e) => e.type === "done");
  const streamEvents = events.filter((e) => e.type !== "done");

  return (
    <div style={{ flex: 1, overflowY: "auto", paddingRight: "0.25rem" }}>
      {doneEvents.length > 0 && (
        <ResultCard event={doneEvents[doneEvents.length - 1]} />
      )}
      {streamEvents.map((e) => (
        <EventRow key={e.id} event={e} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
