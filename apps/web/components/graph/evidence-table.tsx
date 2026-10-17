"use client";

import { useEffect, useMemo, useRef } from "react";
import { ArrowRight, Search } from "lucide-react";

import {
  DataTable,
  useSortedRows,
  type Column,
  type SortState,
} from "@/components/ui/table";
import { SegmentedControl } from "@/components/ui/controls";
import { Chip, StatusBadge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/states";
import {
  formatAmount,
  formatNumber,
  formatTimestamp,
  truncateMiddle,
} from "@/lib/format";
import type { EvidenceRow, GraphModel } from "@/lib/graph/model";

type RowFilter = "all" | "transfers" | "accounts";

const DEFAULT_SORT: SortState = { columnId: "path", direction: "asc" };

/**
 * Evidence table.
 *
 * Columns are grouped so the eye takes two edges: what the record is on the
 * left, what it means on the right. Every row refers to a record an executed
 * query returned, and activating a row selects the same element on the canvas.
 */
export function EvidenceTable({
  model,
  activeIds,
  onSelectTransaction,
  onSelectAccount,
  search,
  onSearchChange,
  filter,
  onFilterChange,
  className,
}: {
  model: GraphModel;
  activeIds: Set<string>;
  onSelectTransaction: (transactionId: string) => void;
  onSelectAccount: (accountKey: string) => void;
  search: string;
  onSearchChange: (value: string) => void;
  filter: RowFilter;
  onFilterChange: (filter: RowFilter) => void;
  className?: string;
}): React.JSX.Element {
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const isTransaction = filter === "transfers";
    const isAccount = filter === "accounts";
    return model.rows.filter((row) => {
      if (isTransaction && row.kind !== "transaction") return false;
      if (isAccount && row.kind !== "account") return false;
      if (needle.length === 0) return true;
      if (row.kind === "transaction") {
        const record = row.transaction;
        return (
          record.transaction_id.toLowerCase().includes(needle) ||
          record.source_account_key.toLowerCase().includes(needle) ||
          record.destination_account_key.toLowerCase().includes(needle) ||
          (record.payment_format ?? "").toLowerCase().includes(needle)
        );
      }
      return (
        row.accountKey.toLowerCase().includes(needle) || row.account.toLowerCase().includes(needle)
      );
    });
  }, [model.rows, search, filter]);

  const columns = useMemo<Column<EvidenceRow>[]>(
    () => [
      {
        id: "record",
        header: "Record",
        sortable: true,
        sortValue: (row) => row.id,
        cell: (row) =>
          row.kind === "transaction" ? (
            <span className="font-mono text-[11.5px] text-foreground">{row.transaction.transaction_id}</span>
          ) : (
            <span className="font-mono text-[11.5px] text-foreground">{row.account}</span>
          ),
      },
      {
        id: "type",
        header: "Type",
        sortable: true,
        sortValue: (row) => row.kind,
        cell: (row) => (
          <span className="flex items-center gap-1.5">
            <span className="text-[12px] text-foreground">
              {row.kind === "transaction" ? "Transfer" : "Account"}
            </span>
            {row.kind === "transaction" && row.isAnchor ? (
              <StatusBadge label="Anchor" tone="primary" />
            ) : null}
            {row.kind === "transaction" && row.supporting ? (
              <StatusBadge label="Cited" tone="warning" />
            ) : null}
          </span>
        ),
      },
      {
        id: "detail",
        header: "Detail",
        align: "right",
        sortable: true,
        sortValue: (row) =>
          row.kind === "transaction" ? (row.transaction.amount_paid ?? 0) : row.bank,
        cell: (row) =>
          row.kind === "transaction" ? (
            <span className="tabular text-[12px] text-foreground">
              {formatAmount(row.transaction.amount_paid, row.transaction.payment_currency)}
              <span className="ml-2 text-[11px] text-muted-foreground">
                {row.transaction.payment_format ?? "format not recorded"}
              </span>
            </span>
          ) : (
            <span className="text-[12px] text-muted-foreground">{row.bank || "bank not separated"}</span>
          ),
      },
      {
        id: "time",
        header: "Time (UTC)",
        align: "right",
        sortable: true,
        sortValue: (row) => (row.kind === "transaction" ? row.transaction.timestamp : ""),
        cell: (row) =>
          row.kind === "transaction" ? (
            <span className="tabular text-[11.5px] whitespace-nowrap text-muted-foreground">
              {formatTimestamp(row.transaction.timestamp)}
            </span>
          ) : (
            <span className="text-[11.5px] text-muted-foreground">
              {formatNumber(row.incidentTransactionIds.length)} linked
            </span>
          ),
      },
      {
        id: "related",
        header: "Connected record",
        cell: (row) =>
          row.kind === "transaction" ? (
            <span className="flex items-center gap-1 font-mono text-[10.5px] text-muted-foreground">
              <span className="truncate" title={row.transaction.source_account_key}>
                {truncateMiddle(row.transaction.source_account_key, 18)}
              </span>
              <ArrowRight className="size-3 shrink-0" aria-hidden="true" />
              <span className="truncate" title={row.transaction.destination_account_key}>
                {truncateMiddle(row.transaction.destination_account_key, 18)}
              </span>
            </span>
          ) : (
            <span className="text-[11.5px] text-muted-foreground">
              {row.isAnchor ? "anchor endpoint" : "counterparty"}
            </span>
          ),
      },
      {
        id: "path",
        header: "Returned via",
        sortable: true,
        sortValue: (row) => (row.pathIndexes.length === 0 ? 99 : Math.min(...row.pathIndexes)),
        cell: (row) => (
          <span className="flex flex-wrap items-center gap-1">
            <Chip title="Which query result carried this record; the service reports durations per tool, not per record.">
              {row.originLabel}
            </Chip>
            {row.pathIndexes.length > 0 ? (
              <Chip mono>path {row.pathIndexes.map((index) => index + 1).join(", ")}</Chip>
            ) : null}
          </span>
        ),
      },
    ],
    [],
  );

  const { rows: sortedRows, sort, toggle } = useSortedRows(filtered, columns, DEFAULT_SORT);
  const effectiveSort = sort ?? DEFAULT_SORT;

  const bodyRef = useRef<HTMLDivElement>(null);
  const highlighted = activeIds.size > 0;

  // When the canvas selects something, bring its row into view. Scrolling a
  // DOM node is the external-system half of this; the highlight itself is
  // derived from the selection rather than stored.
  useEffect(() => {
    if (activeIds.size === 0 || !bodyRef.current) return;
    const target = bodyRef.current.querySelector<HTMLElement>("tr[data-active='true']");
    target?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activeIds]);

  return (
    <section className={className} aria-label="Evidence records">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
        <SegmentedControl<RowFilter>
          label="Filter evidence rows"
          size="sm"
          value={filter}
          onChange={onFilterChange}
          options={[
            { value: "all", label: `All (${model.rows.length})` },
            { value: "transfers", label: `Transfers (${model.transfers.length})` },
            { value: "accounts", label: `Accounts (${model.accounts.length})` },
          ]}
        />
        <div className="relative w-[190px]">
          <label htmlFor="evidence-search" className="sr-only">
            Search evidence records
          </label>
          <Search
            className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <input
            id="evidence-search"
            type="search"
            value={search}
            placeholder="Search records"
            onChange={(event) => onSearchChange(event.target.value)}
            className="h-7 w-full rounded-control border border-input bg-muted pr-2 pl-7 text-[12px] text-foreground focus:border-[var(--border-strong)] focus:bg-card focus:outline-none"
          />
        </div>
        {highlighted ? (
          <p
            className="text-[11px] font-medium text-primary"
            title="Selection on the canvas marks the same records here."
          >
            {activeIds.size} selected record{activeIds.size === 1 ? "" : "s"} highlighted
          </p>
        ) : null}
        <p className="ml-auto text-[11px] text-muted-foreground">
          {formatNumber(sortedRows.length)} of {formatNumber(model.rows.length)} returned records
        </p>
      </div>

      <div ref={bodyRef}>
        {sortedRows.length === 0 ? (
          <EmptyState
            title="No record matches this filter"
            description={
              model.rows.length === 0
                ? "The executed queries returned no records inside the declared bounds."
                : "Clear the search or switch the filter to see the other returned records."
            }
            className="py-8"
          />
        ) : (
          <DataTable<EvidenceRow>
            columns={columns}
            rows={sortedRows}
            rowKey={(row) => row.id}
            sort={effectiveSort}
            onSortToggle={toggle}
            caption="Records returned by the executed graph queries"
            emptyLabel="No records"
            isRowActive={(row) => activeIds.has(row.id)}
            onRowActivate={(row) => {
              if (row.kind === "transaction") onSelectTransaction(row.transaction.transaction_id);
              else onSelectAccount(row.accountKey);
            }}
            rowClassName={(row) =>
              row.kind === "transaction" && row.isAnchor ? "bg-primary/[0.04]" : undefined
            }
          />
        )}
      </div>
    </section>
  );
}

export type { RowFilter };
