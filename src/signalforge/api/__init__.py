"""The SignalForge local HTTP API.

Depends on orchestration, MCP, evidence, reports, audit and events. Nothing in those packages
imports this one, and this package never imports ``signalforge.scenarios`` or ``signalforge.evals``:
the browser is on the investigator's side of the ground-truth firewall.
"""

from signalforge.api.app import create_app
from signalforge.api.config import ApiSettings
from signalforge.api.services import AppServices

__all__ = ["ApiSettings", "AppServices", "create_app"]
