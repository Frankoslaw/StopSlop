"""Exercise real OpenAI SDK requests through the gateway with mock upstreams."""
import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI, PermissionDeniedError
from stopslop.config import Settings
from stopslop_proxy import create_app


@pytest.mark.parametrize("provider,url,model,key", [
    ("nvidia", "https://integrate.api.nvidia.com/v1", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", "secret"),
    ("openai", "https://api.openai.com/v1", "gpt-4.1-mini", "secret"),
    ("ollama", "http://127.0.0.1:11434/v1", "llama3.2", "ollama"),
])
@pytest.mark.parametrize("action", ["filter", "block"])
def test_sdk_gateway_backends(provider, url, model, key, action):
    calls = []
    def upstream(request):
        calls.append(request)
        payload = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "completion", "object": "chat.completion", "created": 0,
            "model": payload["model"], "choices": [{"index": 0,
                "message": {"role": "assistant", "content": payload["messages"][0]["content"]},
                "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        })
    settings = Settings(provider=provider, model=model if provider == "ollama" else None,
                        key="" if provider == "ollama" else key, deterministic=True, policy=action)
    assert settings.base_url == url
    app = create_app(settings, httpx.MockTransport(upstream))
    async def run():
        async with app.router.lifespan_context(app):
            async with AsyncOpenAI(base_url="http://gateway/v1", api_key="client", max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app))) as client:
                args = dict(model="client-model", messages=[{"role": "user", "content": "Email alice@example.com"}], max_tokens=32)
                if action == "block":
                    with pytest.raises(PermissionDeniedError):
                        await client.chat.completions.create(**args)
                else:
                    result = await client.chat.completions.create(**args)
                    assert result.choices[0].message.content == "Email alice@example.com"
                    assert result.model == model
                    assert result.usage.total_tokens == 20
    asyncio.run(run())
    if action == "block":
        assert not calls
    else:
        assert len(calls) == 1
        assert str(calls[0].url) == url + "/chat/completions"
        assert calls[0].headers["authorization"] == "Bearer " + key
        assert "alice@example.com" not in calls[0].content.decode()


def test_provider_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("STOPSLOP_PROVIDER", "openai")
    assert Settings.load(str(tmp_path / "missing.env")).base_url == "https://api.openai.com/v1"
    custom = Settings(provider="openai", base_url="https://custom.example/v1", model="custom")
    assert custom.base_url == "https://custom.example/v1" and custom.model == "custom"
    with pytest.raises(ValueError, match="provider"):
        Settings(provider="unknown")
    with pytest.raises(ValueError, match="installed model"):
        Settings(provider="ollama")


def test_chat_recording_defaults_and_environment(tmp_path, monkeypatch):
    assert Settings().log_chats is True
    monkeypatch.setenv("STOPSLOP_LOG_CHATS", "false")
    assert Settings.load(str(tmp_path / "missing.env")).log_chats is False
    assert Settings.load(str(tmp_path / "missing.env"), log_chats=True).log_chats is True


@pytest.mark.parametrize("provider,model,host,key", [
    ("nvidia", None, "integrate.api.nvidia.com", "provider-secret"),
    ("openai", None, "api.openai.com", "provider-secret"),
    ("ollama", "llama3.2", "127.0.0.1", "ollama"),
])
def test_direct_openai_sdk_transport(provider, model, host, key):
    from openai import OpenAI
    from stopslop.router import PolicyRouter
    calls = []
    settings = Settings(provider=provider, model=model, key="" if provider == "ollama" else key,
                        deterministic=True, policy="filter")
    def upstream(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert request.url.host == host
        assert request.headers["authorization"] == "Bearer " + key
        assert payload["model"] == settings.model
        assert "alice@example.com" not in payload["messages"][0]["content"]
        return httpx.Response(200, json={"id": "completion", "object": "chat.completion", "created": 0,
            "model": payload["model"], "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": payload["messages"][0]["content"]}}]})
    router = PolicyRouter(settings, httpx.MockTransport(upstream))
    with OpenAI(base_url=settings.base_url, api_key="client", max_retries=0,
                http_client=httpx.Client(transport=router)) as client:
        reply = client.chat.completions.create(model=settings.model,
            messages=[{"role": "user", "content": "Email alice@example.com"}], max_tokens=32)
        assert reply.choices[0].message.content == "Email alice@example.com"
    assert len(calls) == 1


