"use client";

import { useCallback, useEffect, useState } from "react";
import type { Route, TranscriptItem, TurnEvent } from "@/app/page";
import Markdown from "@/components/Markdown";
import Dialog from "@/components/Dialog";
import { useToast } from "@/components/Toast";

function useCopy() {
  const toast = useToast();
  return useCallback(
    (text: string) => {
      copyText(text);
      toast.show("Kopierat.", { kind: "success" });
    },
    [toast]
  );
}

const ROUTE_LABEL: Record<Route, string> = {
  chat: "chatt",
  computer: "dator",
  code: "kod",
};

async function copyText(value: string) {
  try {
    await navigator.clipboard.writeText(value);
  } catch {}
}

type ArtifactDescriptor = {
  title: string;
  tag: string;
  tone?: "cyan" | "green" | "red" | "violet" | "dim";
  copyText?: string;
  expandText?: string;
  body: React.ReactNode;
};

type ArtifactUnit = {
  event: TurnEvent;
  relatedAction?: TurnEvent;
};

type TimelineStep = {
  id: number;
  kind: "thinking" | "action" | "consult" | "result" | "error" | "context" | "expert";
  title: string;
  detail?: string;
  accent?: "cyan" | "green" | "red" | "violet" | "dim";
  done?: boolean;
};

function formatArgs(args?: Record<string, unknown>) {
  if (!args) return "";
  const entries = Object.entries(args)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .slice(0, 3)
    .map(([key, value]) => `${key}=${String(value)}`);
  return entries.join(" · ");
}

function terminalBody(content: string) {
  const lines = content.split(/\r?\n/);
  return (
    <div className="term">
      {lines.map((line, index) => (
        <div key={`${line}-${index}`} className={line.trim().startsWith("$") ? "tl" : line.toLowerCase().includes("ok") ? "ok" : undefined}>
          {line || " "}
        </div>
      ))}
    </div>
  );
}

function diffBody(content: string) {
  return (
    <div className="diff">
      {content.split(/\r?\n/).map((line, index) => {
        const kind = line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "ctx";
        return <div key={`${line}-${index}`} className={`ln2 ${kind}`}>{line || " "}</div>;
      })}
    </div>
  );
}

function screenshotBody(source: string) {
  return (
    <div className="shot-real">
      <img
        src={source}
        alt="screenshot"
        style={{ width: "100%", display: "block" }}
      />
    </div>
  );
}

function relatedActionForEvent(events: TurnEvent[], eventId: number) {
  return [...events].reverse().find((candidate) => candidate.type === "action" && candidate.id < eventId);
}

function collectArtifactUnits(events: TurnEvent[]) {
  const units: ArtifactUnit[] = [];
  const artifactEvents = events.filter((event) => event.type === "screenshot" || event.type === "result" || event.type === "error");

  for (const event of artifactEvents) {
    const relatedAction = relatedActionForEvent(events, event.id);
    const previous = units.at(-1);
    const canMergeResult =
      event.type === "result" &&
      previous?.event.type === "result" &&
      previous.relatedAction?.id === relatedAction?.id;

    if (canMergeResult && previous) {
      previous.event = {
        ...previous.event,
        content: `${previous.event.content ?? ""}\n${event.content ?? ""}`.trim(),
      };
      continue;
    }

    units.push({ event: { ...event }, relatedAction });
  }

  return units.slice(-3);
}

function collectTimelineSteps(events: TurnEvent[]) {
  const steps: TimelineStep[] = [];

  for (const event of events) {
    const relatedAction = relatedActionForEvent(events, event.id);
    const previous = steps.at(-1);

    if (event.type === "result") {
      const content = event.content?.trim() ?? "";
      const lineCount = content ? content.split(/\r?\n/).filter(Boolean).length : 0;
      if (previous?.kind === "result" && relatedAction?.id === previous.id) {
        previous.detail = previous.detail
          ? `${previous.detail} · +${lineCount || 1} rader`
          : `+${lineCount || 1} rader`;
        continue;
      }
      steps.push({
        id: relatedAction?.id ?? event.id,
        kind: "result",
        title: relatedAction?.tool ? `${relatedAction.tool} returnerade output` : "Fick resultat",
        detail: lineCount > 0 ? `${lineCount} rader` : "1 rad",
        accent: "green",
        done: true,
      });
      continue;
    }

    if (event.type === "thinking") {
      steps.push({
        id: event.id,
        kind: "thinking",
        title: "Resonerar om nästa steg",
        detail: event.content,
        accent: "dim",
        done: true,
      });
      continue;
    }

    if (event.type === "action") {
      steps.push({
        id: event.id,
        kind: "action",
        title: event.tool ?? "Kör verktyg",
        detail: formatArgs(event.args),
        accent: "cyan",
        done: true,
      });
      continue;
    }

    if (event.type === "consult" || event.type === "expert") {
      steps.push({
        id: event.id,
        kind: event.type,
        title: event.type === "consult" ? `Frågar ${event.tool ?? "expert"}` : `${event.tool ?? "Expert"} svarar`,
        detail: event.content,
        accent: "violet",
        done: true,
      });
      continue;
    }

    if (event.type === "error") {
      steps.push({
        id: event.id,
        kind: "error",
        title: relatedAction?.tool ? `${relatedAction.tool} misslyckades` : "Fel uppstod",
        detail: event.content,
        accent: "red",
        done: true,
      });
      continue;
    }

    if (event.type === "context" && event.content) {
      steps.push({
        id: event.id,
        kind: "context",
        title: "Kontext uppdaterad",
        detail: event.content,
        accent: "dim",
        done: true,
      });
      continue;
    }
  }

  return steps.slice(-8);
}

