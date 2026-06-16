"use client";

import { useEffect, useRef, useState } from "react";
import type { Route, TranscriptItem, TurnEvent } from "@/app/page";
import Markdown from "@/components/Markdown";

const ROUTE_LABEL: Record<Route, string> = {
  chat: "chatt",
  computer: "dator",
  code: "kod",
};

function artifactForEvent(event: TurnEvent) {
  if (event.type === "screenshot" && event.image) {
    return (
      <div className="art">
        <div className="ah">
          <span>▣</span>
          <span className="nm">Skärmbild</span>
          <span className="tg" style={{ background: "rgba(79,214,224,.15)", color: "var(--cyan)" }}>live</span>
        </div>
        <div className="ab">
          <img
            src={`data:image/png;base64,${event.image}`}
            alt="screenshot"
            style={{ width: "100%", borderRadius: 9, border: "1px solid var(--border)" }}
          />
        </div>
      </div>
    );
  }

  if (event.type === "result" && event.content) {
    const isCommand = event.content.includes("Command:") || event.content.includes("Output:");
    return (
      <div className="art">
        <div className="ah">
          <span>▣</span>
          <span className="nm">{isCommand ? "Kommandoutdata" : "Resultat"}</span>
          <span className="tg" style={{ color: "var(--dim)" }}>{isCommand ? "term" : "text"}</span>
        </div>
        <div className="ab">
          <div className={isCommand ? "term" : "prose"}>
            {event.content}
          </div>
        </div>
      </div>
    );
  }

  if (event.type === "error" && event.content) {
    return (
      <div className="art">
        <div className="ah">
          <span>▣</span>
          <span className="nm">Fel</span>
          <span className="tg" style={{ background: "rgba(240,133,124,.15)", color: "var(--del)" }}>error</span>
        </div>
        <div className="ab">
          <div className="term" style={{ color: "var(--del)" }}>
            {event.content}
          </div>
        </div>
      </div>
    );
  }

  return null;
}

function insynLabel(events: TurnEvent[], done: boolean) {
  if (!events.length) return done ? "✓ svarade direkt" : "tänker…";
  const actions = events.filter((event) => event.type === "action").length;
  const consults = events.filter((event) => event.type === "consult").length;
  const parts = [];
  if (consults) parts.push(`frågade ${consults} expert${consults > 1 ? "er" : ""}`);
  if (actions) parts.push(`${actions} verktyg`);
  return done ? `✓ ${parts.join(" · ") || "klar"}` : (parts.join(" · ") || "arbetar…");
}

function Insyn({ events, done }: { events: TurnEvent[]; done: boolean }) {
  const [open, setOpen] = useState(!done);

  useEffect(() => {
    if (!done) setOpen(true);
  }, [done]);

  if (!events.length) return null;

  return (
    <div className={`insyn${open ? " open" : ""}`}>
      <div className="head" onClick={() => setOpen((value) => !value)}>
        <span className="sp">◔</span>
        <span className="lbl">{insynLabel(events, done)}</span>
        <span className="chev">›</span>
      </div>
      <div className="steps">
        {events.map((event, index) => (
          <div key={event.id} className={`istep${done || index < events.length - 1 ? " done" : ""}`}>
            <div className="g">
              <div className="dot">{done || index < events.length - 1 ? "✓" : "◔"}</div>
              <div className="ln" />
            </div>
            <div className="t">
              <b>{event.type === "consult" ? `frågar ${event.tool ?? "expert"}` : event.type}</b>
              {event.content ? ` · ${event.content}` : ""}
              {event.type === "action" && event.tool ? ` · ${event.tool}` : ""}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function UserBubble({ text }: { text: string }) {
  return <div className="u">{text}</div>;
}

function AssistantTurn({ item }: { item: Extract<TranscriptItem, { kind: "assistant" }> }) {
  const memoryEvents = item.events.filter((event) => event.type === "memory" && event.content);
  const artifactEvents = item.events
    .filter((event) => event.type === "screenshot" || event.type === "result" || event.type === "error")
    .slice(-3);

  return (
    <div className="turn">
      <div className="rail">
        <div className="mk">✦</div>
        <div className="spine" />
      </div>
      <div className="body">
        <Insyn events={item.events} done={item.done} />
        {item.route && <div className={`rbadge${!item.done && item.events.length === 0 ? " clarify" : ""}`}>{ROUTE_LABEL[item.route]}</div>}
        {item.text && (
          <div style={{ position: "relative" }}>
            <Markdown>{item.text}</Markdown>
            {!item.done && <span className="cur" />}
          </div>
        )}
        {!item.text && !item.done && <div className="prose"><p>Arbetar…</p></div>}
        {item.summary && item.route === "computer" && (
          <div className="savechip" style={{ color: "var(--cyan)", borderColor: "rgba(79,214,224,.3)", background: "rgba(79,214,224,.07)" }}>
            ▣ {item.summary}
          </div>
        )}
        {memoryEvents.map((event) => (
          <div key={event.id} className="savechip">💾 {event.content}</div>
        ))}
        {artifactEvents.map((event) => (
          <div key={`artifact-${event.id}`}>{artifactForEvent(event)}</div>
        ))}
        {item.cwd && <div className="rbadge" style={{ marginTop: 10 }}>cwd · {item.cwd}</div>}
      </div>
    </div>
  );
}

export default function Transcript({ items }: { items: TranscriptItem[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [items]);

  return (
    <>
      {items.map((item) =>
        item.kind === "user" ? <UserBubble key={item.id} text={item.text} /> : <AssistantTurn key={item.id} item={item} />
      )}
      <div ref={bottomRef} />
    </>
  );
}
