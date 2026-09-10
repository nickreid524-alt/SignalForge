"""Shared fixtures. No network, no API keys: everything runs against the in-process synthetic world."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from signalforge.config import WorldConfig
from signalforge.mcp_client.client import OpsClient
from signalforge.mcp_server.server import create_server
from signalforge.scenarios.ground_truth import GroundTruth
from signalforge.world.generator import GeneratedWorld, generate_world
from signalforge.world.repository import WorldRepository


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def config() -> WorldConfig:
    return WorldConfig()


@pytest.fixture(scope="session")
def generated(config: WorldConfig) -> GeneratedWorld:
    return generate_world(config)


@pytest.fixture(scope="session")
def repo(generated: GeneratedWorld) -> WorldRepository:
    return WorldRepository(generated.snapshot)


@pytest.fixture(scope="session")
def ground_truth(config: WorldConfig) -> GroundTruth:
    return GroundTruth(config)


@pytest.fixture(scope="session")
def server(generated: GeneratedWorld):
    """One MCPServer instance for the whole session; each test opens its own in-memory client."""
    return create_server(snapshot=generated.snapshot)


@pytest.fixture
async def client(server) -> AsyncIterator[OpsClient]:
    async with OpsClient.in_memory(server) as ops:
        yield ops