function artifactDetails(event: TurnEvent, relatedAction?: TurnEvent): ArtifactDescriptor | null {
  if (event.type === "screenshot" && event.image) {
    const source = `data:image/png;base64,${event.image}`;
    return {
      title: relatedAction?.tool ? `${relatedAction.tool} · skärmbild` : "Browser snapshot",
      tag: "live",
      tone: "cyan",
      body: screenshotBody(source),
    };
  }

  if (event.type === "result" && event.content) {
    const fromCommand = relatedAction?.tool === "run_command";
    const isCommand = fromCommand || event.content.includes("Command:") || event.content.includes("Output:") || /^\$ /m.test(event.content);
    const diffLike = !isCommand && /^(@@|diff --git|\+[^+]|-[^-])/m.test(event.content);
    if (diffLike) {
      const title = relatedAction?.tool === "run_command" ? "Patch preview" : relatedAction?.tool ?? "Diff";
      return {
        title,
        tag: "diff",
        tone: "green",
        copyText: event.content,
        expandText: event.content,
        body: diffBody(event.content),
      };
    }
    return {
      title: relatedAction?.tool === "run_command" ? "Kommandoutdata" : relatedAction?.tool ?? (isCommand ? "Kommandoutdata" : "Resultat"),
      tag: isCommand ? "term" : "text",
      tone: isCommand ? "green" : "dim",
      copyText: event.content,
      expandText: event.content,
      body: isCommand ? terminalBody(event.content) : <div className="prose artifact-prose">{event.content}</div>,
    };
  }

  if (event.type === "error" && event.content) {
    return {
      title: relatedAction?.tool ? `${relatedAction.tool} · fel` : "Fel",
      tag: "error",
      tone: "red",
      copyText: event.content,
      expandText: event.content,
      body: <div className="term" style={{ color: "var(--del)" }}>{event.content}</div>,
    };
  }

  return null;
}

function toneStyle(tone: ArtifactDescriptor["tone"]) {
  if (tone === "cyan") return { background: "rgba(79,214,224,.15)", color: "var(--cyan)" };
  if (tone === "green") return { background: "rgba(84,217,140,.15)", color: "var(--green)" };
  if (tone === "red") return { background: "rgba(240,133,124,.15)", color: "var(--del)" };
  if (tone === "violet") return { background: "rgba(182,156,255,.15)", color: "var(--violet)" };
  return { color: "var(--dim)" };
}

function ArtifactCard({
  event,
  relatedAction,
}: {
  event: TurnEvent;
  relatedAction?: TurnEvent;
}) {
  const artifact = artifactDetails(event, relatedAction);
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);

  if (!artifact) return null;

  return (
    <>
      <div className="art">
        <div className="ah">
          <span>▣</span>
          <span className="nm">{artifact.title}</span>
          <span className="tg" style={toneStyle(artifact.tone)}>{artifact.tag}</span>
          <div className="acts">
            {artifact.copyText && (
              <button
                onClick={async () => {
                  await copyText(artifact.copyText ?? "");
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1400);
                }}
              >
                {copied ? "kopierat ✓" : "⎘ kopiera"}
              </button>
            )}
            {(artifact.expandText || event.type === "screenshot") && <button onClick={() => setExpanded(true)}>⤢ expandera</button>}
          </div>
        </div>
        <div className="ab">{artifact.body}</div>
      </div>
      {expanded && (
        <Dialog icon="▣" title={artifact.title} className="narrow artifact-modal" onClose={() => setExpanded(false)}>
          <div className="mb">
            {event.type === "screenshot" && event.image ? (
              screenshotBody(`data:image/png;base64,${event.image}`)
            ) : artifact.expandText ? (
              /^(@@|diff --git|\+[^+]|-[^-])/m.test(artifact.expandText) ? diffBody(artifact.expandText) : terminalBody(artifact.expandText)
            ) : (
              artifact.body
            )}
          </div>
        </Dialog>
      )}
    </>
  );
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

