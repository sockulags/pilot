"use client";

import { useEffect, useId, useRef } from "react";
import { t } from "@/app/strings";

const FOCUSABLE =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

function focusableWithin(node: HTMLElement | null): HTMLElement[] {
  if (!node) return [];
  return Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.hasAttribute("disabled") && el.offsetParent !== null
  );
}

// Tracks open dialogs so only the topmost handles Escape / Tab trapping when
// overlays are stacked (e.g. drawer open + Cmd-K controls).
const dialogStack: symbol[] = [];

/**
 * Wires a container as an accessible dialog: Escape closes it, Tab is trapped
 * inside, focus moves in on open and is restored to the trigger on close.
 * Returns a ref to attach to the dialog container.
 */
export function useDialogA11y(onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);

  // Keep the latest onClose in a ref so the mount effect can depend on [] and
  // run exactly once per overlay open. Depending on [onClose] re-ran the whole
  // trap on every parent re-render (each streamed token), yanking keyboard
  // focus back into the dialog mid-typing (review 2026-07-04).
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const token = Symbol("dialog");
    dialogStack.push(token);
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const node = ref.current;
    const items = focusableWithin(node);
    (items[0] ?? node)?.focus();

    const isTopmost = () => dialogStack[dialogStack.length - 1] === token;

    const onKey = (e: KeyboardEvent) => {
      if (!isTopmost()) return; // only the front overlay reacts
      if (e.key === "Escape") {
        e.preventDefault();
        onCloseRef.current();
        return;
      }
      if (e.key === "Tab") {
        const current = focusableWithin(node);
        if (current.length === 0) {
          e.preventDefault();
          return;
        }
        const idx = current.indexOf(document.activeElement as HTMLElement);
        if (idx === -1) {
          // Focus escaped the dialog — pull it back in.
          e.preventDefault();
          (e.shiftKey ? current[current.length - 1] : current[0]).focus();
        } else if (e.shiftKey && idx === 0) {
          e.preventDefault();
          current[current.length - 1].focus();
        } else if (!e.shiftKey && idx === current.length - 1) {
          e.preventDefault();
          current[0].focus();
        }
      }
    };

    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      const at = dialogStack.indexOf(token);
      if (at !== -1) dialogStack.splice(at, 1);
      previouslyFocused?.focus?.();
    };
    // Runs once per overlay open — onClose is read through onCloseRef.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return ref;
}

export default function Dialog({
  icon,
  title,
  onClose,
  className = "",
  children,
}: {
  icon?: React.ReactNode;
  title: string;
  onClose: () => void;
  className?: string;
  children: React.ReactNode;
}) {
  const ref = useDialogA11y(onClose);
  const titleId = useId();

  return (
    <div className="scrim on" onClick={onClose}>
      <div
        ref={ref}
        className={`modal ${className}`.trim()}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mh">
          {icon && <span aria-hidden="true">{icon}</span>}
          <span className="nm" id={titleId}>
            {title}
          </span>
          <button className="x" onClick={onClose} aria-label={t.common.close}>
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
