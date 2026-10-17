/**
 * Graph model derived from an executed investigation response.
 *
 * Nothing here invents structure. The API returns transfers
 * (`TransactionEvidence`), account references (`AccountEvidence`) and the
 * paths the executed queries returned (`GraphPath`). This module projects that
 * evidence onto the graph schema the repository declares:
 *
 *   (:Account)-[:SENT]->(:Transaction)-[:RECEIVED]->(:Account)
 *
 * Every node and edge carries the identifier it came from, so a selection in
 * the canvas, the evidence table and the detail panel all refer to the same
 * record.
 */

import type {
  GraphPath,
  InvestigationResponse,
  TransactionEvidence,
} from "@/lib/api/types";
import { splitAccountKey, truncateMiddle } from "@/lib/format";

export type EntityKind = "account" | "transaction";
export type EdgeKind = "SENT" | "RECEIVED";

/** Where a record was carried by the query results, derived from the payload. */
export type EvidenceOrigin =
  | "anchor_transfer"
  | "traversal_path"
  | "pattern_path"
  | "query_record"
  | "path_account"
  | "counterparty_record";

export interface GraphNode {
  id: string;
  kind: EntityKind;
  /** Short label drawn on the canvas. */
  label: string;
  /** Two-line label for the card node style used by workspace canvases. */
  cardLabel: string;
  /** Full identifier, used in panels and tooltips. */
  identifier: string;
  typeLabel: string;
  onPath: boolean;
  pathIndexes: number[];
  isAnchor: boolean;
  /** True when the identifier came from the graph's account list. */
  fromAccountList: boolean;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: EdgeKind;
  /** Plain-language relationship label derived from the schema. */
  label: string;
  transactionId: string;
  onPath: boolean;
  pathIndexes: number[];
}

export interface TransferRow {
  kind: "transaction";
  id: string;
  transaction: TransactionEvidence;
  origin: EvidenceOrigin;
  originLabel: string;
  onPath: boolean;
  pathIndexes: number[];
  isAnchor: boolean;
  supporting: boolean;
  cited: boolean;
}

export interface AccountRow {
  kind: "account";
  id: string;
  accountKey: string;
  bank: string;
  account: string;
  origin: EvidenceOrigin;
  originLabel: string;
  onPath: boolean;
  pathIndexes: number[];
  isAnchor: boolean;
  incidentTransactionIds: string[];
}

export type EvidenceRow = TransferRow | AccountRow;

export interface GraphModel {
  nodes: GraphNode[];
  edges: GraphEdge[];
  nodeById: Map<string, GraphNode>;
  edgeById: Map<string, GraphEdge>;
  transfers: TransferRow[];
  accounts: AccountRow[];
  rows: EvidenceRow[];
  paths: GraphPath[];
  /** Transactions cited by the returned report, when one is present. */
  supportingIds: string[];
  citedIds: string[];
}

export const ORIGIN_LABELS: Record<EvidenceOrigin, string> = {
  anchor_transfer: "Anchor transfer",
  traversal_path: "On a traced path",
  pattern_path: "On a pattern match",
  query_record: "Returned by a query",
  path_account: "On a traced path",
  counterparty_record: "Counterparty record",
};

export function accountNodeId(accountKey: string): string {
  return `account:${accountKey}`;
}

export function transactionNodeId(transactionId: string): string {
  return `transaction:${transactionId}`;
}

export function sentEdgeId(transactionId: string): string {
  return `sent:${transactionId}`;
}

export function receivedEdgeId(transactionId: string): string {
  return `received:${transactionId}`;
}

export function accountLabel(accountKey: string): string {
  const { bank, account } = splitAccountKey(accountKey);
  return bank.length > 0 ? `${account}` : accountKey;
}

/** Compact two-line transfer label: amount (or a short id) and its currency. */
function transferCardLabel(transfer: TransactionEvidence): string {
  const amount = transfer.amount_paid ?? transfer.amount_received ?? null;
  const currency = transfer.payment_currency ?? transfer.receiving_currency ?? null;
  const title =
    amount === null
      ? truncateMiddle(transfer.transaction_id, 16)
      : new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(amount);
  return `${title}\n${currency ?? "transfer"}`;
}

function pathIndexesByAccount(paths: GraphPath[]): Map<string, number[]> {
  const index = new Map<string, number[]>();
  paths.forEach((path, position) => {
    for (const key of path.account_keys) {
      const existing = index.get(key);
      if (existing) existing.push(position);
      else index.set(key, [position]);
    }
  });
  return index;
}

