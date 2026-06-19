"use client";

import { useEffect, useRef, useState, KeyboardEvent } from "react";

interface Props {
  onSend: (text: string) => void;
  onAbort?: () => void;
  onOpenContext?: () => void;
  disabled: boolean;
  running?: boolean;
  placeholder?: string;
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
  placeholder = "Be Pilot om något, eller ge en tydlig uppgift…",
}: Props) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const focusTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    autosize(ref.current);
  }, [value]);

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
    if (e.key === "Enter" && !e.shiftKey) {
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
            title={running ? "Avbryt pågående körning" : "Skicka"}
          >
            {running ? "■" : "➜"}
          </button>
        </div>
        <div className="l2">
          <button type="button" className="mini" title="Bilagor stöds inte ännu" disabled>
            ＋
          </button>
          <button type="button" className="mini" title="Röststyrning stöds inte ännu" disabled>
            ◉
          </button>
          <button type="button" className="ctxm" onClick={onOpenContext}>
            <span className="ring" />
            <span className="lbl">Kontext</span>
          </button>
          <span className="sp2" />
          <span className="hint">
            {running ? "Pilot arbetar…" : "Enter skickar · Shift+Enter ny rad"}
          </span>
        </div>
      </div>
    </div>
  );
}
