"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useDialogA11y } from "@/components/Dialog";
import { cn } from "./cn";

export interface PaletteItem {
  icon?: React.ReactNode;
  label: string;
  /** Mono hint at the right edge, e.g. "⌘K" or "aktiv". */
  hint?: React.ReactNode;
  /** Extra strings the query should match besides the label. */
  keywords?: string[];
  onSelect: () => void;
}

export interface PaletteGroup {
  label: string;
  items: PaletteItem[];
}

export interface CommandPaletteProps {
  groups: PaletteGroup[];
  onClose: () => void;
  placeholder?: string;
  /** Message when the query matches nothing. */
  emptyText?: string;
}

/**
 * CommandPalette — the ⌘K palette. Blurred scrim + centered card with a
 * search field and grouped commands. Keyboard-first: type to filter,
 * ↑/↓ to move, Enter to run, Escape to close. Participates in the shared
 * dialog stack (useDialogA11y), so Escape/Tab behave correctly when the
 * palette opens on top of another overlay, and focus is restored to the
 * trigger on close. The list is wired as a combobox/listbox for AT.
 */
export function CommandPalette({ groups, onClose, placeholder = "Sök kommando…", emptyText = "Inga träffar." }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  // Only scroll the active row into view for keyboard navigation — hover
  // must never scroll the list out from under the cursor.
  const keyboardNav = useRef(false);
  const idBase = useId();
  // Escape close, Tab trap, initial focus (the input is the first focusable
  // element) and focus restore all come from the shared dialog hook.
  const dialogRef = useDialogA11y(onClose);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return groups;
    return groups
      .map((g) => ({
        ...g,
        items: g.items.filter((it) =>
          [it.label, ...(it.keywords ?? [])].some((s) => s.toLowerCase().includes(q))
        ),
      }))
      .filter((g) => g.items.length > 0);
  }, [groups, query]);

  const flat = useMemo(() => filtered.flatMap((g) => g.items), [filtered]);
  const activeIdx = Math.min(active, Math.max(0, flat.length - 1));
  const activeId = flat.length > 0 ? `${idBase}-opt-${activeIdx}` : undefined;

  // The cursor resets when the query changes — done in the change handler
  // rather than an effect so typing never triggers a cascading render.
  const onQueryChange = (value: string) => {
    setQuery(value);
    setActive(0);
  };

  useEffect(() => {
    if (!keyboardNav.current) return;
    keyboardNav.current = false;
    listRef.current
      ?.querySelectorAll(".ds-palette__item")
      [activeIdx]?.scrollIntoView({ block: "nearest" });
  }, [activeIdx]);

  const run = (item: PaletteItem | undefined) => {
    if (!item) return;
    onClose();
    item.onSelect();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        keyboardNav.current = true;
        setActive((i) => Math.min(i + 1, flat.length - 1));
        break;
      case "ArrowUp":
        e.preventDefault();
        keyboardNav.current = true;
        setActive((i) => Math.max(i - 1, 0));
        break;
      case "Enter":
        e.preventDefault();
        run(flat[activeIdx]);
        break;
    }
  };

  return (
    <div className="ds-palette-scrim" onClick={onClose}>
      <div
        ref={dialogRef}
        className="ds-palette"
        role="dialog"
        aria-modal="true"
        aria-label="Kommandopalett"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="ds-palette__head">
          <span className="ds-palette__glyph" aria-hidden="true">⌕</span>
          <input
            className="ds-palette__input"
            role="combobox"
            aria-expanded="true"
            aria-controls={`${idBase}-list`}
            aria-activedescendant={activeId}
            aria-autocomplete="list"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder={placeholder}
            aria-label={placeholder}
          />
          <span className="ds-palette__esc" aria-hidden="true">esc</span>
        </div>
        <div className="ds-palette__list" id={`${idBase}-list`} role="listbox" ref={listRef}>
          {flat.length === 0 && <div className="ds-palette__empty">{emptyText}</div>}
          {filtered.map((g) => (
            <div key={g.label} role="group" aria-label={g.label}>
              <div className="ds-palette__group" aria-hidden="true">{g.label}</div>
              {g.items.map((it) => {
                const idx = flat.indexOf(it);
                return (
                  <button
                    key={`${g.label}-${it.label}`}
                    type="button"
                    id={`${idBase}-opt-${idx}`}
                    role="option"
                    aria-selected={idx === activeIdx}
                    className={cn("ds-palette__item", idx === activeIdx && "is-active")}
                    onMouseEnter={() => setActive(idx)}
                    onFocus={() => setActive(idx)}
                    onClick={() => run(it)}
                  >
                    <span className="ds-palette__icon" aria-hidden="true">{it.icon}</span>
                    <span>{it.label}</span>
                    {it.hint && <span className="ds-palette__hint">{it.hint}</span>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
