"""Keep configured auxiliary fallback precedence without patching the discovery rung."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from agent import auxiliary_client as aux


def test_auto_auxiliary_uses_configured_fallback_before_builtin_discovery(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "auxiliary:\n  compression:\n    provider: auto\n"
        "    fallback_chain:\n      - provider: openrouter\n        model: fallback-model\n"
    )
    primary = MagicMock()
    primary.base_url = "https://primary.example/v1"
    failure = Exception("Payment Required: insufficient credits")
    failure.status_code = 402
    primary.chat.completions.create.side_effect = failure
    fallback = MagicMock()
    fallback.base_url = "https://openrouter.ai/api/v1"
    fallback.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="configured fallback"))]
    )
    monkeypatch.setattr(aux, "_get_cached_client", lambda *a, **k: (primary, "primary-model"))
    monkeypatch.setattr(aux, "_resolve_task_provider_model", lambda *a, **k: ("auto", None, None, None, None))
    monkeypatch.setattr(aux, "resolve_provider_client", lambda *a, **k: (fallback, "fallback-model"))
    discovery = MagicMock(side_effect=AssertionError("built-in discovery must follow the configured chain"))
    monkeypatch.setattr(aux, "_try_payment_fallback", discovery)

    response = aux.call_llm(task="compression", messages=[{"role": "user", "content": "summarize"}])

    assert response.choices[0].message.content == "configured fallback"
    fallback.chat.completions.create.assert_called_once()
    discovery.assert_not_called()
