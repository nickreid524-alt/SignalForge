"""Provider configuration, status reporting, and the CLI's live-API guards. No network."""

from __future__ import annotations

import sys

import pytest

from signalforge import cli
from signalforge.providers.base import ProviderError
from signalforge.providers.errors import ProviderFailure
from signalforge.providers.factory import (
    PROVIDER_CHOICES,
    ProviderSettings,
    create_provider,
    default_provider_name,
    provider_status,
)

NO_ENV: dict[str, str] = {}


def test_settings_from_env_and_defaults():
    settings = ProviderSettings.from_env({"SIGNALFORGE_ANTHROPIC_MODEL": "m-a", "SIGNALFORGE_OPENAI_MODEL": "m-o",
                                          "SIGNALFORGE_MAX_OUTPUT_TOKENS": "1234", "SIGNALFORGE_PROVIDER_TIMEOUT": "9.5",
                                          "SIGNALFORGE_EFFORT": "low"})
    assert (settings.anthropic_model, settings.openai_model, settings.max_output_tokens, settings.timeout_seconds, settings.effort) == \
           ("m-a", "m-o", 1234, 9.5, "low")
    bad = ProviderSettings.from_env({"SIGNALFORGE_MAX_OUTPUT_TOKENS": "lots"})
    assert bad.max_output_tokens == 8192 and bad.anthropic_model is None
    assert default_provider_name(NO_ENV) == "scripted"
    assert default_provider_name({"SIGNALFORGE_PROVIDER": "openai"}) == "openai"
    assert default_provider_name({"SIGNALFORGE_PROVIDER": "gemini"}) == "scripted"


def test_provider_status_without_credentials():
    assert PROVIDER_CHOICES == ("scripted", "replay", "anthropic", "openai")
    scripted = provider_status("scripted", env=NO_ENV)
    assert scripted.ready and not scripted.uses_live_api and scripted.sdk_installed is None
    for name in ("anthropic", "openai"):
        status = provider_status(name, env=NO_ENV)
        assert status.uses_live_api and not status.ready and status.credentials_present is False
        assert "missing:" in status.note and status.model is None
    with pytest.raises(ProviderError):
        provider_status("gemini", env=NO_ENV)


def test_create_provider_setup_errors_never_touch_the_network():
    with pytest.raises(ProviderFailure) as info:
        create_provider("anthropic", env=NO_ENV)
    assert info.value.category == "missing_model"
    with pytest.raises(ProviderFailure) as info:
        create_provider("anthropic", env={"SIGNALFORGE_ANTHROPIC_MODEL": "m"})
    assert info.value.category == "missing_api_key"
    with pytest.raises(ProviderFailure) as info:
        create_provider("openai", env={"SIGNALFORGE_OPENAI_MODEL": "m"})
    assert info.value.category == "missing_api_key"
    with pytest.raises(ProviderError):
        create_provider("replay", env=NO_ENV)


def test_cli_providers_lists_live_flags(capsys, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "SIGNALFORGE_ANTHROPIC_MODEL", "SIGNALFORGE_OPENAI_MODEL"):
        monkeypatch.delenv(var, raising=False)
    assert cli.main(["providers"]) == 0
    out = capsys.readouterr().out
    assert "USES LIVE API" in out and "scripted" in out and "anthropic" in out and "openai" in out
    assert "not an Anthropic API key" in out and "not an OpenAI API key" in out
    assert out.count("YES") >= 2 and "missing" in out


def test_cli_provider_check_guards(capsys, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "SIGNALFORGE_ANTHROPIC_MODEL"):
        monkeypatch.delenv(var, raising=False)
    assert cli.main(["provider", "check", "scripted"]) == 0
    assert "USES LIVE API: NO" in capsys.readouterr().out
    assert cli.main(["provider", "check", "anthropic", "--yes"]) == 2       # no credentials: setup error, no traceback
    assert "setup error" in capsys.readouterr().out
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key-000000")
    monkeypatch.setenv("SIGNALFORGE_ANTHROPIC_MODEL", "test-model")
    code = cli.main(["provider", "check", "anthropic"])                      # credentials but no --yes: refuse
    out = capsys.readouterr().out
    assert code == 2 and "LIVE API CONFIRMATION REQUIRED" in out and "Nothing was called" in out
    assert "sk-ant-test" not in out


