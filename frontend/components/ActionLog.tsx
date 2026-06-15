"use client";

import { useEffect, useRef } from "react";
import type { Route, TranscriptItem, TurnEvent } from "@/app/page";

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

const ROUTE_BADGE: Record<Route, { label: string; color: string }> = {
  chat: { label: "💬 Chatt", color: "var(--muted)" },
  computer: { label: "🖥 Dator", color: "var(--blue)" },
  code: { label: "⌨ Kod", color: "var(--accent)" },
};

function UserBubble({ text }: { text: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "0.75rem" }}>
      <div style={{
        maxWidth: "85%",
        background: "var(--accent)",
        color: "var(--text)",
        borderRadius: "12px 12px 2px 12px",
        padding: "0.55rem 0.8rem",
        fontSize: "0.9rem",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}>
        {text}
      </div>
    </div>
  );
}

function RouteBadge({ route }: { route?: Route }) {
  if (!route) return null;
  const badge = ROUTE_BADGE[route];
  return (
    <span style={{
      fontSize: "0.65rem",
      fontWeight: 700,
      color: badge.color,
      textTransform: "uppercase",
      letterSpacing: "0.05em",
      marginBottom: "0.35rem",
      display: "inline-block",
    }}>
      {badge.label}
    </span>
  );
}

function ResultCard({ summary }: { summary: string }) {
  return (
    <div style={{
      background: "rgba(34, 197, 94, 0.08)",
      border: "1px solid var(--green)",
      borderRadius: 10,
      padding: "0.875rem 1rem",
      marginTop: "0.5rem",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", marginBottom: "0.5rem" }}>
        <span style={{ fontSize: "1rem" }}>✅</span>
        <span style={{ fontSize: "0.75rem", fontWeight: 700, color: "var(--green)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
          Resultat
        </span>
      </div>
      <p style={{ fontSize: "0.9rem", color: "var(--text)", whiteSpace: "pre-wrap", lineHeight: 1.6, margin: 0 }}>
        {summary}
      </p>
    </div>
  );
}

function EventRow({ event }: { event: TurnEvent }) {
  const icon = ICONS[event.type] ?? "•";
  const color = COLORS[event.type] ?? "var(--text)";
  const text = event.type === "action"
    ? `${event.tool ?? ""}${event.args ? " " + JSON.stringify(event.args) : ""}`
    : event.content ?? "";

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

function DetailsPanel({ events }: { events: TurnEvent[] }) {
  return (
    <details style={{ border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden", marginTop: "0.5rem" }}>
      <summary style={{ cursor: "pointer", padding: "0.5rem 0.7rem", color: "var(--muted)", fontSize: "0.8rem", userSelect: "none" }}>
        Detaljer ({events.length})
      </summary>
      <div style={{ padding: "0 0.7rem 0.5rem" }}>
        {events.map((e) => <EventRow key={e.id} event={e} />)}
      </div>
    </details>
  );
}

function AssistantTurn({ item }: { item: Extract<TranscriptItem, { kind: "assistant" }> }) {
  const showResult = item.summary && item.route === "computer";
  return (
    <div style={{ marginBottom: "1rem" }}>
      <RouteBadge route={item.route} />
      {item.text && (
        <div style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: "12px 12px 12px 2px",
          padding: "0.6rem 0.85rem",
          fontSize: "0.9rem",
          color: "var(--text)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          lineHeight: 1.5,
        }}>
          {item.text}
          {!item.done && <span style={{ opacity: 0.5 }}>▍</span>}
        </div>
      )}
      {!item.text && !item.done && item.events.length === 0 && (
        <div style={{ color: "var(--muted)", fontSize: "0.85rem", fontStyle: "italic" }}>tänker…</div>
      )}
      {item.events.length > 0 && <DetailsPanel events={item.events} />}
      {showResult && <ResultCard summary={item.summary!} />}
    </div>
  );
}

export default function Transcript({ items }: { items: TranscriptItem[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [items]);

  if (items.length === 0) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)", fontSize: "0.875rem", textAlign: "center" }}>
        Skriv ett meddelande. Pilot svarar, styr datorn, eller kopplar in Claude Code — beroende på vad du ber om.
      </div>
    );
  }

  return (
    <div style={{ flex: 1, overflowY: "auto", paddingRight: "0.25rem" }}>
      {items.map((item) =>
        item.kind === "user"
          ? <UserBubble key={item.id} text={item.text} />
          : <AssistantTurn key={item.id} item={item} />
      )}
      <div ref={bottomRef} />
    </div>
  );
}
