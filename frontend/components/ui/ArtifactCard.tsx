"use client";

import { useState } from "react";
import { cn } from "./cn";

export type ArtifactTone = "cyan" | "green" | "red" | "violet" | "dim";

export interface ArtifactCardProps {
  /** Card title, e.g. "run_command · skärmbild". */
  title: React.ReactNode;
  /** Mono tone tag, e.g. "term", "diff", "live". @default "text" */
  tag?: React.ReactNode;
  /** @default "dim" */
  tone?: ArtifactTone;
  children: React.ReactNode;
  /** Copy handler — shows the ⎘ action and flips to "kopierat ✓". */
  onCopy?: () => void | Promise<unknown>;
  /** Expand handler — shows the ⤢ action. */
  onExpand?: () => void;
  /** Remove body padding (image/browser-frame bodies). @default false */
  flush?: boolean;
  className?: string;
}

/**
 * Artifact card — the framed output of a tool call (terminal, diff, text,
 * screenshot). Mono header with a tone tag + copy/expand actions, a body
 * region with its own scroll. Rises in on mount.
 */
export function ArtifactCard({ title, tag = "text", tone = "dim", children, onCopy, onExpand, flush = false, className }: ArtifactCardProps) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await onCopy?.();
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  return (
    <div className={cn("ds-art", className)}>
      <div className="ds-art__head">
        <span aria-hidden="true">▣</span>
        <span className="ds-art__title">{title}</span>
        <span className={cn("ds-art__tag", tone !== "dim" && `is-${tone}`)}>{tag}</span>
        <div className="ds-art__acts">
          {onCopy && (
            <button type="button" className="ds-art__act" onClick={copy}>
              {copied ? "kopierat ✓" : "⎘ kopiera"}
            </button>
          )}
          {onExpand && (
            <button type="button" className="ds-art__act" onClick={onExpand}>
              ⤢ expandera
            </button>
          )}
        </div>
      </div>
      <div className={cn("ds-art__body", flush && "ds-art__body--flush")}>{children}</div>
    </div>
  );
}

/** Terminal-style body for command output. Lines starting with `$` render
 *  in cyan, lines containing "ok" in green (matching the shipped look). */
export function Terminal({ text, error = false }: { text: string; error?: boolean }) {
  return (
    <div className={cn("ds-term", error && "is-error")}>
      {text.split(/\r?\n/).map((line, i) => (
        <div
          key={i}
          className={
            error ? undefined : line.trim().startsWith("$") ? "ds-term__cmd" : line.toLowerCase().includes("ok") ? "ds-term__ok" : undefined
          }
        >
          {line || " "}
        </div>
      ))}
    </div>
  );
}

/** Unified-diff body. Pass raw patch text. */
export function Diff({ text }: { text: string }) {
  return (
    <div className="ds-diff">
      {text.split(/\r?\n/).map((line, i) => {
        const kind = line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "ctx";
        return (
          <div key={i} className={`ds-diff__ln is-${kind}`}>
            {line || " "}
          </div>
        );
      })}
    </div>
  );
}

/**
 * Browser-chrome frame for the screenshots the agent captures — traffic-light
 * dots + near-black window, per the DS imagery guideline ("the only images
 * in-product are user screenshots, framed in a browser-chrome artifact card").
 */
export function BrowserFrame({ src, alt = "skärmbild", url, children }: { src?: string; alt?: string; url?: string; children?: React.ReactNode }) {
  return (
    <div className="ds-browser">
      <div className="ds-browser__bar" aria-hidden="true">
        <i className="ds-browser__dot" />
        <i className="ds-browser__dot" />
        <i className="ds-browser__dot" />
        {url && <span className="ds-browser__url">{url}</span>}
      </div>
      {/* Screenshots arrive as runtime base64 data URIs over the WS — next/image
          adds no optimization for those and the app is a static export. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <div className="ds-browser__win">{src ? <img src={src} alt={alt} /> : children}</div>
    </div>
  );
}