def test_cli_investigate_and_eval_live_guards(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key-0000000000")
    monkeypatch.setenv("SIGNALFORGE_OPENAI_MODEL", "test-model")
    code = cli.main(["investigate", "INC-2026-0101", "--provider", "openai", "--trace-db", str(tmp_path / "t.sqlite")])
    out = capsys.readouterr().out
    assert code == 2 and "LIVE API CONFIRMATION REQUIRED" in out and "--yes" in out and "sk-test" not in out
    code = cli.main(["eval", "--provider", "openai", "--yes"])
    out = capsys.readouterr().out
    assert code == 2 and "LIVE SUITE GUARD" in out and "--allow-live-suite" in out
    monkeypatch.delenv("OPENAI_API_KEY")
    code = cli.main(["eval", "--provider", "openai", "--scenario", "SCN-01", "--yes"])
    assert code == 2 and "setup error" in capsys.readouterr().out
    # the default provider stays scripted and needs no confirmation
    code = cli.main(["eval", "--scenario", "SCN-06"])
    assert code == 0 and "USES LIVE API: NO" in capsys.readouterr().out


# ---------------------------------------------------------------- base-install behaviour (vendor SDKs absent)
#
# CI's base matrix installs only ".[dev]", so neither vendor SDK exists there. A developer machine with
# ".[dev,providers]" would never exercise that path, so these tests hide the SDK modules explicitly:
# putting None in sys.modules makes importlib report the package as absent, exactly like a base install.


def _hide_vendor_sdks(monkeypatch, *packages: str) -> None:
    for package in packages:
        monkeypatch.setitem(sys.modules, package, None)


def _configure_live_env(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key-000000")
    monkeypatch.setenv("SIGNALFORGE_ANTHROPIC_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key-0000000000")
    monkeypatch.setenv("SIGNALFORGE_OPENAI_MODEL", "test-model")


def test_provider_status_without_the_sdk_reports_the_extra_to_install(monkeypatch):
    _hide_vendor_sdks(monkeypatch, "anthropic", "openai")
    for name, extra in (("anthropic", 'signalforge[anthropic]'), ("openai", 'signalforge[openai]')):
        status = provider_status(name, env={"ANTHROPIC_API_KEY": "x", "OPENAI_API_KEY": "x",
                                            "SIGNALFORGE_ANTHROPIC_MODEL": "m", "SIGNALFORGE_OPENAI_MODEL": "m"})
        assert status.sdk_installed is False and status.sdk_version is None
        assert not status.ready and extra in status.note
        assert status.uses_live_api and status.credentials_present is True


def test_provider_check_asks_for_confirmation_before_reporting_a_missing_sdk(capsys, monkeypatch):
    _hide_vendor_sdks(monkeypatch, "anthropic")
    _configure_live_env(monkeypatch)
    # no --yes: the confirmation notice comes first, whatever is installed
    assert cli.main(["provider", "check", "anthropic"]) == 2
    out = capsys.readouterr().out
    assert "LIVE API CONFIRMATION REQUIRED" in out and "Nothing was called" in out
    assert "setup error" not in out and "sk-ant-test" not in out
    # --yes: now the missing extra is reported, still without calling anything
    assert cli.main(["provider", "check", "anthropic", "--yes"]) == 2
    out = capsys.readouterr().out
    assert "setup error" in out and 'signalforge[anthropic]' in out and "Nothing was called" in out
    assert "sk-ant-test" not in out


def test_live_suite_guard_fires_before_reporting_a_missing_sdk(capsys, monkeypatch):
    _hide_vendor_sdks(monkeypatch, "openai")
    _configure_live_env(monkeypatch)
    # multi-scenario live eval without --allow-live-suite: refused on the arguments alone
    assert cli.main(["eval", "--provider", "openai", "--yes"]) == 2
    out = capsys.readouterr().out
    assert "LIVE SUITE GUARD" in out and "--allow-live-suite" in out and "Nothing was called" in out
    assert "setup error" not in out
    # with the suite explicitly allowed, the missing extra is reported instead
    assert cli.main(["eval", "--provider", "openai", "--yes", "--allow-live-suite"]) == 2
    out = capsys.readouterr().out
    assert "setup error" in out and 'signalforge[openai]' in out
    assert "sk-test" not in out


def test_investigate_confirmation_precedes_a_missing_sdk(capsys, monkeypatch, tmp_path):
    _hide_vendor_sdks(monkeypatch, "anthropic", "openai")
    _configure_live_env(monkeypatch)
    assert cli.main(["investigate", "INC-2026-0101", "--provider", "anthropic",
                     "--trace-db", str(tmp_path / "t.sqlite")]) == 2
    out = capsys.readouterr().out
    assert "LIVE API CONFIRMATION REQUIRED" in out and "Nothing was called" in out
    assert "setup error" not in out
    # the scripted default is unaffected by the vendor SDKs being absent
    assert cli.main(["eval", "--scenario", "SCN-06"]) == 0
    assert "USES LIVE API: NO" in capsys.readouterr().out
