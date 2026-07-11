"use client";

import { useMemo, useState } from "react";
import type { Agent, Job, ModelOption, Project, TranscriptItem } from "@/app/page";
import { collectArtifactUnits, collectTimelineSteps, TurnArtifactCard, type TimelineStep } from "@/components/ActionLog";
import { Inspector, InspectorSection, Stat } from "@/components/ui";
import { t } from "@/app/strings";

interface Props {
  transcript: TranscriptItem[];
  jobs: Job[];
  selectedProject: Project | null;
  agent: Agent;
  modelMode: string;
  models: ModelOption[];
  routeMode: string;
  statusLabel: string;
  onClose: () => void;
}

type TabId = "orchestration" | "artifacts" | "session";

function stepDotClass(accent: TimelineStep["accent"]) {
  if (accent === "cyan") return "dot cyan";
  if (accent === "green") return "dot green";
  if (accent === "red") return "dot red";
  if (accent === "violet") return "dot violet";
  return "dot";
}

/**
 * The app's Inspector — a tabbed technical view of the current session
 * (DS "Kommandobrygga" panel stack adapted to the data the app actually
 * has): orchestration steps and artifacts from the latest turn, plus
 * session facts and jobs.
 */
export default function InspectorPanel({ transcript, jobs, selectedProject, agent, modelMode, models, routeMode, statusLabel, onClose }: Props) {
  const [tab, setTab] = useState<TabId>("orchestration");

  const lastAssistant = useMemo(
    () => [...transcript].reverse().find((item): item is Extract<TranscriptItem, { kind: "assistant" }> => item.kind === "assistant"),
    [transcript]
  );
  const steps = lastAssistant ? collectTimelineSteps(lastAssistant.events) : [];
  const artifacts = lastAssistant ? collectArtifactUnits(lastAssistant.events) : [];
  const turns = transcript.filter((item) => item.kind === "user").length;
  const modelLabel = modelMode === "auto" ? "Auto" : models.find((m) => m.id === modelMode)?.label ?? modelMode;
  const agentLabel = t.agents.find((a) => a.id === agent)?.label ?? agent;

  return (
    <Inspector
      title={t.inspector.title}
      onClose={onClose}
      tabs={[
        { value: "orchestration", label: t.inspector.tabs.orchestration },
        { value: "artifacts", label: t.inspector.tabs.artifacts },
        { value: "session", label: t.inspector.tabs.session },
      ]}
      activeTab={tab}
      onTab={(v) => setTab(v as TabId)}
    >
      {tab === "orchestration" && (
        <InspectorSection label={t.inspector.lastTurn}>
          {steps.length === 0 ? (
            <p className="insp-empty">{t.inspector.emptyOrchestration}</p>
          ) : (
            steps.map((step, index) => (
              <div key={`${step.kind}-${step.id}-${index}`} className="istep done">
                <div className="g">
                  <div className={stepDotClass(step.accent)}>✓</div>
                  <div className="ln" />
                </div>
                <div className="t">
                  <b>{step.title}</b>
                  {step.detail ? <span className="meta"> · {step.detail}</span> : null}
                </div>
              </div>
            ))
          )}
        </InspectorSection>
      )}

      {tab === "artifacts" && (
        <InspectorSection label={t.inspector.lastTurn}>
          {artifacts.length === 0 ? (
            <p className="insp-empty">{t.inspector.emptyArtifacts}</p>
          ) : (
            artifacts.map(({ event, relatedAction }) => (
              <TurnArtifactCard key={`insp-${event.id}`} event={event} relatedAction={relatedAction} />
            ))
          )}
        </InspectorSection>
      )}

      {tab === "session" && (
        <>
          <InspectorSection label={t.inspector.sessionFacts}>
            <div className="insp-stats">
              <Stat label={t.inspector.status} value={statusLabel} />
              <Stat label={t.inspector.turns} value={turns} />
              <Stat label={t.inspector.project} value={selectedProject?.name ?? t.inspector.noProject} />
              <Stat label={t.inspector.model} value={modelLabel} />
              <Stat label={t.inspector.route} value={routeMode === "auto" ? "Auto" : routeMode} />
              <Stat label={t.inspector.agent} value={agentLabel} />
            </div>
          </InspectorSection>
          <InspectorSection label={t.inspector.jobsLabel} last>
            {jobs.length === 0 ? (
              <p className="insp-empty">{t.jobs.none}</p>
            ) : (
              jobs.map((job) => (
                <div key={job.id} className="insp-job" style={{ opacity: job.enabled ? 1 : 0.55 }}>
                  <span className="insp-job__glyph">{job.kind === "task" ? "✦" : "⏰"}</span>
                  <span className="insp-job__title">{job.title}</span>
                  <span className="insp-job__next">{job.next_run_label}</span>
                </div>
              ))
            )}
          </InspectorSection>
        </>
      )}
    </Inspector>
  );
}