function pathIndexesByTransaction(paths: GraphPath[]): Map<string, number[]> {
  const index = new Map<string, number[]>();
  paths.forEach((path, position) => {
    for (const id of path.transaction_ids) {
      const existing = index.get(id);
      if (existing) existing.push(position);
      else index.set(id, [position]);
    }
  });
  return index;
}

/** Project one investigation response onto the graph schema. */
export function buildGraphModel(response: InvestigationResponse): GraphModel {
  const { evidence } = response;
  const paths = evidence.paths;
  const accountsOnPaths = pathIndexesByAccount(paths);
  const transactionsOnPaths = pathIndexesByTransaction(paths);

  const accountKeys = new Set<string>(evidence.accounts.map((entry) => entry.account_key));
  for (const transfer of evidence.transactions) {
    accountKeys.add(transfer.source_account_key);
    accountKeys.add(transfer.destination_account_key);
  }

  const report = response.report ?? null;
  const supportingIds = report?.supporting_transaction_ids ?? [];
  const citedIds = report?.cited_claims.map((claim) => claim.transaction_id) ?? [];
  const supporting = new Set(supportingIds);
  const cited = new Set(citedIds);

  const nodes: GraphNode[] = [];
  const nodeById = new Map<string, GraphNode>();

  for (const key of accountKeys) {
    const { bank, account } = splitAccountKey(key);
    const indexes = accountsOnPaths.get(key) ?? [];
    const node: GraphNode = {
      id: accountNodeId(key),
      kind: "account",
      label: account.length > 0 ? account : key,
      cardLabel: `${account.length > 0 ? account : key}\n${
        bank.length > 0 ? `Account · ${bank}` : "Account"
      }`,
      identifier: key,
      typeLabel: bank.length > 0 ? `Account · ${bank}` : "Account",
      onPath: indexes.length > 0,
      pathIndexes: indexes,
      isAnchor:
        key === response.evidence.transactions.find((t) => t.transaction_id === response.transaction_id)?.source_account_key ||
        key === response.evidence.transactions.find((t) => t.transaction_id === response.transaction_id)?.destination_account_key,
      fromAccountList: evidence.accounts.some((entry) => entry.account_key === key),
    };
    nodes.push(node);
    nodeById.set(node.id, node);
  }

  const edges: GraphEdge[] = [];
  const edgeById = new Map<string, GraphEdge>();
  const transfers: TransferRow[] = [];

  for (const transfer of evidence.transactions) {
    const isAnchor = transfer.transaction_id === response.transaction_id;
    const indexes = transactionsOnPaths.get(transfer.transaction_id) ?? [];
    const onPath = indexes.length > 0;

    const transactionNode: GraphNode = {
      id: transactionNodeId(transfer.transaction_id),
      kind: "transaction",
      label: transfer.transaction_id,
      cardLabel: transferCardLabel(transfer),
      identifier: transfer.transaction_id,
      typeLabel: "Transaction",
      onPath,
      pathIndexes: indexes,
      isAnchor,
      fromAccountList: false,
    };
    nodes.push(transactionNode);
    nodeById.set(transactionNode.id, transactionNode);

    const sent: GraphEdge = {
      id: sentEdgeId(transfer.transaction_id),
      source: accountNodeId(transfer.source_account_key),
      target: transactionNode.id,
      kind: "SENT",
      label: "sent",
      transactionId: transfer.transaction_id,
      onPath,
      pathIndexes: indexes,
    };
    const received: GraphEdge = {
      id: receivedEdgeId(transfer.transaction_id),
      source: transactionNode.id,
      target: accountNodeId(transfer.destination_account_key),
      kind: "RECEIVED",
      label: "received by",
      transactionId: transfer.transaction_id,
      onPath,
      pathIndexes: indexes,
    };
    edges.push(sent, received);
    edgeById.set(sent.id, sent);
    edgeById.set(received.id, received);

    const origin: EvidenceOrigin = isAnchor
      ? "anchor_transfer"
      : onPath
        ? paths[indexes[0] ?? 0]?.direction.startsWith("pattern:")
          ? "pattern_path"
          : "traversal_path"
        : "query_record";

    transfers.push({
      kind: "transaction",
      id: transfer.transaction_id,
      transaction: transfer,
      origin,
      originLabel: ORIGIN_LABELS[origin],
      onPath,
      pathIndexes: indexes,
      isAnchor,
      supporting: supporting.has(transfer.transaction_id),
      cited: cited.has(transfer.transaction_id),
    });
  }

  const accounts: AccountRow[] = [];
  for (const key of accountKeys) {
    const { bank, account } = splitAccountKey(key);
    const indexes = accountsOnPaths.get(key) ?? [];
    const node = nodeById.get(accountNodeId(key));
    accounts.push({
      kind: "account",
      id: accountNodeId(key),
      accountKey: key,
      bank,
      account,
      origin: indexes.length > 0 ? "path_account" : "counterparty_record",
      originLabel:
        indexes.length > 0 ? ORIGIN_LABELS.path_account : ORIGIN_LABELS.counterparty_record,
      onPath: indexes.length > 0,
      pathIndexes: indexes,
      isAnchor: node?.isAnchor ?? false,
      incidentTransactionIds: evidence.transactions
        .filter(
          (transfer) =>
            transfer.source_account_key === key || transfer.destination_account_key === key,
        )
        .map((transfer) => transfer.transaction_id),
    });
  }

  return {
    nodes,
    edges,
    nodeById,
    edgeById,
    transfers,
    accounts,
    rows: [...transfers, ...accounts],
    paths,
    supportingIds,
    citedIds,
  };
}

