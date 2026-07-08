// Pilot Design System — component library barrel.
// Import from "@/components/ui" for every DS primitive.
//
// Foundations (tokens/colors/type/space/motion) live in styles/ds/*.css,
// imported once via styles/ds.css from app/globals.css.

export { cn } from "./cn";
export type { ClassValue } from "./cn";

// Core
export { Button } from "./Button";
export type { ButtonProps, ButtonVariant, ButtonSize } from "./Button";
export { IconButton } from "./IconButton";
export type { IconButtonProps } from "./IconButton";
export { Badge } from "./Badge";
export type { BadgeProps, Tone } from "./Badge";
export { Pill } from "./Pill";
export type { PillProps, OrbTone } from "./Pill";
export { Chip } from "./Chip";
export type { ChipProps } from "./Chip";
export { SegControl } from "./SegControl";
export type { SegControlProps, SegOption } from "./SegControl";

// Forms
export { Field } from "./Field";
export type { FieldProps } from "./Field";
export { Select } from "./Select";
export type { SelectProps, SelectOption } from "./Select";
export { Switch } from "./Switch";
export type { SwitchProps } from "./Switch";

// Feedback
export { ToolChip } from "./ToolChip";
export type { ToolChipProps } from "./ToolChip";
export { Tooltip } from "./Tooltip";
export type { TooltipProps } from "./Tooltip";
export { Spinner } from "./Spinner";
export type { SpinnerProps } from "./Spinner";

// Navigation
export { Tabs } from "./Tabs";
export type { TabsProps, Tab } from "./Tabs";

// Overlay
export { Modal } from "./Modal";
export type { ModalProps } from "./Modal";

// Brand + layout helpers (DS-derived, own components)
export { Logomark } from "./Logomark";
export type { LogomarkProps } from "./Logomark";
export { Kbd, SectionLabel, Stat, Card, CardHead, EmptyState } from "./Primitives";
