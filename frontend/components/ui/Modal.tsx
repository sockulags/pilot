"use client";

import Dialog from "@/components/Dialog";
import { cn } from "./cn";

export interface ModalProps {
  /** Header glyph. @default "⌘" */
  glyph?: React.ReactNode;
  title: string;
  children: React.ReactNode;
  onClose?: () => void;
  /** @default "wide" (920px) — "narrow" is 560px. */
  width?: "wide" | "narrow";
  className?: string;
}

/**
 * Pilot modal — a blurred-scrim sheet with a mono header and scrolling
 * body. Thin wrapper over the app's accessible Dialog (focus trap, Escape,
 * focus restore, stacked-overlay handling) so the DS and product share one
 * modal implementation.
 */
export function Modal({ glyph = "⌘", title, children, onClose, width = "wide", className }: ModalProps) {
  return (
    <Dialog icon={glyph} title={title} onClose={onClose ?? (() => {})} className={cn(width === "narrow" && "narrow", className)}>
      {children}
    </Dialog>
  );
}