def test_generic_provider_configuration_precedence(tmp_path, monkeypatch):
    env_file = tmp_path / "settings.env"
    env_file.write_text("STOPSLOP_PROVIDER=openai\nSTOPSLOP_BASE_URL=https://file.example/v1\nSTOPSLOP_MODEL=file-model\nSTOPSLOP_KEY=file-key\n")
    monkeypatch.setenv("STOPSLOP_MODEL", "environment-model")
    monkeypatch.setenv("STOPSLOP_NVIDIA_BASE_URL", "https://ignored.example/v1")
    settings = Settings.load(str(env_file), provider="nvidia", model="flag-model")
    assert settings.provider == "nvidia" and settings.model == "flag-model"
    assert settings.base_url == "https://file.example/v1" and settings.key == "file-key"
    assert Settings.load(str(env_file)).model == "environment-model"
    assert Settings.load(str(tmp_path / "missing.env")).base_url != "https://file.example/v1"


def test_classifier_belongs_only_to_runtime_configuration(tmp_path):
    from stopslop.policy_file import PolicyFile
    for data in ({"version": 1, "classifier": "laya"},
                 {"version": 1, "budget_fallback": {"route": "local", "classifier": "laya"}}):
        with pytest.raises(ValueError):
            PolicyFile(data=data)
    settings = Settings(classifier="jev", fallback_classifier="laya")
    assert settings.classifier == "jev" and settings.fallback_classifier == "laya"


def test_shared_cli_syntax():
    import argparse
    from stopslop.config import add_settings_arguments
    parser = argparse.ArgumentParser()
    add_settings_arguments(parser)
    args = parser.parse_args(["--provider", "ollama", "--model", "installed", "--key", "secret", "--no-log-chats"])
    assert (args.provider, args.model, args.key, args.log_chats) == ("ollama", "installed", "secret", False)


def test_policy_discovery_shared_by_gateway_and_demo(tmp_path):
    (tmp_path / "policy.toml").write_text("version = 1\n")
    assert Settings.load(str(tmp_path / "missing.env")).policy_file == "policy.toml"
    assert not Settings.load(str(tmp_path / "missing.env"), policy="filter").policy_file


def test_settings_repr_omits_credentials():
    settings = Settings(key="upstream-secret", jev_key="classifier-secret", client_token="client-secret",
                        access_tokens='{"client":"bearer-secret"}')
    for secret in ("upstream-secret", "classifier-secret", "client-secret", "bearer-secret"):
        assert secret not in repr(settings)


def test_fallback_classifier_checked_before_service_starts(tmp_path):
    from stopslop.preflight import check_semantic_setup
    policy = tmp_path / "policy.toml"
    policy.write_text('version = 1\n[[rules]]\ntype = "semantic"\nname = "nda"\ndescription = "No NDA disclosure"\naction = "block"\n[budget_fallback]\nroute = "local"\n')
    settings = Settings(key="upstream", classifier="llm", fallback_classifier="jev", policy_file=str(policy))
    with pytest.raises(ValueError, match="STOPSLOP_JEV_KEY"):
        check_semantic_setup(settings)


def test_explicit_flags_override_invalid_environment_values(tmp_path, monkeypatch):
    monkeypatch.setenv("STOPSLOP_TIMEOUT", "invalid")
    monkeypatch.setenv("STOPSLOP_LOG_CHATS", "invalid")
    settings = Settings.load(str(tmp_path / "missing.env"), timeout=45, log_chats=False)
    assert settings.timeout == 45 and settings.log_chats is False
