"use client";

import { useEffect, useRef } from "react";
import type { Route, TranscriptItem, TurnEvent } from "@/app/page";

const ICONS: Record<string, string> = {
  thinking: "💭",
  context: "📁",
  action: "⚡",
  consult: "🔀",
  expert: "🧠",
  memory: "💾",
  codex_trace: "⌨",
  result: "✓",
  screenshot: "📸",
  error: "❌",
};

const COLORS: Record<string, string> = {
  thinking: "var(--muted)",
  context: "var(--muted)",
  action: "var(--accent)",
  consult: "var(--blue)",
  expert: "var(--text)",
  memory: "var(--green)",
  codex_trace: "var(--accent)",
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

function ModelChip({ model }: { model?: string }) {
  if (!model) return null;
  // Strip the ":latest"/":14b" tag for a compact label; keep full id on hover.
  const short = model.split(":")[0];
  return (
    <span
      title={`Svarade med ${model}`}
      style={{
        fontSize: "0.62rem",
        fontWeight: 600,
        color: "var(--muted)",
        border: "1px solid var(--border)",
        borderRadius: 5,
        padding: "0.05rem 0.35rem",
        marginLeft: "0.4rem",
        verticalAlign: "middle",
      }}
    >
      🧠 {short}
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
  // consult/expert rows carry the model name in `tool` — surface it as the label.
  const label =
    event.type === "consult" ? `frågar ${event.tool ?? "expert"}`
    : event.type === "expert" ? (event.tool ?? "expert")
    : event.type;

  return (
    <div style={{ display: "flex", gap: "0.5rem", padding: "0.375rem 0", borderBottom: "1px solid var(--border)", alignItems: "flex-start" }}>
      <span style={{ fontSize: "0.85rem", flexShrink: 0, marginTop: 1 }}>{icon}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <span style={{ fontSize: "0.7rem", color: event.type === "expert" ? "var(--blue)" : "var(--muted)", marginRight: "0.5rem", textTransform: "uppercase", fontWeight: 600 }}>
          {label}
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

// While a turn is still running, show the work live (open) so the user sees the
// coordinator consulting experts / acting. Once done it collapses to DetailsPanel.
function LivePanel({ events }: { events: TurnEvent[] }) {
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, marginTop: "0.5rem", padding: "0 0.7rem" }}>
      <div style={{ padding: "0.4rem 0 0.1rem", color: "var(--muted)", fontSize: "0.72rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em" }}>
        Arbetar…
      </div>
      {events.map((e) => <EventRow key={e.id} event={e} />)}
    </div>
  );
}

function CodexEvidence({ trace }: { trace: NonNullable<Extract<TranscriptItem, { kind: "assistant" }>["codexTrace"]> }) {
  const calls = trace.codex_tool_calls ?? [];
  return (
    <details style={{ border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden", marginTop: "0.5rem" }}>
      <summary style={{ cursor: "pointer", padding: "0.5rem 0.7rem", color: "var(--accent)", fontSize: "0.8rem", userSelect: "none" }}>
        Codex-bevis ({trace.codex_tool_call_count ?? 0} tool calls)
      </summary>
      <div style={{ padding: "0 0.7rem 0.6rem", fontSize: "0.8rem", lineHeight: 1.5, color: "var(--text)" }}>
        {trace.codex_session_id && <p style={{ margin: "0.35rem 0" }}><strong>Session:</strong> {trace.codex_session_id}</p>}
        {trace.codex_log_path && <p style={{ margin: "0.35rem 0", wordBreak: "break-word" }}><strong>Logg:</strong> {trace.codex_log_path}</p>}
        {trace.codex_prompt && <p style={{ margin: "0.35rem 0", whiteSpace: "pre-wrap" }}><strong>Prompt:</strong> {trace.codex_prompt}</p>}
        <p style={{ margin: "0.35rem 0" }}>
          <strong>Calls:</strong> shell {trace.codex_shell_call_count ?? 0}, MCP {trace.codex_mcp_call_count ?? 0}
        </p>
        {trace.codex_error_summary && (
          <p style={{ margin: "0.35rem 0", color: "var(--red)", whiteSpace: "pre-wrap" }}><strong>Fel:</strong> {trace.codex_error_summary}</p>
        )}
        {trace.codex_final_summary && (
          <p style={{ margin: "0.35rem 0", whiteSpace: "pre-wrap" }}><strong>Final:</strong> {trace.codex_final_summary}</p>
        )}
        {calls.length > 0 && (
          <div style={{ marginTop: "0.5rem" }}>
            <strong>Tool calls:</strong>
            {calls.slice(0, 10).map((call, idx) => (
              <div key={`${call.namespace}-${call.name}-${idx}`} style={{ marginTop: "0.3rem", paddingTop: "0.3rem", borderTop: "1px solid var(--border)" }}>
                <span style={{ color: "var(--accent)" }}>{call.namespace ? `${call.namespace}.` : ""}{call.name}</span>
                {call.arguments && <span style={{ display: "block", color: "var(--muted)", wordBreak: "break-word" }}>{call.arguments}</span>}
              </div>
            ))}
          </div>
        )}
      </div>
    </details>
  );
}

function AssistantTurn({ item }: { item: Extract<TranscriptItem, { kind: "assistant" }> }) {
  const showResult = item.summary && item.route === "computer";
  return (
    <div style={{ marginBottom: "1rem" }}>
      <RouteBadge route={item.route} />
      <ModelChip model={item.model} />
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
      {item.cwd && (
        <div style={{ color: "var(--muted)", fontSize: "0.75rem", marginTop: "0.35rem", wordBreak: "break-word" }}>
          CWD: {item.cwd}
        </div>
      )}
      {item.events.length > 0 && (item.done ? <DetailsPanel events={item.events} /> : <LivePanel events={item.events} />)}
      {item.codexTrace && <CodexEvidence trace={item.codexTrace} />}
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
