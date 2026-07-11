"use client";

import { useCallback, useEffect, useState } from "react";
import type { Route, RouteInsight as RouteInsightData, TranscriptItem, TurnEvent } from "@/app/page";
import Markdown from "@/components/Markdown";
import Dialog from "@/components/Dialog";
import { useToast } from "@/components/Toast";
import { ArtifactCard as DsArtifactCard, BrowserFrame, Diff, Terminal } from "@/components/ui";
import { t } from "@/app/strings";

function useCopy() {
  const toast = useToast();
  return useCallback(
    (text: string) => {
      void copyText(text).then((ok) =>
        toast.show(ok ? t.messageActions.copied : t.messageActions.copyFailed, {
          kind: ok ? "success" : "error",
        })
      );
    },
    [toast]
  );
}

const ROUTE_LABEL: Record<Route, string> = t.routeLabel;

async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}

type ArtifactDescriptor = {
  title: string;
  tag: string;
  tone?: "cyan" | "green" | "red" | "violet" | "dim";
  copyText?: string;
  expandText?: string;
  body: React.ReactNode;
};

export type ArtifactUnit = {
  event: TurnEvent;
  relatedAction?: TurnEvent;
};

export type TimelineStep = {
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

// Screenshots are framed in the DS browser-chrome window (traffic-light
// dots, near-black canvas) — the one sanctioned "image" treatment.
function screenshotBody(source: string, url?: string) {
  return <BrowserFrame src={source} url={url} />;
}

function relatedActionForEvent(events: TurnEvent[], eventId: number) {
  return [...events].reverse().find((candidate) => candidate.type === "action" && candidate.id < eventId);
}

export function collectArtifactUnits(events: TurnEvent[]) {
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

export function collectTimelineSteps(events: TurnEvent[]) {
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
        body: <Diff text={event.content} />,
      };
    }
    return {
      title: relatedAction?.tool === "run_command" ? "Kommandoutdata" : relatedAction?.tool ?? (isCommand ? "Kommandoutdata" : "Resultat"),
      tag: isCommand ? "term" : "text",
      tone: isCommand ? "green" : "dim",
      copyText: event.content,
      expandText: event.content,
      body: isCommand ? <Terminal text={event.content} /> : <div className="ds-art__prose">{event.content}</div>,
    };
  }

  if (event.type === "error" && event.content) {
    return {
      title: relatedAction?.tool ? `${relatedAction.tool} · fel` : "Fel",
      tag: "error",
      tone: "red",
      copyText: event.content,
      expandText: event.content,
      body: <Terminal text={event.content} error />,
    };
  }

  return null;
}

// Exported so the Inspector's artifact tab can render the same cards.
export function TurnArtifactCard({
  event,
  relatedAction,
}: {
  event: TurnEvent;
  relatedAction?: TurnEvent;
}) {
  const artifact = artifactDetails(event, relatedAction);
  const [expanded, setExpanded] = useState(false);

  if (!artifact) return null;

  const expandable = Boolean(artifact.expandText) || event.type === "screenshot";

  return (
    <>
      <DsArtifactCard
        title={artifact.title}
        tag={artifact.tag}
        tone={artifact.tone}
        onCopy={artifact.copyText ? () => copyText(artifact.copyText ?? "") : undefined}
        onExpand={expandable ? () => setExpanded(true) : undefined}
      >
        {artifact.body}
      </DsArtifactCard>
      {expanded && (
        <Dialog icon="▣" title={artifact.title} className="narrow artifact-modal" onClose={() => setExpanded(false)}>
          <div className="mb">
            {event.type === "screenshot" && event.image ? (
              screenshotBody(`data:image/png;base64,${event.image}`)
            ) : artifact.expandText ? (
              /^(@@|diff --git|\+[^+]|-[^-])/m.test(artifact.expandText) ? (
                <Diff text={artifact.expandText} />
              ) : (
                <Terminal text={artifact.expandText} error={event.type === "error"} />
              )
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
        <button onClick={() => copy(text)} aria-label={t.messageActions.copyPrompt}>⎘</button>
        <button onClick={() => onEdit(text)} aria-label={t.messageActions.edit}>✎</button>
        <button onClick={() => onResend(text)} aria-label={t.messageActions.resend}>↻</button>
      </div>
    </div>
  );
}

// Surfaces the routing/model rationale the backend already sends (turn_start +
// routing_decision) as a compact, collapsed-by-default line. Additive only — it
// stays out of the way until the user asks "why this route?".
function RouteInsight({ insight }: { insight?: RouteInsightData }) {
  const [open, setOpen] = useState(false);
  if (!insight) return null;

  const rows: { label: string; value: string }[] = [];
  if (insight.executionEngine) rows.push({ label: t.routeInsight.engine, value: insight.executionEngine });
  if (insight.agentRole) rows.push({ label: t.routeInsight.role, value: insight.agentRole });
  if (insight.agentRoleModel) rows.push({ label: t.routeInsight.model, value: insight.agentRoleModel });
  if (insight.reason) rows.push({ label: t.routeInsight.reason, value: insight.reason });
  if (insight.agentRoleFallback) rows.push({ label: t.routeInsight.fallback, value: insight.agentRoleFallback });
  if (insight.requiredPermissions?.length) {
    rows.push({ label: t.routeInsight.permissions, value: insight.requiredPermissions.join(" · ") });
  }
  if (!rows.length) return null;

  return (
    <div className={`whyroute${open ? " open" : ""}`}>
      <button className="whyroute-toggle" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span className="chev">›</span>
        <span>{t.routeInsight.toggle}</span>
      </button>
      {open && (
        <dl className="whyroute-body">
          {rows.map((row) => (
            <div key={row.label} className="whyroute-row">
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
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
        <RouteInsight insight={item.insight} />
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
              <TurnArtifactCard
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
            <button onClick={() => copy(item.text)} aria-label={t.messageActions.copyAnswer}>⎘ {t.messageActions.copy}</button>
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
