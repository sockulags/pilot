"use client";

import { useEffect, useId, useRef } from "react";

const FOCUSABLE =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

function focusableWithin(node: HTMLElement | null): HTMLElement[] {
  if (!node) return [];
  return Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.hasAttribute("disabled") && el.offsetParent !== null
  );
}

/**
 * Wires a container as an accessible dialog: Escape closes it, Tab is trapped
 * inside, focus moves in on open and is restored to the trigger on close.
 * Returns a ref to attach to the dialog container.
 */
export function useDialogA11y(onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const node = ref.current;
    const items = focusableWithin(node);
    (items[0] ?? node)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key === "Tab") {
        const current = focusableWithin(node);
        if (current.length === 0) {
          e.preventDefault();
          return;
        }
        const idx = current.indexOf(document.activeElement as HTMLElement);
        if (e.shiftKey && idx <= 0) {
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
      previouslyFocused?.focus?.();
    };
  }, [onClose]);

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
          <button className="x" onClick={onClose} aria-label="Stäng">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
