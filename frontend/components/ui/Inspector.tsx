"use client";

import { useDialogA11y } from "@/components/Dialog";
import { Tabs, type Tab } from "./Tabs";
import { cn } from "./cn";

export interface InspectorProps {
  /** @default "Inspector" */
  title?: React.ReactNode;
  /** @default "⊟" */
  glyph?: React.ReactNode;
  onClose?: () => void;
  tabs?: Tab[];
  activeTab?: string;
  onTab?: (value: string) => void;
  /** Layout-participating variant for showcases/docs (no overlay). */
  inline?: boolean;
  children: React.ReactNode;
}

/**
 * Inspector — the slide-in right panel holding the technical view of the
 * current session (orchestration steps, artifacts, session facts). Fixed
 * overlay by default (scrim + Escape + focus trap via the shared dialog
 * hook); `inline` renders it as a normal flex child for docs.
 */
export function Inspector({ title = "Inspector", glyph = "⊟", onClose, tabs, activeTab, onTab, inline = false, children }: InspectorProps) {
  const head = (
    <div className={cn("ds-inspector__head", !tabs && "ds-inspector__head--line")}>
      <span className="ds-inspector__glyph" aria-hidden="true">{glyph}</span>
      <span className="ds-inspector__title">{title}</span>
      {onClose && (
        <button type="button" className="ds-inspector__close" onClick={onClose} aria-label="Stäng">
          ✕
        </button>
      )}
    </div>
  );
  const body = (
    <>
      {head}
      {tabs && activeTab !== undefined && (
        <div className="ds-inspector__tabs">
          <Tabs tabs={tabs} value={activeTab} onChange={onTab} aria-label={typeof title === "string" ? title : "Inspector"} />
        </div>
      )}
      <div className="ds-inspector__body">{children}</div>
    </>
  );

  if (inline) {
    return <aside className="ds-inspector ds-inspector--inline">{body}</aside>;
  }
  return <InspectorOverlay onClose={onClose}>{body}</InspectorOverlay>;
}

function InspectorOverlay({ onClose, children }: { onClose?: () => void; children: React.ReactNode }) {
  const ref = useDialogA11y(onClose ?? (() => {}));
  return (
    <>
      <div className="ds-inspector-scrim" onClick={onClose} />
      <aside
        ref={ref}
        className="ds-inspector is-open"
        role="dialog"
        aria-modal="true"
        aria-label="Inspector"
        tabIndex={-1}
      >
        {children}
      </aside>
    </>
  );
}

/** A labelled section inside the Inspector. `action` renders at the right
 *  of the label (e.g. a ＋ to add a job). `last` pins it to the bottom. */
export function InspectorSection({
  label,
  action,
  onAction,
  last = false,
  children,
}: {
  label?: React.ReactNode;
  action?: React.ReactNode;
  onAction?: () => void;
  last?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("ds-inspector-section", last && "ds-inspector-section--last")}>
      {label && (
        <div className="ds-inspector-section__head">
          <span>{label}</span>
          {action && (
            <button type="button" className="ds-inspector-section__action" onClick={onAction}>
              {action}
            </button>
          )}
        </div>
      )}
      {children}
    </div>
  );
}