function stepTone(accent: TimelineStep["accent"]) {
  if (accent === "cyan") return " cyan";
  if (accent === "green") return " green";
  if (accent === "red") return " red";
  if (accent === "violet") return " violet";
  return "";
}

function Insyn({ events, done }: { events: TurnEvent[]; done: boolean }) {
  const [open, setOpen] = useState(!done);
  const steps = collectTimelineSteps(events);

  useEffect(() => {
    if (!done) setOpen(true);
  }, [done]);

  if (!steps.length) return null;

  return (
    <div className={`insyn${open ? " open" : ""}`}>
      <div className="head" onClick={() => setOpen((value) => !value)}>
        <span className="sp">◔</span>
        <span className="lbl">{insynLabel(events, done)}</span>
        <span className="chev">›</span>
      </div>
      <div className="steps">
        {steps.map((step, index) => (
          <div key={`${step.kind}-${step.id}-${index}`} className={`istep${done || index < steps.length - 1 || step.done ? " done" : ""}`}>
            <div className="g">
              <div className={`dot${stepTone(step.accent)}`}>{done || index < steps.length - 1 || step.done ? "✓" : "◔"}</div>
              <div className="ln" />
            </div>
            <div className="t">
              <b>{step.title}</b>
              {step.detail ? <span className="meta"> · {step.detail}</span> : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function UserBubble({
  id,
  text,
  onEdit,
  onResend,
}: {
  id: number;
  text: string;
  onEdit: (text: string) => void;
  onResend: (text: string) => void;
}) {
  const copy = useCopy();
  return (
    <div className="u-wrap">
      <div className="u" id={`msg-${id}`}>{text}</div>
      <div className="msg-actions">
        <button onClick={() => copy(text)} aria-label="Kopiera prompt">⎘</button>
        <button onClick={() => onEdit(text)} aria-label="Redigera i rutan">✎</button>
        <button onClick={() => onResend(text)} aria-label="Skicka igen">↻</button>
      </div>
    </div>
  );
}

function AssistantTurn({ item }: { item: Extract<TranscriptItem, { kind: "assistant" }> }) {
  const copy = useCopy();
  const memoryEvents = item.events.filter((event) => event.type === "memory" && event.content);
  const artifactUnits = collectArtifactUnits(item.events);
  const actionEvents = item.events.filter((event) => event.type === "action");
  const toolCount = item.events.filter((event) => event.type === "action").length;
  const expertCount = item.events.filter((event) => event.type === "consult" || event.type === "expert").length;
  const toolMeta = actionEvents.slice(-3).map((event) => ({
    id: event.id,
    name: event.tool ?? "verktyg",
    args: formatArgs(event.args),
  }));

  return (
    <div className="turn">
      <div className="rail">
        <div className="mk">✦</div>
        <div className="spine" />
      </div>
      <div className="body">
        <Insyn events={item.events} done={item.done} />
        <div className="fu">
          {item.route && <div className={`rbadge${!item.done && item.events.length === 0 ? " clarify" : ""}`}>{ROUTE_LABEL[item.route]}</div>}
          {item.model && <div className="rbadge">{item.model}</div>}
          {toolCount > 0 && <div className="rbadge">{toolCount} verktyg</div>}
          {expertCount > 0 && <div className="rbadge">{expertCount} experter</div>}
        </div>
        {toolMeta.length > 0 && (
          <div className="toolstrip">
            {toolMeta.map((tool) => (
              <div key={tool.id} className="toolchip">
                <span className="tn">{tool.name}</span>
                {tool.args && <span className="ta">{tool.args}</span>}
              </div>
            ))}
          </div>
        )}
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
        {artifactUnits.length > 0 && (
          <div className="artifact-stack">
            {artifactUnits.length > 1 && <div className="artifact-label">Artifacts</div>}
            {artifactUnits.map(({ event, relatedAction }) => (
              <ArtifactCard
                key={`artifact-${event.id}`}
                event={event}
                relatedAction={relatedAction}
              />
            ))}
          </div>
        )}
        {item.cwd && <div className="rbadge" style={{ marginTop: 10 }}>cwd · {item.cwd}</div>}
        {item.text && item.done && (
          <div className="msg-actions left">
            <button onClick={() => copy(item.text)} aria-label="Kopiera svar">⎘ Kopiera</button>
          </div>
        )}
      </div>
    </div>
  );
}

export default function Transcript({
  items,
  onEdit,
  onResend,
}: {
  items: TranscriptItem[];
  onEdit: (text: string) => void;
  onResend: (text: string) => void;
}) {
  return (
    <>
      {items.map((item) =>
        item.kind === "user" ? (
          <UserBubble key={item.id} id={item.id} text={item.text} onEdit={onEdit} onResend={onResend} />
        ) : (
          <AssistantTurn key={item.id} item={item} />
        )
      )}
    </>
  );
}
