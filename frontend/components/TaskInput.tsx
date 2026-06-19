"use client";

import { useEffect, useRef, useState, KeyboardEvent } from "react";
import { t } from "@/app/strings";

interface Props {
  onSend: (text: string) => void;
  onAbort?: () => void;
  onOpenContext?: () => void;
  disabled: boolean;
  running?: boolean;
  placeholder?: string;
  initialValue?: string;
}

function autosize(el: HTMLTextAreaElement | null) {
  if (!el) return;
  el.style.height = "0px";
  el.style.height = `${Math.min(el.scrollHeight, 150)}px`;
}

export default function ChatInput({
  onSend,
  onAbort,
  onOpenContext,
  disabled,
  running = false,
  placeholder = t.composer.placeholder,
  initialValue = "",
}: Props) {
  const [value, setValue] = useState(initialValue);
  const ref = useRef<HTMLTextAreaElement>(null);
  const focusTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    autosize(ref.current);
  }, [value]);

  // When seeded with an edited prompt (component is re-keyed), focus and place
  // the caret at the end so the user can tweak and resend.
  useEffect(() => {
    if (!initialValue) return;
    const el = ref.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }, [initialValue]);

  useEffect(() => () => {
    if (focusTimer.current) clearTimeout(focusTimer.current);
  }, []);

  const submit = () => {
    const text = value.trim();
    if (!text || disabled || running) return;
    onSend(text);
    setValue("");
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter (or Cmd/Ctrl+Enter) sends; Shift+Enter inserts a newline.
    if (e.key === "Enter" && (!e.shiftKey || e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      submit();
    }
  };

  // On mobile the keyboard can briefly overlap the input before the layout
  // settles; nudge the composer back into view once it opens. Replaces any
  // pending nudge so rapid refocus doesn't stack scrolls.
  const onFocus = () => {
    if (focusTimer.current) clearTimeout(focusTimer.current);
    focusTimer.current = setTimeout(() => ref.current?.scrollIntoView({ block: "nearest" }), 300);
  };

  return (
    <div className="composer">
      <div className="box">
        <div className="l1">
          <textarea
            ref={ref}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKey}
            onFocus={onFocus}
            disabled={disabled || running}
            placeholder={placeholder}
            rows={1}
          />
          <button
            type="button"
            className={`send${running ? " stop" : ""}`}
            disabled={!running && (disabled || !value.trim())}
            onClick={running ? onAbort : submit}
            title={running ? t.composer.abort : t.composer.send}
          >
            {running ? "■" : "➜"}
          </button>
        </div>
        <div className="l2">
          <button type="button" className="ctxm" onClick={onOpenContext}>
            <span className="ring" />
            <span className="lbl">{t.composer.context}</span>
          </button>
          <span className="sp2" />
          <span className="hint">
            {running ? t.composer.working : disabled ? t.composer.waiting : t.composer.enterHint}
          </span>
        </div>
      </div>
    </div>
  );
}
