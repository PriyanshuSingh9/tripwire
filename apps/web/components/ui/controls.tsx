"use client";

import { useId, useRef } from "react";
import { Search, X } from "lucide-react";

import { cn } from "@/lib/utils";

export function SearchInput({
  value,
  onChange,
  label,
  placeholder = "Search",
  className,
}: {
  value: string;
  onChange: (next: string) => void;
  label: string;
  placeholder?: string;
  className?: string;
}): React.JSX.Element {
  const inputId = useId();
  return (
    <div className={cn("relative", className)}>
      <label htmlFor={inputId} className="sr-only">
        {label}
      </label>
      <Search
        className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
      <input
        id={inputId}
        type="search"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 w-full rounded-control border border-input bg-muted pr-8 pl-8 text-[13px] text-foreground transition-colors duration-[var(--motion-fast)] focus:border-[var(--border-strong)] focus:bg-card focus:outline-none"
      />
      {value.length > 0 ? (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label="Clear search"
          className="absolute top-1/2 right-2 -translate-y-1/2 rounded-sm p-0.5 text-muted-foreground transition-colors duration-[var(--motion-fast)] hover:text-foreground"
        >
          <X className="size-3.5" aria-hidden="true" />
        </button>
      ) : null}
    </div>
  );
}

export interface SelectOption {
  value: string;
  label: string;
}

export function SelectField({
  label,
  value,
  options,
  onChange,
  className,
  hideLabel = false,
}: {
  label: string;
  value: string;
  options: SelectOption[];
  onChange: (next: string) => void;
  className?: string;
  hideLabel?: boolean;
}): React.JSX.Element {
  const selectId = useId();
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <label
        htmlFor={selectId}
        className={hideLabel ? "sr-only" : "text-[12px] whitespace-nowrap text-muted-foreground"}
      >
        {label}
      </label>
      <select
        id={selectId}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 rounded-control border border-input bg-muted px-2 text-[13px] text-foreground transition-colors duration-[var(--motion-fast)] focus:border-[var(--border-strong)] focus:bg-card focus:outline-none"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  hint?: string;
}

/**
 * Segmented control for a choice that changes the same view in place.
 * The thumb slides to the chosen option; the value lands on the press, so
 * nothing waits for the animation. Arrow keys move and select.
 */
export function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
  className,
  size = "md",
}: {
  label: string;
  value: T;
  options: SegmentedOption<T>[];
  onChange: (next: T) => void;
  className?: string;
  size?: "sm" | "md";
}): React.JSX.Element {
  const groupRef = useRef<HTMLDivElement>(null);
  const activeIndex = Math.max(
    0,
    options.findIndex((option) => option.value === value),
  );
  const columns = Math.max(1, options.length);

  const move = (nextIndex: number): void => {
    const clamped = (nextIndex + options.length) % options.length;
    const option = options[clamped];
    if (option) onChange(option.value);
    const buttons = groupRef.current?.querySelectorAll<HTMLButtonElement>('[role="radio"]');
    buttons?.[clamped]?.focus();
  };

  return (
    <div
      ref={groupRef}
      role="radiogroup"
      aria-label={label}
      className={cn(
        "relative inline-flex items-center rounded-control border border-border bg-muted p-0.5",
        className,
      )}
      onKeyDown={(event) => {
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          event.preventDefault();
          move(activeIndex + 1);
        } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          event.preventDefault();
          move(activeIndex - 1);
        } else if (event.key === "Home") {
          event.preventDefault();
          move(0);
        } else if (event.key === "End") {
          event.preventDefault();
          move(options.length - 1);
        }
      }}
    >
      <span
        aria-hidden="true"
        className={cn(
          "absolute top-0.5 bottom-0.5 left-0.5 rounded-[6px] bg-card bevel-neutral transition-transform duration-[var(--motion-standard)]",
        )}
        style={{
          width: `calc((100% - 4px) / ${columns})`,
          transform: `translateX(calc(${activeIndex} * 100%))`,
        }}
      />
      {options.map((option) => {
        const checked = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={checked}
            tabIndex={checked ? 0 : -1}
            title={option.hint}
            onClick={() => onChange(option.value)}
            className={cn(
              "relative z-10 inline-flex items-center justify-center rounded-[6px] font-medium whitespace-nowrap transition-colors duration-[var(--motion-fast)]",
              size === "sm" ? "min-h-6 px-2 text-[11px]" : "min-h-7 px-3 text-[12px]",
              checked ? "text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function TextField({
  label,
  value,
  onChange,
  placeholder,
  hint,
  mono = false,
  error,
  required = false,
  className,
  inputMode,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  hint?: React.ReactNode;
  mono?: boolean;
  error?: string | null;
  required?: boolean;
  className?: string;
  inputMode?: "text" | "numeric";
}): React.JSX.Element {
  const inputId = useId();
  const hintId = `${inputId}-hint`;
  return (
    <div className={cn("space-y-1.5", className)}>
      <label htmlFor={inputId} className="block text-[12px] font-medium text-foreground">
        {label}
        {required ? <span className="ml-1 text-critical">*</span> : null}
      </label>
      <input
        id={inputId}
        value={value}
        inputMode={inputMode}
        placeholder={placeholder}
        aria-invalid={error ? true : undefined}
        aria-describedby={hint ? hintId : undefined}
        onChange={(event) => onChange(event.target.value)}
        className={cn(
          "h-9 w-full rounded-control border bg-muted px-2.5 text-[13px] text-foreground transition-colors duration-[var(--motion-fast)] focus:bg-card focus:outline-none",
          mono && "font-mono text-[12px]",
          error ? "border-critical" : "border-input focus:border-[var(--border-strong)]",
        )}
      />
      {error ? (
        <p className="text-[11px] text-critical">{error}</p>
      ) : hint ? (
        <p id={hintId} className="text-[11px] leading-relaxed text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function CheckboxField({
  label,
  description,
  checked,
  onChange,
  disabled = false,
  disabledReason,
}: {
  label: string;
  description?: React.ReactNode;
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  disabledReason?: string;
}): React.JSX.Element {
  const inputId = useId();
  const descriptionId = `${inputId}-description`;
  return (
    <div className="flex items-start gap-2.5">
      <input
        id={inputId}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        title={disabled ? disabledReason : undefined}
        aria-describedby={description ? descriptionId : undefined}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 size-4 shrink-0 accent-[var(--primary)] disabled:opacity-40"
      />
      <div className="min-w-0">
        <label
          htmlFor={inputId}
          className={cn(
            "block text-[12px] font-medium text-foreground",
            disabled && "opacity-60",
          )}
        >
          {label}
        </label>
        {description ? (
          <p id={descriptionId} className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
    </div>
  );
}
