import { Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  primary:
    "bevel-accent border border-transparent bg-primary text-primary-foreground hover:bg-primary/90 active:bg-primary/85",
  secondary:
    "bevel-neutral border border-border-strong bg-secondary text-foreground hover:bg-surface-hover active:bg-surface-active",
  ghost:
    "border border-transparent bg-transparent text-muted-foreground hover:bg-surface-hover hover:text-foreground",
  danger:
    "border border-critical/50 bg-critical/12 text-critical hover:bg-critical/20 active:bg-critical/25",
};

const SIZE_CLASS = {
  sm: "h-7 px-2.5 text-[12px] gap-1.5",
  md: "h-9 px-3.5 text-[13px] gap-2",
} as const;

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: keyof typeof SIZE_CLASS;
  icon?: React.ComponentType<{ className?: string }>;
  loading?: boolean;
}

export function Button({
  variant = "secondary",
  size = "md",
  icon: Icon,
  loading = false,
  className,
  children,
  disabled,
  type = "button",
  ...rest
}: ButtonProps): React.JSX.Element {
  return (
    <button
      {...rest}
      type={type}
      disabled={disabled || loading}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-control font-medium transition-colors duration-[var(--motion-fast)]",
        "disabled:cursor-not-allowed disabled:opacity-45",
        VARIANT_CLASS[variant],
        SIZE_CLASS[size],
        className,
      )}
    >
      {loading ? (
        <Loader2 className="size-3.5 shrink-0 animate-spin" aria-hidden="true" />
      ) : Icon ? (
        <Icon className="size-3.5 shrink-0" aria-hidden="true" />
      ) : null}
      {children}
    </button>
  );
}

/** Icon-only control. `label` becomes the accessible name and the tooltip. */
export function IconButton({
  label,
  icon: Icon,
  variant = "ghost",
  size = "md",
  loading = false,
  className,
  ...rest
}: Omit<ButtonProps, "children" | "icon"> & {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}): React.JSX.Element {
  const dimension = size === "sm" ? "size-7" : "size-9";
  return (
    <button
      {...rest}
      type="button"
      aria-label={label}
      title={label}
      disabled={rest.disabled || loading}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-control transition-colors duration-[var(--motion-fast)]",
        "disabled:cursor-not-allowed disabled:opacity-40",
        VARIANT_CLASS[variant],
        dimension,
        className,
      )}
    >
      {loading ? (
        <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
      ) : (
        <Icon className="size-4" aria-hidden="true" />
      )}
    </button>
  );
}
