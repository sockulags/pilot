"use client";

interface Props {
  onAbort: () => void;
}

export default function AbortButton({ onAbort }: Props) {
  return (
    <button
      onClick={onAbort}
      style={{
        padding: "0.5rem 1rem",
        background: "transparent",
        border: "1px solid var(--red)",
        color: "var(--red)",
        borderRadius: 8,
        cursor: "pointer",
        fontWeight: 600,
        fontSize: "0.85rem",
        alignSelf: "flex-start",
      }}
    >
      Avbryt
    </button>
  );
}
