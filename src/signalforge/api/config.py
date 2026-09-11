"""API settings. Everything the browser must not choose is decided here, from the environment.

Defaults are the safe ones: loopback only, no CORS origin, live providers disabled, conservative
concurrency. A deployment loosens them deliberately; a request can never loosen them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

ENV_HOST = "SIGNALFORGE_API_HOST"
ENV_PORT = "SIGNALFORGE_API_PORT"
ENV_CORS = "SIGNALFORGE_API_CORS_ORIGINS"
ENV_MAX_CONCURRENCY = "SIGNALFORGE_API_MAX_CONCURRENCY"
ENV_MAX_QUEUED = "SIGNALFORGE_API_MAX_QUEUED"
ENV_ALLOW_LIVE = "SIGNALFORGE_API_ALLOW_LIVE"
ENV_TRACE_DB = "SIGNALFORGE_API_TRACE_DB"
ENV_EVENT_DB = "SIGNALFORGE_API_EVENT_DB"

#: Requests are tiny JSON documents. Anything larger is a mistake or an attack.
MAX_BODY_BYTES = 16 * 1024


def _int(env: Mapping[str, str], name: str, default: int, *, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(env[name])))
    except (KeyError, TypeError, ValueError):
        return default


def _flag(env: Mapping[str, str], name: str) -> bool:
    return (env.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ApiSettings:
    host: str = "127.0.0.1"
    port: int = 8765
    #: Exact allowed browser origins. Never "*": a wildcard plus live-provider access would let any
    #: page on the internet spend the operator's API budget.
    cors_origins: tuple[str, ...] = ()
    max_concurrency: int = 2
    max_queued: int = 16
    #: Live (paid) providers must be enabled by the operator at startup. A request cannot enable them.
    allow_live_providers: bool = False
    trace_db: str = "runs/signalforge.sqlite"
    event_db: str = "runs/signalforge-events.sqlite"
    max_body_bytes: int = MAX_BODY_BYTES
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ApiSettings:
        env = os.environ if env is None else env
        origins = tuple(o.strip() for o in (env.get(ENV_CORS) or "").split(",") if o.strip() and o.strip() != "*")
        return cls(
            host=env.get(ENV_HOST) or "127.0.0.1",
            port=_int(env, ENV_PORT, 8765, low=1, high=65535),
            cors_origins=origins,
            max_concurrency=_int(env, ENV_MAX_CONCURRENCY, 2, low=1, high=8),
            max_queued=_int(env, ENV_MAX_QUEUED, 16, low=1, high=256),
            allow_live_providers=_flag(env, ENV_ALLOW_LIVE),
            trace_db=env.get(ENV_TRACE_DB) or "runs/signalforge.sqlite",
            event_db=env.get(ENV_EVENT_DB) or "runs/signalforge-events.sqlite",
        )

    @property
    def binds_publicly(self) -> bool:
        return self.host not in {"127.0.0.1", "localhost", "::1"}
