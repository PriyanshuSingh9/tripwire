"""Graph dependency status probe and pooled FalkorDB access.

Reports only coarse status values. Never returns hosts, ports, credentials,
dataset labels, or exception text.

``GraphPool`` keeps one bounded, blocking Redis connection pool per process
(keyed by host, port, and graph name) so requests reuse sockets instead of
connecting per request. Each worker thread receives its own graph handle over
the shared pool: falkordb-py graph handles cache schema state, so they are
not shared across threads, while the underlying connections are.
"""

from __future__ import annotations

import threading
from typing import Any, Literal

GraphStatus = Literal["available", "unavailable", "not_configured"]

# Extra seconds the socket waits beyond the server-side query timeout, so the
# FalkorDB timeout fires first and a dead socket still cannot block forever.
SOCKET_TIMEOUT_MARGIN_SECONDS = 2.0
CONNECT_TIMEOUT_SECONDS = 1.0


class GraphUnavailable(Exception):
    """Raised when no usable FalkorDB graph handle can be produced."""


def probe_graph(host: str | None, port: int) -> GraphStatus:
    """Probe FalkorDB with a short timeout and map the result to a status."""
    if host is None or host.strip() == "":
        return "not_configured"

    client = None
    try:
        from falkordb import FalkorDB

        client = FalkorDB(
            host=host,
            port=port,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        client.execute_command("PING")
        return "available"
    except Exception:  # noqa: BLE001 -- any probe failure maps to a coarse status
        return "unavailable"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001, S110 -- close failures never escape probes
                pass


class GraphPool:
    """Process-wide FalkorDB connection pool with per-thread graph handles."""

    def __init__(
        self,
        host: str,
        port: int,
        graph_name: str,
        *,
        max_connections: int,
        query_timeout_ms: int,
    ) -> None:
        import redis
        from falkordb import FalkorDB

        self.graph_name = graph_name
        self.query_timeout_ms = query_timeout_ms
        self._pool = redis.BlockingConnectionPool(
            host=host,
            port=port,
            max_connections=max_connections,
            timeout=query_timeout_ms / 1000.0,
            socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
            socket_timeout=query_timeout_ms / 1000.0 + SOCKET_TIMEOUT_MARGIN_SECONDS,
        )
        self._client = FalkorDB(connection_pool=self._pool)
        self._local = threading.local()

    def graph(self) -> Any:
        """Return this thread's graph handle over the shared connections."""
        handle = getattr(self._local, "graph", None)
        if handle is None:
            handle = self._client.select_graph(self.graph_name)
            self._local.graph = handle
        return handle

    def close(self) -> None:
        """Disconnect every pooled connection."""
        self._pool.disconnect()


_POOLS: dict[tuple[str, int, str, int, int], GraphPool] = {}
_POOLS_LOCK = threading.Lock()


def get_graph_pool(
    host: str | None,
    port: int,
    graph_name: str,
    *,
    max_connections: int = 16,
    query_timeout_ms: int = 5_000,
) -> GraphPool:
    """Return the shared pool for this target or raise GraphUnavailable.

    Pool construction does not connect; connection failures surface on the
    first query and are classified by the investigation layer.
    """
    if host is None or host.strip() == "":
        raise GraphUnavailable("graph is not configured")
    key = (host, port, graph_name, max_connections, query_timeout_ms)
    with _POOLS_LOCK:
        pool = _POOLS.get(key)
        if pool is None:
            try:
                pool = GraphPool(
                    host,
                    port,
                    graph_name,
                    max_connections=max_connections,
                    query_timeout_ms=query_timeout_ms,
                )
            except Exception as exc:
                raise GraphUnavailable("graph is unavailable") from exc
            _POOLS[key] = pool
        return pool


def close_graph_pools() -> None:
    """Disconnect and forget every pool (application shutdown)."""
    with _POOLS_LOCK:
        for pool in _POOLS.values():
            try:
                pool.close()
            except Exception:  # noqa: BLE001, S110 -- shutdown never raises
                pass
        _POOLS.clear()
