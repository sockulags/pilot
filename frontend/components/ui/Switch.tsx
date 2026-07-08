"use client";

import { useId } from "react";
import { cn } from "./cn";

export interface SwitchProps {
  checked?: boolean;
  onChange?: (checked: boolean) => void;
  disabled?: boolean;
  /** Optional label rendered to the right (whole row toggles). */
  label?: React.ReactNode;
  id?: string;
  className?: string;
  "aria-label"?: string;
}

/**
 * Switch — track + knob toggle with a gradient track when on. Job
 * enable/disable, settings, feature flags. Built on a real role="switch"
 * button so keyboard + screen readers work.
 */
export function Switch({ checked = false, onChange, disabled, label, id, className, ...aria }: SwitchProps) {
  const auto = useId();
  const switchId = id ?? auto;
  const toggle = () => !disabled && onChange?.(!checked);

  const control = (
    <button
      type="button"
      role="switch"
      id={switchId}
      aria-checked={checked}
      disabled={disabled}
      className={cn("ds-switch", checked && "is-on")}
      onClick={toggle}
      {...(label ? {} : aria)}
    >
      <span className="ds-switch__knob" aria-hidden="true" />
    </button>
  );

  if (!label) return <span className={className}>{control}</span>;

  return (
    <label htmlFor={switchId} className={cn("ds-switch-wrap", disabled && "is-disabled", className)}>
      {control}
      <span className="ds-switch-label">{label}</span>
    </label>
  );
}
