"""Static topology of SignalForge Demo Commerce: services, infrastructure nodes, edges."""

from __future__ import annotations

from signalforge.world.models import DependencyEdge, InfraNode, Service

SERVICES: list[Service] = [
    Service(
        id="edge-gateway", name="Edge Gateway", team="mast", tier=1, runtime="go", replicas=6,
        autoscale_min=4, autoscale_max=10, datastores=[],
        description="Public ingress: TLS termination, routing, JWT validation, rate limiting.",
    ),
    Service(
        id="identity", name="Identity", team="mast", tier=1, runtime="python", replicas=4,
        autoscale_min=3, autoscale_max=8, datastores=["identity-db", "session-cache"],
        description="Authentication, sessions and token issuance.",
    ),
    Service(
        id="catalog", name="Catalog", team="chart", tier=1, runtime="java", replicas=6,
        autoscale_min=4, autoscale_max=12, datastores=["catalog-db", "search-index"],
        description="Product catalogue and search API.",
    ),
    Service(
        id="cart", name="Cart", team="galley", tier=2, runtime="node", replicas=4,
        autoscale_min=3, autoscale_max=8, datastores=["session-cache"],
        description="Shopping cart backed by the session cache.",
    ),
    Service(
        id="pricing", name="Pricing", team="galley", tier=2, runtime="python", replicas=3,
        autoscale_min=2, autoscale_max=6, datastores=[],
        description="Price and promotion rule evaluation.",
    ),
    Service(
        id="checkout", name="Checkout", team="galley", tier=1, runtime="python", replicas=6,
        autoscale_min=4, autoscale_max=12, datastores=[],
        description="Order placement orchestration across cart, inventory, pricing, payments and orders.",
    ),
    Service(
        id="payments", name="Payments", team="beacon", tier=1, runtime="go", replicas=4,
        autoscale_min=3, autoscale_max=8, datastores=["payments-db"],
        description="Card authorisation and capture through the external PayVault processor.",
    ),
    Service(
        id="inventory", name="Inventory", team="hold", tier=1, runtime="java", replicas=4,
        autoscale_min=3, autoscale_max=8, datastores=["inventory-db"],
        description="Stock levels and reservations.",
    ),
    Service(
        id="orders", name="Orders", team="hold", tier=1, runtime="python", replicas=4,
        autoscale_min=3, autoscale_max=8, datastores=["orders-db"],
        description="Order persistence and lifecycle; publishes order.placed events.",
    ),
    Service(
        id="fulfillment-worker", name="Fulfillment Worker", team="hold", tier=2, runtime="python",
        replicas=6, autoscale_min=6, autoscale_max=12, datastores=["orders-db"],
        description="Consumes order.placed events and creates shipments.",
    ),
    Service(
        id="notifications", name="Notifications", team="signal", tier=3, runtime="node", replicas=3,
        autoscale_min=2, autoscale_max=6, datastores=[],
        description="Email and SMS delivery through the external Mailrelay API.",
    ),
    Service(
        id="reporting", name="Reporting", team="ledger", tier=3, runtime="python", replicas=2,
        autoscale_min=2, autoscale_max=2, datastores=["orders-db", "warehouse"],
        description="Nightly batch analytics and management reports.",
    ),
]

NODES: list[InfraNode] = [
    InfraNode(id="orders-db", name="Orders Database", kind="database",
              description="PostgreSQL primary plus one read replica; connection pool limits apply per client."),
    InfraNode(id="inventory-db", name="Inventory Database", kind="database",
              description="PostgreSQL primary for stock and reservations."),
    InfraNode(id="identity-db", name="Identity Database", kind="database",
              description="PostgreSQL primary with streaming replica for account data."),
    InfraNode(id="catalog-db", name="Catalog Database", kind="database",
              description="PostgreSQL primary for product data."),
    InfraNode(id="payments-db", name="Payments Database", kind="database",
              description="PostgreSQL primary for transaction ledgers."),
    InfraNode(id="warehouse", name="Analytics Warehouse", kind="database",
              description="Columnar warehouse loaded nightly by reporting."),
    InfraNode(id="session-cache", name="Session Cache", kind="cache",
              description="Redis cluster holding sessions and carts."),
    InfraNode(id="event-bus", name="Event Bus", kind="message_bus",
              description="Partitioned log-based message bus carrying order events."),
    InfraNode(id="search-index", name="Search Index", kind="search",
              description="Managed full-text search cluster behind catalog."),
    InfraNode(id="dns", name="StackDNS (internal resolver)", kind="dns",
              description="Cluster DNS resolver; search-domain configuration is platform-owned."),
    InfraNode(id="payvault", name="PayVault", kind="external", external=True,
              description="Fictional external card processor reached over mTLS."),
    InfraNode(id="mailrelay", name="Mailrelay", kind="external", external=True,
              description="Fictional external email/SMS API reached via StackDNS name api.mailrelay.internal."),
]


def _edge(source: str, target: str, protocol: str, criticality: str, timeout_ms: int) -> DependencyEdge:
    return DependencyEdge(
        id=f"EDGE-{source}-{target}", source=source, target=target, protocol=protocol,
        criticality=criticality, timeout_ms=timeout_ms,  # type: ignore[arg-type]
    )


EDGES: list[DependencyEdge] = [
    _edge("edge-gateway", "identity", "http", "critical", 500),
    _edge("edge-gateway", "catalog", "http", "critical", 2000),
    _edge("edge-gateway", "cart", "http", "critical", 1500),
    _edge("edge-gateway", "checkout", "http", "critical", 5000),
    _edge("identity", "identity-db", "postgres", "critical", 1000),
    _edge("identity", "session-cache", "redis", "critical", 200),
    _edge("catalog", "catalog-db", "postgres", "critical", 1000),
    _edge("catalog", "search-index", "http", "critical", 2000),
    _edge("cart", "session-cache", "redis", "critical", 200),
    _edge("cart", "pricing", "grpc", "important", 300),
    _edge("checkout", "cart", "grpc", "critical", 500),
    _edge("checkout", "inventory", "grpc", "critical", 800),
    _edge("checkout", "pricing", "grpc", "critical", 300),
    _edge("checkout", "payments", "grpc", "critical", 6000),
    _edge("checkout", "orders", "grpc", "critical", 1000),
    _edge("checkout", "identity", "grpc", "important", 300),
    _edge("payments", "payments-db", "postgres", "critical", 1000),
    _edge("payments", "payvault", "https-mtls", "critical", 3000),
    _edge("inventory", "inventory-db", "postgres", "critical", 1000),
    _edge("orders", "orders-db", "postgres", "critical", 1000),
    _edge("orders", "event-bus", "kafka-like", "critical", 2000),
    _edge("fulfillment-worker", "event-bus", "kafka-like", "critical", 30000),
    _edge("fulfillment-worker", "orders-db", "postgres", "important", 1000),
    _edge("fulfillment-worker", "notifications", "http", "best_effort", 2000),
    _edge("notifications", "event-bus", "kafka-like", "important", 30000),
    _edge("notifications", "dns", "dns", "critical", 200),
    _edge("notifications", "mailrelay", "https", "critical", 4000),
    _edge("reporting", "orders-db", "postgres", "important", 60000),
    _edge("reporting", "warehouse", "sql", "critical", 120000),
]

SERVICE_IDS: frozenset[str] = frozenset(s.id for s in SERVICES)
NODE_IDS: frozenset[str] = frozenset(n.id for n in NODES)
ALL_NODE_IDS: frozenset[str] = SERVICE_IDS | NODE_IDS
