"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

type ToastKind = "info" | "success" | "error";

type ToastAction = { label: string; onClick: () => void };

type Toast = {
  id: number;
  kind: ToastKind;
  message: string;
  action?: ToastAction;
};

type ShowOptions = {
  kind?: ToastKind;
  action?: ToastAction;
  duration?: number; // ms; 0 keeps it until dismissed
};

type ToastApi = { show: (message: string, opts?: ShowOptions) => void };

const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within a ToastProvider");
  return ctx;
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const idRef = useRef(0);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const remove = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const show = useCallback(
    (message: string, opts?: ShowOptions) => {
      const id = idRef.current++;
      const toast: Toast = { id, kind: opts?.kind ?? "info", message, action: opts?.action };
      setToasts((prev) => [...prev, toast]);
      // Give actionable toasts (e.g. undo) longer to be noticed.
      const duration = opts?.duration ?? (opts?.action ? 6000 : 3500);
      if (duration > 0) timers.current.set(id, setTimeout(() => remove(id), duration));
    },
    [remove]
  );

  useEffect(() => {
    const map = timers.current;
    return () => map.forEach((timer) => clearTimeout(timer));
  }, []);

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <div className="toasts" role="region" aria-label="Notiser">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`} role="status">
            <span className="toast-msg">{t.message}</span>
            {t.action && (
              <button
                type="button"
                className="toast-action"
                onClick={() => {
                  t.action!.onClick();
                  remove(t.id);
                }}
              >
                {t.action.label}
              </button>
            )}
            <button type="button" className="toast-x" aria-label="Stäng" onClick={() => remove(t.id)}>
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
