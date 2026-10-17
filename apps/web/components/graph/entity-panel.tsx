"use client";

import { ArrowUpRight, Landmark, Link2, Receipt } from "lucide-react";

import { Chip, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, StatLine } from "@/components/ui/card";
import { Disclosure, SidePanel } from "@/components/ui/side-panel";
import { EmptyState } from "@/components/ui/states";
import {
  accountLabel,
  sentEdgeId,
  receivedEdgeId,
  transactionNodeId,
  accountNodeId,
  type GraphEdge,
  type GraphModel,
  type GraphNode,
} from "@/lib/graph/model";
import { formatAmount, formatTimestamp, titleCase } from "@/lib/format";
import type { GraphSelection } from "@/components/graph/graph-canvas";

function nodeHeadline(node: GraphNode): string {
  return node.kind === "account" ? accountLabel(node.identifier) : node.identifier;
}

/** Account detail: identity, connected entities, incident transfers. */
function AccountDetails({
  model,
  node,
  onSelectNode,
  onSelectTransaction,
}: {
  model: GraphModel;
  node: GraphNode;
  onSelectNode: (nodeId: string) => void;
  onSelectTransaction: (transactionId: string) => void;
}): React.JSX.Element {
  const row = model.accounts.find((account) => account.accountKey === node.identifier);
  const incident = row?.incidentTransactionIds ?? [];
  const neighbours = new Set<string>();
  for (const transfer of model.transfers) {
    const record = transfer.transaction;
    if (record.source_account_key === node.identifier) neighbours.add(record.destination_account_key);
    if (record.destination_account_key === node.identifier) neighbours.add(record.source_account_key);
  }

  return (
    <div className="space-y-4">
      <Card className="bg-muted">
        <CardBody className="space-y-1">
          <StatLine label="Entity" value="Account" tone="muted" />
          <StatLine label="Bank / institution" value={row?.bank || "not separated"} tone="muted" />
          <StatLine label="Account number" value={row?.account ?? node.identifier} />
          <StatLine label="Anchor endpoint" value={node.isAnchor ? "Yes" : "No"} tone="muted" />
          <StatLine
            label="On a returned path"
            value={node.pathIndexes.length > 0 ? `Path ${node.pathIndexes.map((i) => i + 1).join(", ")}` : "No"}
            tone={node.pathIndexes.length > 0 ? "warning" : "muted"}
          />
        </CardBody>
      </Card>

      <section>
        <h3 className="text-[12px] font-semibold text-foreground">
          Connected entities in this result
        </h3>
        {neighbours.size === 0 ? (
          <p className="mt-1 text-[12px] text-muted-foreground">
            No other account in this result shares a transfer with this account inside the queried
            bounds.
          </p>
        ) : (
          <ul className="mt-2 space-y-1">
            {[...neighbours].map((key) => (
              <li key={key}>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={Landmark}
                  className="w-full justify-start font-mono text-[11.5px]"
                  onClick={() => onSelectNode(accountNodeId(key))}
                >
                  {accountLabel(key)}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h3 className="text-[12px] font-semibold text-foreground">
          Transfers involving this account
        </h3>
        <p className="mt-0.5 text-[11px] text-muted-foreground">
          {incident.length} record{incident.length === 1 ? "" : "s"} returned by the executed
          queries.
        </p>
        <ul className="mt-2 space-y-1.5">
          {incident.map((transactionId) => {
            const transfer = model.transfers.find((item) => item.id === transactionId);
            const record = transfer?.transaction;
            const outgoing = record?.source_account_key === node.identifier;
            return (
              <li key={transactionId}>
                <button
                  type="button"
                  onClick={() => onSelectTransaction(transactionId)}
                  className="w-full rounded-control border border-border bg-muted px-2.5 py-2 text-left transition-colors duration-[var(--motion-fast)] hover:border-border-strong hover:bg-surface-hover"
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-[11px] text-foreground">
                      {transactionId}
                    </span>
                    <StatusBadge
                      label={outgoing ? "Outgoing" : "Incoming"}
                      tone={outgoing ? "neutral" : "primary"}
                    />
                  </span>
                  <span className="mt-1 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                    <span className="tabular">
                      {formatAmount(record?.amount_paid, record?.payment_currency)}
                    </span>
                    <span>{formatTimestamp(record?.timestamp)}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

/** Transfer detail: the anchor record and its two account ends. */
function TransactionDetails({
  model,
  node,
  onSelectNode,
}: {
  model: GraphModel;
  node: GraphNode;
  onSelectNode: (nodeId: string) => void;
}): React.JSX.Element {
  const row = model.transfers.find((transfer) => transfer.id === node.identifier);
  if (!row) {
    return (
      <EmptyState
        title="This transfer is not in the returned evidence"
        description="The graph node exists in the rendered set but no transaction record accompanies it."
      />
    );
  }
  const record = row.transaction;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {row.isAnchor ? <StatusBadge label="Anchor transfer" tone="primary" icon={Receipt} /> : null}
        <Chip mono>{row.originLabel}</Chip>
        {row.supporting ? <StatusBadge label="Cited by report" tone="warning" /> : null}
      </div>

      <Card className="bg-muted">
        <CardBody className="space-y-1">
          <StatLine label="Timestamp (UTC)" value={formatTimestamp(record.timestamp)} />
          <StatLine
            label="Amount paid"
            value={formatAmount(record.amount_paid, record.payment_currency)}
          />
          <StatLine
            label="Amount received"
            value={formatAmount(record.amount_received, record.receiving_currency)}
          />
          <StatLine label="Payment format" value={record.payment_format ?? "not recorded"} tone="muted" />
          <StatLine
            label="On a returned path"
            value={
              row.pathIndexes.length > 0
                ? `Path ${row.pathIndexes.map((i) => i + 1).join(", ")}`
                : "No"
            }
            tone={row.pathIndexes.length > 0 ? "warning" : "muted"}
          />
        </CardBody>
      </Card>

      <section className="space-y-2">
        <h3 className="text-[12px] font-semibold text-foreground">Account ends</h3>
        <div className="space-y-1.5">
          <div>
            <p className="text-[11px] text-muted-foreground">Sent by</p>
            <Button
              size="sm"
              variant="secondary"
              icon={Landmark}
              className="mt-1 w-full justify-start font-mono text-[11.5px]"
              onClick={() => onSelectNode(accountNodeId(record.source_account_key))}
            >
              {record.source_account_key}
            </Button>
          </div>
          <div>
            <p className="text-[11px] text-muted-foreground">Received by</p>
            <Button
              size="sm"
              variant="secondary"
              icon={Landmark}
              className="mt-1 w-full justify-start font-mono text-[11.5px]"
              onClick={() => onSelectNode(accountNodeId(record.destination_account_key))}
            >
              {record.destination_account_key}
            </Button>
          </div>
        </div>
      </section>

      <Disclosure summary="Record fields">
        <dl className="space-y-1">
          <StatLine label="transaction_id" value={<span className="font-mono text-[11px]">{record.transaction_id}</span>} tone="muted" />
          <StatLine label="payment_currency" value={record.payment_currency ?? "not recorded"} tone="muted" />
          <StatLine label="receiving_currency" value={record.receiving_currency ?? "not recorded"} tone="muted" />
        </dl>
      </Disclosure>
    </div>
  );
}

/** Relationship detail: which transfer the edge stands for, and its endpoints. */
function EdgeDetails({
  model,
  edge,
  onSelectNode,
  onSelectTransaction,
}: {
  model: GraphModel;
  edge: GraphEdge;
  onSelectNode: (nodeId: string) => void;
  onSelectTransaction: (transactionId: string) => void;
}): React.JSX.Element {
  const row = model.transfers.find((transfer) => transfer.id === edge.transactionId);
  const record = row?.transaction;
  const source = model.nodeById.get(edge.source);
  const target = model.nodeById.get(edge.target);
  return (
    <div className="space-y-4">
      <Card className="bg-muted">
        <CardBody className="space-y-1">
          <StatLine label="Relationship" value={edge.kind === "SENT" ? "SENT" : "RECEIVED"} />
          <StatLine
            label="Schema meaning"
            value={edge.kind === "SENT" ? "Account sent this transfer" : "This transfer was received by the account"}
            tone="muted"
          />
          <StatLine label="Carried by transfer" value={<span className="font-mono text-[11px]">{edge.transactionId}</span>} />
          <StatLine
            label="On a returned path"
            value={edge.pathIndexes.length > 0 ? `Path ${edge.pathIndexes.map((i) => i + 1).join(", ")}` : "No"}
            tone={edge.pathIndexes.length > 0 ? "warning" : "muted"}
          />
        </CardBody>
      </Card>

      <div className="space-y-1.5">
        <p className="text-[11px] text-muted-foreground">Endpoints</p>
        {source ? (
          <Button
            size="sm"
            variant="secondary"
            icon={Link2}
            className="w-full justify-start text-[11.5px]"
            onClick={() => onSelectNode(source.id)}
          >
            {nodeHeadline(source)}
          </Button>
        ) : null}
        {target ? (
          <Button
            size="sm"
            variant="secondary"
            icon={Link2}
            className="w-full justify-start text-[11.5px]"
            onClick={() => onSelectNode(target.id)}
          >
            {nodeHeadline(target)}
          </Button>
        ) : null}
      </div>

      {record ? (
        <section className="space-y-2">
          <h3 className="text-[12px] font-semibold text-foreground">Supporting record</h3>
          <p className="text-[11.5px] leading-relaxed text-muted-foreground">
            This relationship is grounded in {row?.originLabel.toLowerCase() ?? "returned evidence"},
            a transfer record the executed queries returned. It is evidence of graph structure, not
            a finding of wrongdoing.
          </p>
          <Button
            size="sm"
            variant="primary"
            icon={ArrowUpRight}
            onClick={() => onSelectTransaction(record.transaction_id)}
          >
            Inspect transfer record
          </Button>
          <dl className="rounded-control border border-border bg-muted px-2.5 py-2">
            <StatLine label="Time (UTC)" value={formatTimestamp(record.timestamp)} />
            <StatLine label="Amount paid" value={formatAmount(record.amount_paid, record.payment_currency)} />
          </dl>
        </section>
      ) : null}
    </div>
  );
}

/**
 * Contextual details for whatever is selected on the canvas.
 * Plain language first; raw identifiers and contract fields behind a
 * disclosure.
 */
export function EntityPanel({
  model,
  selection,
  onClose,
  onSelectNode,
  onSelectTransaction,
  focusLabel,
  onFocus,
}: {
  model: GraphModel;
  selection: GraphSelection;
  onClose: () => void;
  onSelectNode: (nodeId: string) => void;
  onSelectTransaction: (transactionId: string) => void;
  focusLabel?: string;
  onFocus?: () => void;
}): React.JSX.Element | null {
  const node = selection.kind === "node" ? model.nodeById.get(selection.id) : undefined;
  const edge = selection.kind === "edge" ? model.edgeById.get(selection.id) : undefined;

  if (!node && !edge) return null;

  const title = node
    ? node.kind === "account"
      ? "Account details"
      : "Transfer details"
    : "Relationship details";
  const subtitle = node
    ? node.typeLabel
    : edge
      ? edge.kind === "SENT"
        ? "Account → Transaction (SENT)"
        : "Transaction → Account (RECEIVED)"
      : "";

  return (
    <SidePanel
      open
      onClose={onClose}
      title={title}
      subtitle={<span className="font-mono text-[11px]">{subtitle}</span>}
      footer={
        onFocus ? (
          <Button size="sm" variant="secondary" className="w-full" onClick={onFocus}>
            {focusLabel ?? "Focus connections"}
          </Button>
        ) : null
      }
    >
      {node ? (
        <>
          <p className="mb-3 font-mono text-[11px] break-all text-muted-foreground">
            {node.identifier}
          </p>
          {node.kind === "account" ? (
            <AccountDetails
              model={model}
              node={node}
              onSelectNode={onSelectNode}
              onSelectTransaction={onSelectTransaction}
            />
          ) : (
            <TransactionDetails model={model} node={node} onSelectNode={onSelectNode} />
          )}
        </>
      ) : edge ? (
        <EdgeDetails
          model={model}
          edge={edge}
          onSelectNode={onSelectNode}
          onSelectTransaction={onSelectTransaction}
        />
      ) : null}
      {node ? (
        <div className="mt-4">
          <Disclosure summary="Technical identifiers">
            <dl className="space-y-1">
              <StatLine
                label="node id"
                value={<span className="font-mono text-[11px]">{node.id}</span>}
                tone="muted"
              />
              <StatLine
                label="kind"
                value={<span className="font-mono text-[11px]">{node.kind}</span>}
                tone="muted"
              />
              <StatLine
                label="from account list"
                value={node.fromAccountList ? "yes" : "derived from transfer endpoints"}
                tone="muted"
              />
              {node.kind === "transaction" ? (
                <>
                  <StatLine
                    label="sent edge"
                    value={<span className="font-mono text-[11px]">{sentEdgeId(node.identifier)}</span>}
                    tone="muted"
                  />
                  <StatLine
                    label="received edge"
                    value={
                      <span className="font-mono text-[11px]">{receivedEdgeId(node.identifier)}</span>
                    }
                    tone="muted"
                  />
                  <StatLine
                    label="node id"
                    value={
                      <span className="font-mono text-[11px]">
                        {transactionNodeId(node.identifier)}
                      </span>
                    }
                    tone="muted"
                  />
                </>
              ) : null}
            </dl>
          </Disclosure>
        </div>
      ) : null}
    </SidePanel>
  );
}

export { titleCase };