/** Transfers incident to an entity, used to link a selection to the table. */
export function incidentTransactionIds(model: GraphModel, nodeId: string): string[] {
  const node = model.nodeById.get(nodeId);
  if (!node) return [];
  if (node.kind === "transaction") return [node.identifier];
  return (
    model.accounts.find((row) => row.accountKey === node.identifier)?.incidentTransactionIds ?? []
  );
}

/** Node and edge ids that belong to one returned path. */
export function elementIdsForPath(model: GraphModel, pathIndex: number): string[] {
  const path = model.paths[pathIndex];
  if (!path) return [];
  const ids: string[] = [];
  for (const key of path.account_keys) ids.push(accountNodeId(key));
  for (const id of path.transaction_ids) {
    ids.push(transactionNodeId(id));
    ids.push(sentEdgeId(id));
    ids.push(receivedEdgeId(id));
  }
  return ids;
}

/** Immediate neighbours of a node plus the node itself. */
export function egoNetwork(model: GraphModel, nodeId: string): Set<string> {
  const ids = new Set<string>([nodeId]);
  for (const edge of model.edges) {
    if (edge.source === nodeId) {
      ids.add(edge.target);
      ids.add(edge.id);
    } else if (edge.target === nodeId) {
      ids.add(edge.source);
      ids.add(edge.id);
    }
  }
  return ids;
}

export interface CytoscapeElement {
  data: Record<string, string | number | boolean>;
}

export interface CytoscapeElements {
  nodes: CytoscapeElement[];
  edges: CytoscapeElement[];
}

/**
 * Cytoscape element payloads. Class names carry the state that the stylesheet
 * keys on (`anchor`, `on-path`, `dimmed`), so styling stays in one place.
 */
export function toCytoscapeElements(
  model: GraphModel,
  options: { keep?: Set<string>; focusId?: string | null } = {},
): CytoscapeElements {
  const keep = options.keep;
  const nodes: CytoscapeElement[] = [];
  const edges: CytoscapeElement[] = [];

  for (const node of model.nodes) {
    if (keep && !keep.has(node.id)) continue;
    nodes.push({
      data: {
        id: node.id,
        label: node.kind === "account" ? node.label : "transfer",
        cardLabel: node.cardLabel,
        fullLabel: node.identifier,
        kind: node.kind,
        typeLabel: node.typeLabel,
        onPath: node.onPath,
        isAnchor: node.isAnchor,
      },
    });
  }

  for (const edge of model.edges) {
    if (keep && (!keep.has(edge.source) || !keep.has(edge.target))) continue;
    edges.push({
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: edge.label,
        kind: edge.kind,
        transactionId: edge.transactionId,
        onPath: edge.onPath,
      },
    });
  }

  return { nodes, edges };
}

/** Compact model for the small previews on Overview, Cases and Alerts. */
export function previewSubset(
  model: GraphModel,
  options: { maxNodes?: number; focusId?: string | null } = {},
): Set<string> {
  const maxNodes = options.maxNodes ?? 14;
  const keep = new Set<string>();
  const focus = options.focusId ?? null;

  if (focus && model.nodeById.has(focus)) {
    for (const id of egoNetwork(model, focus)) keep.add(id);
  }

  // Prefer entities on a path, then the anchor's own neighbourhood, so the
  // preview shows the most evidential part of the result rather than the
  // first-inserted records.
  const ordered = [...model.nodes].sort((left, right) => {
    const score = (node: GraphNode): number =>
      (node.onPath ? 2 : 0) + (node.isAnchor ? 1 : 0) + (node.kind === "account" ? 0.5 : 0);
    return score(right) - score(left);
  });

  for (const node of ordered) {
    if (keep.size >= maxNodes) break;
    keep.add(node.id);
    for (const edge of model.edges) {
      if (edge.source === node.id || edge.target === node.id) keep.add(edge.id);
    }
  }
  return keep;
}
