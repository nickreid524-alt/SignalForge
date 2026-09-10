"""Shared vocabulary of failure categories.

This is domain vocabulary, not ground truth: a report may name any of these
categories, and scenario specifications use the same names so evaluations can
compare them. Nothing here says which category applies to which incident.
"""

from __future__ import annotations

from typing import Literal, get_args

CauseCategory = Literal[
    "deployment_regression",
    "db_pool_exhaustion",
    "memory_leak",
    "disk_saturation",
    "queue_backlog",
    "certificate_expiry",
    "dns_failure",
    "cache_failure",
    "dependency_latency",
    "config_mistake",
    "auth_failure",
    "traffic_spike",
    "crash_loop",
    "third_party_degradation",
    "inconclusive",
]

CAUSE_CATEGORIES: tuple[str, ...] = tuple(get_args(CauseCategory))
