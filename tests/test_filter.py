import asyncio
import json

import httpx
import pytest

from stopslop.config import Settings
from stopslop.policy import Policy
from stopslop.proxy import create_app
from stopslop.router import PolicyRouter


def payload(text):
    return {"messages": [{"role": "user", "content": text}]}


def test_mapping_repeats_collisions_overlaps_and_isolation():
    policy = Policy(Settings(main_key="secret", policy="filter"))
    phone = "+48 555 555 555"
    bank = "PL61 1090 1014 0000 0712 1981 2874"
    text = f"[ANON:phone:1] Jan Kowalski {phone} {phone} {bank}"
    original = payload(text)
    original["messages"].append({"role": "assistant", "content": phone})
    route = policy.route(original)
    assert original["messages"][0]["content"] == text
    assert len(route.replacements) == 3
    token = next(t for t, value in route.replacements.items() if value == phone)
    assert route.payload["messages"][0]["content"].count(token) == 2
    assert route.payload["messages"][1]["content"] == token
    assert route.restore(route.payload)["messages"] == original["messages"]
    assert bank not in json.dumps(route.payload)
    assert phone not in repr(route)
    assert policy.route(payload("Hello")).replacements == {}
    second = policy.route(payload("Anna Nowak"))
    assert phone not in second.replacements.values()


@pytest.mark.parametrize("transport_kind", ["router", "proxy"])
def test_filter_round_trip_all_choices_and_followup(transport_kind):
    configuration = Settings(main_key="secret", policy="filter")
    originals = ["+48 555 555 555", "+48 512 345 678", "Jan Kowalski", "02070803628"]
    text = " / ".join(originals)
    calls = []

    def upstream(request):
        outbound = json.loads(request.content)
        calls.append(outbound)
        assert all(value not in request.content.decode() for value in originals)
        anonymous = outbound["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [
            {"message": {"content": anonymous, "reasoning_content": anonymous}},
            {"message": {"content": None}},
        ], "usage": {"total_tokens": 12}}, headers={"x-upstream": "preserved"})

    if transport_kind == "router":
        with httpx.Client(transport=PolicyRouter(configuration, httpx.MockTransport(upstream))) as client:
            response = client.post("https://local/v1/chat/completions", json=payload(text))
            followup = payload(text)
            followup["messages"].append({"role": "assistant", "content": response.json()["choices"][0]["message"]["content"]})
            client.post("https://local/v1/chat/completions", json=followup)
    else:
        async def run():
            app = create_app(configuration, httpx.MockTransport(upstream))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as client:
                response = await client.post("/v1/chat/completions", json=payload(text))
                followup = payload(text)
                followup["messages"].append({"role": "assistant", "content": response.json()["choices"][0]["message"]["content"]})
                await client.post("/v1/chat/completions", json=followup)
                return response
        response = asyncio.run(run())
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"] == {"content": text, "reasoning_content": text}
    assert response.json()["choices"][1]["message"]["content"] is None
    assert response.json()["usage"]["total_tokens"] == 12
    assert response.headers["X-StopSlop-Action"] == "filter"
    assert len(calls) == 2
    assert calls[1]["messages"][0]["content"] == calls[1]["messages"][1]["content"]


def test_restoration_does_not_cascade():
    from stopslop.policy import Route
    route = Route({}, "", "", "filter", [], {"[A]": "[B]", "[B]": "original"})
    assert route.restore({"content": "[A] [B] [unknown]"}) == {"content": "[B] original [unknown]"}


def test_invalid_filter_response_fails_locally():
    router = PolicyRouter(Settings(main_key="secret", policy="filter"),
                          httpx.MockTransport(lambda request: httpx.Response(200, text="invalid")))
    with httpx.Client(transport=router) as client:
        response = client.post("https://local/v1/chat/completions", json=payload("Jan Kowalski"))
    assert response.status_code == 502
