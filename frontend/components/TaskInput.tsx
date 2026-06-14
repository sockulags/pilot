"use client";

import { useState, KeyboardEvent } from "react";

interface Props {
  onRun: (task: string) => void;
  disabled: boolean;
}

export default function TaskInput({ onRun, disabled }: Props) {
  const [value, setValue] = useState("");

  const submit = () => {
    if (!value.trim() || disabled) return;
    onRun(value.trim());
    setValue("");
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div style={{ display: "flex", gap: "0.5rem" }}>
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKey}
        disabled={disabled}
        placeholder="Beskriv vad agenten ska göra... (Enter för att köra)"
        rows={3}
        style={{
          flex: 1,
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          color: "var(--text)",
          padding: "0.625rem 0.75rem",
          fontSize: "0.95rem",
          resize: "vertical",
          outline: "none",
          fontFamily: "inherit",
        }}
      />
      <button
        onClick={submit}
        disabled={disabled || !value.trim()}
        style={{
          alignSelf: "flex-end",
          padding: "0.625rem 1.25rem",
          background: disabled || !value.trim() ? "var(--border)" : "var(--accent)",
          color: "var(--text)",
          border: "none",
          borderRadius: 8,
          cursor: disabled || !value.trim() ? "not-allowed" : "pointer",
          fontWeight: 600,
          fontSize: "0.9rem",
          whiteSpace: "nowrap",
        }}
      >
        Kör
      </button>
    </div>
  );
}
