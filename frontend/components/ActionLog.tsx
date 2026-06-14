"use client";

import { useEffect, useRef } from "react";
import type { LogEvent } from "@/app/page";

const ICONS: Record<string, string> = {
  thinking: "💭",
  action: "⚡",
  result: "✓",
  screenshot: "📸",
  done: "✅",
  error: "❌",
};

const COLORS: Record<string, string> = {
  thinking: "var(--muted)",
  action: "var(--accent)",
  result: "var(--green)",
  screenshot: "var(--blue)",
  done: "var(--green)",
  error: "var(--red)",
};

function EventRow({ event }: { event: LogEvent }) {
  const icon = ICONS[event.type] ?? "•";
  const color = COLORS[event.type] ?? "var(--text)";

  let text = "";
  if (event.type === "action") {
    const argsStr = event.args ? " " + JSON.stringify(event.args) : "";
    text = `${event.tool}${argsStr}`;
  } else if (event.type === "done") {
    text = event.summary ?? "Done";
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

  return (
    <div style={{ flex: 1, overflowY: "auto", paddingRight: "0.25rem" }}>
      {events.map((e) => (
        <EventRow key={e.id} event={e} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
