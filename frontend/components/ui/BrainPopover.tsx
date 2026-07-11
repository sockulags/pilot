"use client";

import { useRef } from "react";
import { SegControl, type SegOption } from "./SegControl";
import { cn } from "./cn";

export interface BrainModel {
  id: string;
  label: React.ReactNode;
  /** Orb tint. @default "grad" */
  orb?: "grad" | "violet" | "cyan" | "green" | "amber";
  /** Mono tag at the right, e.g. "kod". */
  tag?: React.ReactNode;
}

export interface BrainPopoverProps {
  mode: string;
  modes: SegOption[];
  onMode?: (value: string) => void;
  model: string;
  models: BrainModel[];
  onModel?: (id: string) => void;
  agent: string;
  agents: SegOption[];
  onAgent?: (value: string) => void;
  /** Hint under the model label. */
  modelHint?: React.ReactNode;
  className?: string;
}

/**
 * BrainPopover — the grouped orchestration popover: Läge (segmented) +
 * Modell (radio list with orbs) + Agent (segmented). Anchor it under the
 * brain pill; the caller owns open state and dismissal (outside click +
 * Escape). The model list is a real roving-tabindex radiogroup, matching
 * the SegControl contract.
 */
export function BrainPopover({ mode, modes, onMode, model, models, onModel, agent, agents, onAgent, modelHint, className }: BrainPopoverProps) {
  const radioRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const hasChecked = models.some((m) => m.id === model);
  const rovingIdx = hasChecked ? models.findIndex((m) => m.id === model) : 0;

  const selectAt = (i: number) => {
    const n = models.length;
    if (n === 0) return;
    const idx = ((i % n) + n) % n;
    onModel?.(models[idx].id);
    radioRefs.current[idx]?.focus();
  };

  const onModelKeyDown = (e: React.KeyboardEvent) => {
    switch (e.key) {
      case "ArrowDown":
      case "ArrowRight":
        e.preventDefault();
        selectAt(rovingIdx + 1);
        break;
      case "ArrowUp":
      case "ArrowLeft":
        e.preventDefault();
        selectAt(rovingIdx - 1);
        break;
      case "Home":
        e.preventDefault();
        selectAt(0);
        break;
      case "End":
        e.preventDefault();
        selectAt(models.length - 1);
        break;
    }
  };

  return (
    <div className={cn("ds-brainpop", className)} role="group" aria-label="Orkestrering">
      <div className="ds-brainpop__head">Orkestrering</div>

      <div className="ds-brainpop__sect">
        <div className="ds-brainpop__label">Läge</div>
        <SegControl options={modes} value={mode} onChange={onMode} aria-label="Läge" />
      </div>

      <div className="ds-brainpop__sect" role="radiogroup" aria-label="Modell" onKeyDown={onModelKeyDown}>
        <div className="ds-brainpop__label">{modelHint ?? "Modell"}</div>
        {models.map((m, i) => {
          const sel = m.id === model;
          return (
            <button
              key={m.id}
              ref={(el) => {
                radioRefs.current[i] = el;
              }}
              type="button"
              role="radio"
              aria-checked={sel}
              tabIndex={i === rovingIdx ? 0 : -1}
              className={cn("ds-brainpop__opt", sel && "is-active")}
              onClick={() => onModel?.(m.id)}
            >
              <span className={cn("ds-brainpop__orb", m.orb && m.orb !== "grad" && `is-${m.orb}`)} aria-hidden="true" />
              <span>{m.label}</span>
              {m.tag && <span className="ds-brainpop__tag">{m.tag}</span>}
              {sel && <span className="ds-brainpop__check" aria-hidden="true">✓</span>}
            </button>
          );
        })}
      </div>

      <div className="ds-brainpop__sect ds-brainpop__sect--row">
        <div className="ds-brainpop__label">Agent</div>
        <SegControl options={agents} value={agent} onChange={onAgent} aria-label="Agent" />
      </div>
    </div>
  );
}
