"""Procedural signals: metrics and logs computed on demand, deterministically.

No time series is stored. ``value(node, metric, dimension, t)`` is a pure
function of the world snapshot (baseline profile + active effects + seeded
noise), so any window can be queried at any resolution and always produces the
same numbers. Logs are generated per five-minute bucket from a seeded RNG.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import UTC, datetime, timedelta

from signalforge.config import ServerLimits, ensure_utc
from signalforge.world.models import (
    LogEffect,
    LogEntry,
    MetricEffect,
    MetricPoint,
    StatusEffect,
    WorldSnapshot,
)


class MetricQueryError(ValueError):
    """The request cannot be answered as posed (unknown metric, bad dimension, ...)."""


LATENCY_GROUP = ("latency_p50", "latency_p95", "latency_p99")
P95_FACTOR = 2.4
P99_FACTOR = 4.5
CLIENT_SHARE: dict[str, float] = {"mobile": 0.45, "web": 0.55}
STATUS_CLASSES = ("2xx", "4xx", "5xx")
BASE_4XX_SHARE = 0.03

# Baseline midday values. Keys double as the set of natively available metrics.
PROFILES: dict[str, dict[str, float]] = {
    "edge-gateway": {"request_rate": 1400, "latency_p50": 45, "error_rate": 0.002, "cpu_utilization": 0.42,
                     "memory_usage": 900, "disk_usage": 0.31, "replica_count": 6, "restart_count": 0},
    "identity": {"request_rate": 320, "latency_p50": 60, "error_rate": 0.003, "cpu_utilization": 0.35,
                 "memory_usage": 700, "disk_usage": 0.28, "replica_count": 4, "restart_count": 0,
                 "db_pool_active": 8, "db_pool_max": 30, "cache_hit_ratio": 0.98},
    "catalog": {"request_rate": 650, "latency_p50": 90, "error_rate": 0.004, "cpu_utilization": 0.48,
                "memory_usage": 1200, "disk_usage": 0.35, "replica_count": 6, "restart_count": 0,
                "db_pool_active": 14, "db_pool_max": 40},
    "cart": {"request_rate": 240, "latency_p50": 55, "error_rate": 0.003, "cpu_utilization": 0.30,
             "memory_usage": 512, "disk_usage": 0.22, "replica_count": 4, "restart_count": 0,
             "cache_hit_ratio": 0.97},
    "pricing": {"request_rate": 300, "latency_p50": 35, "error_rate": 0.002, "cpu_utilization": 0.33,
                "memory_usage": 600, "disk_usage": 0.25, "replica_count": 3, "restart_count": 0},
    "checkout": {"request_rate": 90, "latency_p50": 80, "error_rate": 0.004, "cpu_utilization": 0.38,
                 "memory_usage": 800, "disk_usage": 0.27, "replica_count": 6, "restart_count": 0},
    "payments": {"request_rate": 60, "latency_p50": 190, "error_rate": 0.005, "cpu_utilization": 0.28,
                 "memory_usage": 500, "disk_usage": 0.24, "replica_count": 4, "restart_count": 0,
                 "db_pool_active": 6, "db_pool_max": 20},
    "inventory": {"request_rate": 150, "latency_p50": 40, "error_rate": 0.002, "cpu_utilization": 0.36,
                  "memory_usage": 900, "disk_usage": 0.30, "replica_count": 4, "restart_count": 0,
                  "db_pool_active": 9, "db_pool_max": 40},
    "orders": {"request_rate": 85, "latency_p50": 70, "error_rate": 0.004, "cpu_utilization": 0.34,
               "memory_usage": 850, "disk_usage": 0.29, "replica_count": 4, "restart_count": 0,
               "db_pool_active": 12, "db_pool_max": 50},
    "fulfillment-worker": {"request_rate": 40, "latency_p50": 120, "error_rate": 0.006, "cpu_utilization": 0.40,
                           "memory_usage": 600, "disk_usage": 0.21, "replica_count": 6, "restart_count": 0,
                           "queue_lag": 5, "queue_depth": 20},
    "notifications": {"request_rate": 30, "latency_p50": 150, "error_rate": 0.008, "cpu_utilization": 0.22,
                      "memory_usage": 400, "disk_usage": 0.19, "replica_count": 3, "restart_count": 0},
    "reporting": {"request_rate": 2, "latency_p50": 900, "error_rate": 0.01, "cpu_utilization": 0.55,
                  "memory_usage": 3000, "disk_usage": 0.52, "replica_count": 2, "restart_count": 0},
    # infrastructure
    "orders-db": {"cpu_utilization": 0.30, "memory_usage": 16000, "disk_usage": 0.44, "latency_p50": 3},
    "inventory-db": {"cpu_utilization": 0.28, "memory_usage": 12000, "disk_usage": 0.38, "latency_p50": 3},
    "identity-db": {"cpu_utilization": 0.20, "memory_usage": 8000, "disk_usage": 0.33, "latency_p50": 2},
    "catalog-db": {"cpu_utilization": 0.33, "memory_usage": 12000, "disk_usage": 0.41, "latency_p50": 3},
    "payments-db": {"cpu_utilization": 0.18, "memory_usage": 8000, "disk_usage": 0.30, "latency_p50": 2},
    "warehouse": {"cpu_utilization": 0.25, "memory_usage": 64000, "disk_usage": 0.61, "latency_p50": 450},
    "session-cache": {"cpu_utilization": 0.15, "memory_usage": 5200, "cache_hit_ratio": 0.97,
                      "cache_evictions": 0, "request_rate": 4200},
    "event-bus": {"cpu_utilization": 0.35, "queue_depth": 20, "queue_lag": 5, "redelivery_count": 0,
                  "error_rate": 0.0005, "request_rate": 300},
    "search-index": {"cpu_utilization": 0.45, "memory_usage": 24000, "request_rate": 800,
                     "error_rate": 0.001, "latency_p50": 25},
    "dns": {"request_rate": 5000, "error_rate": 0.0001, "latency_p50": 1},
}

POOL_CLIENTS: dict[str, dict[str, float]] = {
    "orders-db": {"orders": 12, "fulfillment-worker": 4, "reporting": 0},
    "inventory-db": {"inventory": 9, "checkout": 0},
    "identity-db": {"identity": 8},
    "catalog-db": {"catalog": 14},
    "payments-db": {"payments": 6},
    "session-cache": {"identity": 40, "cart": 60},
    "warehouse": {"reporting": 6},
}

INTEGER_METRICS = {"replica_count", "restart_count", "db_pool_active", "db_pool_max", "queue_depth",
                   "connections_by_client", "cache_evictions", "http_status_count", "redelivery_count"}

NOISE_AMPLITUDE: dict[str, float] = {
    "request_rate": 0.04, "error_rate": 0.15, "latency_p50": 0.06, "latency_p95": 0.06, "latency_p99": 0.08,
    "cpu_utilization": 0.03, "memory_usage": 0.02, "disk_usage": 0.0, "db_pool_active": 0.10, "db_pool_max": 0.0,
    "cache_hit_ratio": 0.004, "cache_evictions": 0.2, "queue_lag": 0.10, "queue_depth": 0.10,
    "replica_count": 0.0, "restart_count": 0.0, "dependency_latency_p95": 0.06, "http_status_count": 0.04,
    "redelivery_count": 0.2, "connections_by_client": 0.05,
}

DIMENSION_REQUIRED: dict[str, str] = {
    "dependency_latency_p95": "dependency",
    "http_status_count": "status_class",
    "connections_by_client": "client",
}
CLIENT_DIMENSION_METRICS = {"request_rate", "error_rate", "latency_p50", "latency_p95", "latency_p99"}


def _unit(*parts: object) -> float:
    """Deterministic pseudo-random number in [0, 1) derived from the parts."""
    digest = hashlib.blake2b(":".join(str(p) for p in parts).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


class SignalEngine:
    def __init__(self, snapshot: WorldSnapshot, limits: ServerLimits | None = None) -> None:
        self.snapshot = snapshot
        self.limits = limits or ServerLimits()
        self.seed = snapshot.seed
        self._services = {s.id: s for s in snapshot.services}
        self._nodes = {n.id: n for n in snapshot.nodes}
        self._edges_out: dict[str, list[str]] = {}
        for edge in snapshot.edges:
            self._edges_out.setdefault(edge.source, []).append(edge.target)
        self._metric_effects: dict[str, list[MetricEffect]] = {}
        for eff in snapshot.metric_effects:
            self._metric_effects.setdefault(eff.node, []).append(eff)
        self._log_effects: dict[str, list[LogEffect]] = {}
        for leff in snapshot.log_effects:
            self._log_effects.setdefault(leff.node, []).append(leff)
        self._status_effects: dict[str, list[StatusEffect]] = {}
        for seff in snapshot.status_effects:
            self._status_effects.setdefault(seff.node, []).append(seff)

    # ------------------------------------------------------------------ catalogue

    def is_known(self, node: str) -> bool:
        return node in self._services or node in self._nodes

    def is_external(self, node: str) -> bool:
        info = self._nodes.get(node)
        return bool(info and info.external)

    def known_nodes(self) -> list[str]:
        return sorted([*self._services, *self._nodes])

    def available_metrics(self, node: str) -> list[str]:
        if not self.is_known(node):
            raise MetricQueryError(f"Unknown node {node!r}. Known nodes: {', '.join(self.known_nodes())}.")
        if self.is_external(node):
            return []
        profile = PROFILES.get(node, {})
        metrics = set(profile)
        if "latency_p50" in profile:
            metrics.update(LATENCY_GROUP)
        if node in self._services and "request_rate" in profile and "error_rate" in profile:
            metrics.add("http_status_count")
        if self._edges_out.get(node):
            metrics.add("dependency_latency_p95")
        if node in POOL_CLIENTS:
            metrics.add("connections_by_client")
        return sorted(metrics)

    def dimension_options(self, node: str, metric: str) -> list[str]:
        if metric == "dependency_latency_p95":
            return [f"dependency={t}" for t in sorted(self._edges_out.get(node, []))]
        if metric == "http_status_count":
            return [f"status_class={c}" for c in STATUS_CLASSES]
        if metric == "connections_by_client":
            return [f"client={c}" for c in sorted(POOL_CLIENTS.get(node, {}))]
        if metric in CLIENT_DIMENSION_METRICS and node in self._services:
            return [f"client={c}" for c in sorted(CLIENT_SHARE)]
        return []

    def validate(self, node: str, metric: str, dimension: str | None) -> None:
        available = self.available_metrics(node)
        if self.is_external(node):
            raise MetricQueryError(
                f"Metrics are not collected for external dependency {node!r}. Query "
                f"dependency_latency_p95 on a calling service with dimension=dependency={node}, "
                f"or use get_service_health for its status feed."
            )
        if metric not in available:
            raise MetricQueryError(
                f"Metric {metric!r} is not available for {node!r}. Available: {', '.join(available)}."
            )
        options = self.dimension_options(node, metric)
        required = DIMENSION_REQUIRED.get(metric)
        if dimension is None:
            if required:
                raise MetricQueryError(
                    f"Metric {metric!r} requires a dimension. Options: {', '.join(options)}."
                )
            return
        if "=" not in dimension:
            raise MetricQueryError("dimension must be given as key=value, for example dependency=pricing.")
        if dimension not in options:
            if not options:
                raise MetricQueryError(f"Metric {metric!r} on {node!r} does not accept a dimension.")
            raise MetricQueryError(
                f"Unknown dimension {dimension!r} for {metric!r} on {node!r}. Options: {', '.join(options)}."
            )

    # ------------------------------------------------------------------ metrics

    def _active(self, eff: MetricEffect | LogEffect | StatusEffect, t: datetime) -> bool:
        return eff.start <= t and (eff.end is None or t < eff.end)

    @staticmethod
    def _apply(eff: MetricEffect, base: float, t: datetime) -> float:
        target = eff.absolute if eff.absolute is not None else base * (eff.multiplier or 1.0)
        if eff.shape == "step":
            return target
        if eff.shape == "ramp":
            assert eff.ramp_end is not None
            span = (eff.ramp_end - eff.start).total_seconds()
            if span <= 0 or t >= eff.ramp_end:
                return target
            frac = (t - eff.start).total_seconds() / span
            return base + (target - base) * frac
        if eff.shape == "sawtooth":
            assert eff.period_seconds and eff.low is not None and eff.high is not None
            elapsed = (t - eff.start).total_seconds()
            frac = (elapsed % eff.period_seconds) / eff.period_seconds
            return eff.low + (eff.high - eff.low) * frac
        raise MetricQueryError(f"unsupported effect shape {eff.shape!r}")

    def _effects_for(self, node: str, metric: str, dimension: str | None) -> list[MetricEffect]:
        group = "latency" if metric in LATENCY_GROUP else None
        return [
            e for e in self._metric_effects.get(node, [])
            if (e.metric == metric or (group and e.metric == group)) and e.dimension == dimension
        ]

    def _diurnal(self, t: datetime, strength: float) -> float:
        hour = t.hour + t.minute / 60
        factor = 1 + strength * math.sin(2 * math.pi * (hour - 9) / 24)
        if t.weekday() >= 5:
            factor *= 1 - 0.15 * strength / 0.35 if strength else 1
        return factor

    def _baseline(self, node: str, metric: str, dimension: str | None, t: datetime) -> float:
        profile = PROFILES.get(node, {})
        client = None
        if dimension and dimension.startswith("client=") and metric in CLIENT_DIMENSION_METRICS:
            client = dimension.split("=", 1)[1]
        if metric == "request_rate":
            base = profile["request_rate"] * self._diurnal(t, 0.35)
            return base * CLIENT_SHARE[client] if client else base
        if metric in ("latency_p50", "latency_p95", "latency_p99"):
            factor = {"latency_p50": 1.0, "latency_p95": P95_FACTOR, "latency_p99": P99_FACTOR}[metric]
            return profile["latency_p50"] * factor * self._diurnal(t, 0.05)
        if metric == "cpu_utilization":
            return profile["cpu_utilization"] * self._diurnal(t, 0.12)
        if metric == "dependency_latency_p95":
            target = dimension.split("=", 1)[1] if dimension else ""
            target_p50 = PROFILES.get(target, {}).get("latency_p50", 60)
            return target_p50 * P95_FACTOR * 1.05 + 5.0
        if metric == "connections_by_client":
            client_name = dimension.split("=", 1)[1] if dimension else ""
            return POOL_CLIENTS.get(node, {}).get(client_name, 0.0)
        if metric == "http_status_count":
            status_class = dimension.split("=", 1)[1] if dimension else "2xx"
            rr = self._value(node, "request_rate", None, t, noisy=False)
            err = self._value(node, "error_rate", None, t, noisy=False)
            share = {"2xx": max(0.0, 1 - err - BASE_4XX_SHARE), "4xx": BASE_4XX_SHARE, "5xx": err}[status_class]
            return rr * 60 * share
        return float(profile.get(metric, 0.0))

    def _value(self, node: str, metric: str, dimension: str | None, t: datetime, *, noisy: bool) -> float:
        value = self._baseline(node, metric, dimension, t)
        for eff in self._effects_for(node, metric, dimension):
            if self._active(eff, t):
                value = self._apply(eff, value, t)
        if noisy:
            amp = NOISE_AMPLITUDE.get(metric, 0.0)
            if amp:
                minute_bucket = int(t.timestamp() // 60)
                value *= 1 + amp * (2 * _unit(self.seed, node, metric, dimension, minute_bucket) - 1)
        return value

    def value(self, node: str, metric: str, dimension: str | None, t: datetime) -> float:
        self.validate(node, metric, dimension)
        return self._round(metric, self._value(node, metric, dimension, ensure_utc(t), noisy=True))

    @staticmethod
    def _round(metric: str, value: float) -> float:
        if metric in INTEGER_METRICS:
            return float(round(value))
        if metric in ("error_rate", "cache_hit_ratio", "cpu_utilization", "disk_usage"):
            return round(max(0.0, min(1.0, value)), 4)
        return round(value, 1)

    def series(self, node: str, metric: str, dimension: str | None, start: datetime, end: datetime,
               step_seconds: int) -> list[MetricPoint]:
        self.validate(node, metric, dimension)
        start, end = ensure_utc(start), ensure_utc(end)
        points: list[MetricPoint] = []
        t = start
        while t < end:
            points.append(MetricPoint(timestamp=t, value=self._round(metric, self._value(node, metric, dimension, t, noisy=True))))
            t += timedelta(seconds=step_seconds)
        return points

    # ------------------------------------------------------------------ status feeds

    def status_feed(self, node: str, at: datetime) -> tuple[str, str]:
        at = ensure_utc(at)
        for eff in self._status_effects.get(node, []):
            if self._active(eff, at):
                return eff.status, eff.detail
        return "operational", "All systems operational"

    # ------------------------------------------------------------------ logs

    def log_entries(self, node: str, start: datetime, end: datetime) -> list[LogEntry]:
        """All log lines for a node in [start, end), sorted by timestamp then id."""
        if not self.is_known(node):
            raise MetricQueryError(f"Unknown node {node!r}. Known nodes: {', '.join(self.known_nodes())}.")
        if self.is_external(node):
            raise MetricQueryError(f"Logs are not collected for external dependency {node!r}.")
        start, end = ensure_utc(start), ensure_utc(end)
        bucket = timedelta(seconds=self.limits.log_bucket_seconds)
        first = datetime.fromtimestamp(
            math.floor(start.timestamp() / bucket.total_seconds()) * bucket.total_seconds(), tz=UTC
        )
        entries: list[LogEntry] = []
        cursor = first
        while cursor < end:
            entries.extend(self._bucket_entries(node, cursor, bucket))
            cursor += bucket
        return [e for e in entries if start <= e.timestamp < end]

    def _bucket_entries(self, node: str, bucket_start: datetime, bucket: timedelta) -> list[LogEntry]:
        rng = random.Random(f"{self.seed}:{node}:{bucket_start.isoformat()}")
        raw: list[tuple[float, str, str, str]] = []  # (offset, level, message, pattern)
        lo, hi = (6, 10) if node in self._services else (2, 4)
        templates = BASE_TEMPLATES.get(node) or (SERVICE_TEMPLATES if node in self._services else INFRA_TEMPLATES)
        weights = [w for _, _, w, _ in templates]
        for _ in range(rng.randint(lo, hi)):
            level, template, _, pattern = rng.choices(templates, weights=weights)[0]
            raw.append((rng.uniform(0, bucket.total_seconds()), level, _render(template, rng, node), pattern))
        bucket_end = bucket_start + bucket
        for eff in self._log_effects.get(node, []):
            seg_start = max(bucket_start, eff.start)
            seg_end = min(bucket_end, eff.end) if eff.end else bucket_end
            if seg_end <= seg_start:
                continue
            overlap = (seg_end - seg_start) / bucket
            whole, frac = divmod(eff.rate_per_bucket * overlap, 1.0)
            count = int(whole) + (1 if rng.random() < frac else 0)
            lo = (seg_start - bucket_start).total_seconds()
            hi = (seg_end - bucket_start).total_seconds()
            for _ in range(count):
                raw.append((rng.uniform(lo, hi), eff.level, _render(eff.template, rng, node), eff.pattern_id))
        raw.sort(key=lambda r: r[0])
        stamp = bucket_start.strftime("%Y%m%dT%H%M")
        return [
            LogEntry(id=f"LOG-{node}-{stamp}-{i:03d}", node=node,
                     timestamp=bucket_start + timedelta(seconds=round(off, 3)),
                     level=level, message=message, pattern_id=pattern)  # type: ignore[arg-type]
            for i, (off, level, message, pattern) in enumerate(raw, start=1)
        ]


# ------------------------------------------------------------------ log templates

SERVICE_TEMPLATES: list[tuple[str, str, int, str]] = [
    ("INFO", "request completed method=GET path={path} status=200 duration_ms={ms}", 6, "http.request_ok"),
    ("INFO", "request completed method=POST path={path} status=201 duration_ms={ms}", 3, "http.request_ok"),
    ("WARN", "slow request path={path} duration_ms={slow_ms} status=200", 1, "http.slow_request"),
    ("INFO", "health check ok upstreams=all", 1, "health.ok"),
    ("DEBUG", "connection pool stats active={n} idle={n} waiting=0", 1, "pool.stats"),
]

INFRA_TEMPLATES: list[tuple[str, str, int, str]] = [
    ("INFO", "checkpoint complete duration_ms={ms}", 2, "infra.checkpoint"),
    ("INFO", "replication healthy lag_ms={ms}", 1, "infra.replication_ok"),
    ("DEBUG", "connections active={n}{n} idle={n}", 1, "infra.connections"),
]

BASE_TEMPLATES: dict[str, list[tuple[str, str, int, str]]] = {
    "checkout": [
        *SERVICE_TEMPLATES,
        ("INFO", "order submitted order=ORD-{id} items={n} total_cents={big}00", 3, "checkout.order_submitted"),
    ],
    "payments": [
        *SERVICE_TEMPLATES,
        ("INFO", "authorization approved txn=TXN-{id} processor=payvault latency_ms={ms}", 3, "payments.auth_ok"),
    ],
    "fulfillment-worker": [
        ("INFO", "processed order.placed order=ORD-{id} shipment=SHP-{id} lag_s={n}", 6, "worker.processed"),
        ("INFO", "health check ok consumer_group=fulfillment", 1, "health.ok"),
        ("DEBUG", "poll returned {n} messages", 2, "worker.poll"),
    ],
    "notifications": [
        *SERVICE_TEMPLATES,
        ("INFO", "delivered channel=email message_id=MSG-{id} provider=mailrelay latency_ms={ms}", 3, "notifications.delivered"),
    ],
    "reporting": [
        ("INFO", "etl stage complete stage={stage} rows={big}00 duration_ms={slow_ms}", 4, "reporting.etl_stage"),
        ("INFO", "health check ok", 1, "health.ok"),
    ],
    "session-cache": [
        ("INFO", "keyspace stats keys={big}000 hit_ratio=0.97 evicted=0", 2, "cache.keyspace"),
        ("DEBUG", "rdb snapshot complete duration_ms={ms}", 1, "cache.snapshot"),
    ],
    "event-bus": [
        ("INFO", "partition rebalance none; consumer groups healthy lag<{n}0", 2, "bus.healthy"),
        ("DEBUG", "log segment rolled partition={n}", 1, "bus.segment"),
    ],
    "dns": [
        ("INFO", "resolver stats qps={big}0 nxdomain_ratio=0.0001", 2, "dns.stats"),
    ],
    "search-index": [
        ("INFO", "query served latency_ms={ms} hits={big}", 3, "search.query_ok"),
        ("DEBUG", "segment merge complete shard={n}", 1, "search.merge"),
    ],
}

PATHS = ["/v1/products", "/v1/cart", "/v1/checkout/submit", "/v1/login", "/v1/orders", "/v1/search",
         "/v1/prices/quote", "/v1/health", "/v1/inventory/reserve", "/v1/notify"]
STAGES = ["order_lines", "customers", "inventory_snapshot", "returns", "shipments"]
UPSTREAMS = ["checkout", "catalog", "cart", "identity"]


def _render(template: str, rng: random.Random, node: str) -> str:
    values = {
        "ms": rng.randint(12, 380),
        "slow_ms": rng.randint(800, 2600),
        "n": rng.randint(1, 9),
        "big": rng.randint(100, 999),
        "id": f"{rng.randrange(16**6):06x}",
        "path": rng.choice(PATHS),
        "stage": rng.choice(STAGES),
        "upstream": rng.choice(UPSTREAMS),
        "azab": rng.choice(["a", "b"]),
    }
    try:
        return template.format_map(values)
    except (KeyError, IndexError, ValueError):
        return template
