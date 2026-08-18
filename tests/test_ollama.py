"""Tests for the Ollama backend additions in graphify/llm.py."""
from __future__ import annotations

import pytest

from graphify.llm import detect_backend, BACKENDS, _validate_ollama_base_url

# Every environment variable that graphify.llm.detect_backend() consults when
# selecting a backend (see detect_backend + the BACKENDS dict in llm.py). Tests
# must start from a sanitized environment so host-shell API keys (e.g. a
# developer's ANTHROPIC_API_KEY / OPENAI_API_KEY / AWS_PROFILE) cannot flip which
# backend detect_backend() returns -- the root cause of the flaky
# test_detect_backend_* results observed under different shells.
DETECT_BACKEND_ENV_VARS = [
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "MOONSHOT_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AWS_PROFILE",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "OLLAMA_BASE_URL",
    "OLLAMA_HOST",
    "OLLAMA_API_KEY",
]


@pytest.fixture
def isolated_backend_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Clear all backend-selection env vars; tests set only what they need."""
    for var in DETECT_BACKEND_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/v1",
    "http://169.254.1.5:11434/v1",
    "http://metadata.google.internal/v1",
    "http://0.0.0.0:11434/v1",
])
def test_ollama_blocks_link_local_and_metadata(url):
    """Link-local / cloud-metadata Ollama targets fail closed (F3)."""
    with pytest.raises(ValueError):
        _validate_ollama_base_url(url)


def test_ollama_loopback_and_lan_do_not_raise(capsys):
    """Loopback is silent; a general LAN host warns but is allowed (F3)."""
    _validate_ollama_base_url("http://localhost:11434/v1")
    assert capsys.readouterr().err == ""
    _validate_ollama_base_url("http://192.168.1.50:11434/v1")  # LAN: warn, not raise
    assert "non-loopback" in capsys.readouterr().err


def test_ollama_alias_resolving_to_link_local_blocked(monkeypatch):
    """A hostname that RESOLVES to a link-local IP is blocked, not just literals (F3)."""
    from graphify import llm

    def fake_getaddrinfo(host, *a, **k):
        return [(2, 1, 6, "", ("169.254.169.254", 0))]  # alias -> metadata IP

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError):
        llm._validate_ollama_base_url("http://innocent-looking-host/v1")


def test_ollama_warn_false_still_hard_blocks_but_stays_quiet(capsys):
    """warn=False suppresses the LAN warning but never the metadata hard-block (F3)."""
    # LAN host with warn=False: allowed, and no warning emitted (early-gate use).
    _validate_ollama_base_url("http://192.168.1.50:11434/v1", warn=False)
    assert capsys.readouterr().err == ""
    # metadata host with warn=False: still raises.
    with pytest.raises(ValueError):
        _validate_ollama_base_url("http://169.254.169.254/v1", warn=False)


def test_ollama_in_backends():
    assert "ollama" in BACKENDS
    assert BACKENDS["ollama"]["pricing"]["input"] == 0.0
    assert BACKENDS["ollama"]["pricing"]["output"] == 0.0
    assert "max_tokens" in BACKENDS["ollama"]


def test_detect_backend_ollama(isolated_backend_env):
    isolated_backend_env.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    assert detect_backend() == "ollama"


def test_detect_backend_kimi_beats_ollama(isolated_backend_env):
    isolated_backend_env.setenv("MOONSHOT_API_KEY", "test-key")
    isolated_backend_env.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    assert detect_backend() == "kimi"


def test_detect_backend_claude_beats_ollama(isolated_backend_env):
    # ANTHROPIC_API_KEY (paid, intentional) should win over OLLAMA_BASE_URL
    # (env-driven, easy to set accidentally) -- security fix F-002/F-029.
    isolated_backend_env.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    isolated_backend_env.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert detect_backend() == "claude"


def test_detect_backend_none_without_envvars(isolated_backend_env):
    assert detect_backend() is None


def test_ollama_api_key_sentinel(monkeypatch):
    """extract_files_direct with backend=ollama and no OLLAMA_API_KEY should use sentinel 'ollama' not raise."""
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    from unittest.mock import patch
    from pathlib import Path
    import tempfile

    fake_result = {
        "nodes": [],
        "edges": [],
        "hyperedges": [],
        "input_tokens": 0,
        "output_tokens": 10,
        "finish_reason": "stop",
    }
    with patch("graphify.llm._call_openai_compat", return_value=fake_result) as mock_call:
        from graphify.llm import extract_files_direct
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("x = 1\n")
            tmp = Path(f.name)
        try:
            extract_files_direct([tmp], backend="ollama", root=tmp.parent)
            # Should have called _call_openai_compat with api_key="ollama"
            assert mock_call.called
            call_kwargs = mock_call.call_args
            api_key_used = call_kwargs.args[1] if call_kwargs.args else call_kwargs.kwargs.get("api_key", "")
            assert api_key_used == "ollama"
        finally:
            tmp.unlink(missing_ok=True)
