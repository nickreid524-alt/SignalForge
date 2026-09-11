"""The deterministic benchmark, served as a frozen artifact.

Loading a web page must never start an evaluation. A 15-scenario run takes real time, and against a
live provider it would cost real money, so these endpoints read a static JSON file produced offline
by ``signalforge eval``. There is no endpoint that triggers an evaluation; adding a live one later
would need its own explicit action and its own guard, exactly like the CLI's ``--allow-live-suite``.

This module does not import ``signalforge.evals`` or ``signalforge.scenarios``: the benchmark is data.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from signalforge.api.errors import not_found
from signalforge.api.routes.common import json_ok
from signalforge.api.schemas import EvaluationIndex

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BENCHMARKS = {"scripted": DATA_DIR / "scripted_benchmark.json"}
INDEX_NOTE = (
    "Frozen benchmark results. Requesting these endpoints does not run an evaluation and never "
    "calls a model API."
)


@lru_cache(maxsize=4)
def load_benchmark(name: str) -> dict[str, Any]:
    path = BENCHMARKS[name]
    return json.loads(path.read_text(encoding="utf-8"))


async def list_evaluations(request: Request) -> JSONResponse:
    return json_ok(EvaluationIndex(available=sorted(BENCHMARKS), note=INDEX_NOTE))


async def get_evaluation(request: Request) -> JSONResponse:
    name = request.path_params["name"]
    if name not in BENCHMARKS:
        raise not_found("benchmark", name)
    return json_ok(load_benchmark(name))
