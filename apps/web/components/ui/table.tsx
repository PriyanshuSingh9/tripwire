"use client";

import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, ChevronsUpDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { formatNumber } from "@/lib/format";

export interface Column<T> {
  id: string;
  header: React.ReactNode;
  cell: (row: T) => React.ReactNode;
  align?: "left" | "right";
  sortable?: boolean;
  /** Value used for client-side sorting; required when `sortable`. */
  sortValue?: (row: T) => string | number;
  className?: string;
  headerTitle?: string;
}

export type SortDirection = "asc" | "desc";
export interface SortState {
  columnId: string;
  direction: SortDirection;
}

export function useSortedRows<T>(
  rows: T[],
  columns: Column<T>[],
  initial: SortState | null,
): {
  rows: T[];
  sort: SortState | null;
  toggle: (columnId: string) => void;
} {
  const [sort, setSort] = useState<SortState | null>(initial);

  const sorted = useMemo(() => {
    if (sort === null) return rows;
    const column = columns.find((candidate) => candidate.id === sort.columnId);
    if (!column?.sortValue) return rows;
    const read = column.sortValue;
    const factor = sort.direction === "asc" ? 1 : -1;
    return [...rows].sort((left, right) => {
      const a = read(left);
      const b = read(right);
      if (typeof a === "number" && typeof b === "number") return (a - b) * factor;
      return String(a).localeCompare(String(b)) * factor;
    });
  }, [rows, columns, sort]);

  const toggle = (columnId: string): void => {
    setSort((current) => {
      if (current === null || current.columnId !== columnId) {
        return { columnId, direction: "desc" };
      }
      return {
        columnId,
        direction: current.direction === "desc" ? "asc" : "desc",
      };
    });
  };

  return { rows: sorted, sort, toggle };
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowActivate,
  isRowActive,
  sort,
  onSortToggle,
  caption,
  emptyLabel = "No records",
  className,
  rowClassName,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowActivate?: (row: T) => void;
  isRowActive?: (row: T) => boolean;
  sort?: SortState | null;
  onSortToggle?: (columnId: string) => void;
  caption?: string;
  emptyLabel?: string;
  className?: string;
  rowClassName?: (row: T) => string | undefined;
}): React.JSX.Element {
  return (
    <div className={cn("relative overflow-x-auto", className)}>
      <table className="w-full border-collapse text-left text-[12.5px]">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr className="border-b border-border">
            {columns.map((column) => {
              const isSorted = sort?.columnId === column.id;
              const SortIcon = !isSorted
                ? ChevronsUpDown
                : sort.direction === "asc"
                  ? ArrowUp
                  : ArrowDown;
              return (
                <th
                  key={column.id}
                  scope="col"
                  title={column.headerTitle}
                  aria-sort={
                    isSorted ? (sort.direction === "asc" ? "ascending" : "descending") : undefined
                  }
                  className={cn(
                    "px-3 py-2 text-[11px] font-medium tracking-[0.04em] whitespace-nowrap text-muted-foreground uppercase",
                    column.align === "right" ? "text-right" : "text-left",
                    column.className,
                  )}
                >
                  {column.sortable && onSortToggle ? (
                    <button
                      type="button"
                      onClick={() => onSortToggle(column.id)}
                      className={cn(
                        "inline-flex items-center gap-1 rounded-sm transition-colors duration-[var(--motion-fast)] hover:text-foreground",
                        isSorted && "text-foreground",
                      )}
                    >
                      {column.header}
                      <SortIcon className="size-3" aria-hidden="true" />
                      <span className="sr-only">
                        {isSorted
                          ? `sorted ${sort.direction === "asc" ? "ascending" : "descending"}, activate to reverse`
                          : "activate to sort"}
                      </span>
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="px-3 py-8 text-center text-[12px] text-muted-foreground"
              >
                {emptyLabel}
              </td>
            </tr>
          ) : (
            rows.map((row) => {
              const active = isRowActive?.(row) ?? false;
              return (
                <tr
                  key={rowKey(row)}
                  // Mouse convenience only; the first cell always holds a real
                  // control so keyboard users have the same action.
                  onClick={onRowActivate ? () => onRowActivate(row) : undefined}
                  className={cn(
                    "border-b border-border/70 transition-colors duration-[var(--motion-fast)] last:border-b-0",
                    onRowActivate && "cursor-pointer hover:bg-surface-hover",
                    active && "bg-surface-active",
                    rowClassName?.(row),
                  )}
                >
                  {columns.map((column, index) => {
                    const content = column.cell(row);
                    return (
                      <td
                        key={column.id}
                        className={cn(
                          "px-3 py-2 align-middle",
                          column.align === "right" ? "text-right" : "text-left",
                          index === 0 && "font-medium",
                          column.className,
                        )}
                      >
                        {index === 0 && onRowActivate ? (
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              onRowActivate(row);
                            }}
                            aria-current={active ? "true" : undefined}
                            className="text-left underline-offset-2 hover:text-primary hover:underline"
                          >
                            {content}
                          </button>
                        ) : (
                          content
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

/** Offset pagination. The footer states the loaded slice and the server total. */
export function TablePager({
  total,
  limit,
  offset,
  onChange,
  className,
  label = "records",
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (nextOffset: number) => void;
  className?: string;
  label?: string;
}): React.JSX.Element {
  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + limit, total);
  const canGoBack = offset > 0;
  const canGoForward = last < total;
  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-2.5 text-[12px] text-muted-foreground",
        className,
      )}
    >
      <p className="tabular">
        Showing <span className="text-foreground">{formatNumber(first)}</span>–
        <span className="text-foreground">{formatNumber(last)}</span> of{" "}
        <span className="text-foreground">{formatNumber(total)}</span> {label}
      </p>
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="secondary"
          icon={ChevronLeft}
          disabled={!canGoBack}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          Previous
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={!canGoForward}
          onClick={() => onChange(offset + limit)}
        >
          Next
          <ChevronRight className="size-3.5" aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}
