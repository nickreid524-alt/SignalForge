"""Synthetic operations world: SignalForge Demo Commerce.

Boundary rule: nothing in this package imports ``signalforge.scenarios``. The
world knows *what happened* (deployments, config changes, alerts, signal
effects, incidents) but never *why* in evaluation terms (root causes, decisive
evidence, red-herring labels). Those live only in ``signalforge.scenarios``.
"""

from signalforge.world.generator import GeneratedWorld, build_snapshot, generate_world
from signalforge.world.repository import WorldRepository

__all__ = ["GeneratedWorld", "WorldRepository", "build_snapshot", "generate_world"]
