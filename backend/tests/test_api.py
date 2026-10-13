import base64
import json

import httpx
import pytest
import respx

from app.config import Settings
from app.emotion import EmotionStream
from app.main import create_app

ADMIN = "admin-secret"
DEVICE = "pom_test_device_token_1234567890"


def settings(tmp_path, **kw) -> Settings:
    base = dict(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
        admin_api_key=ADMIN, bootstrap_device_token=DEVICE,
        anthropic_api_key="sk-ant-test", sarvam_api_key="sarvam-test",
        upstream_retries=1, rate_limit_per_minute=600, rate_limit_burst=50, log_level="WARNING",
    )
    base.update(kw)
    return Settings(_env_file=None, **base)  # ignore the developer's real .env


@pytest.fixture
async def client(tmp_path):
    app = create_app(settings(tmp_path))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


AUTH = {"Authorization": f"Bearer {DEVICE}"}


def anthropic_sse(text_chunks):
    lines = [
        'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":42}}}\n\n',
    ]
    for t in text_chunks:
        lines.append("event: content_block_delta\ndata: " + json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}) + "\n\n")
    lines.append('event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":17}}\n\n')
    lines.append('event: message_stop\ndata: {"type":"message_stop"}\n\n')
    return "".join(lines)


def parse_sse(body: str):
    out = []
    for block in body.strip().split("\n\n"):
        ev = data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
        out.append((ev, data))
    return out


# ------------------------------------------------------------------ unit
def test_emotion_stream_splits_tags_across_chunks():
    p = EmotionStream()
    pieces = []
    for chunk in ["[emo", "tion:hap", "py] Hello ", "there! [emotion:", "excited]Wow", " [link](x)"]:
        pieces += p.feed(chunk)
    pieces += p.flush()
    emos = [x.value for x in pieces if x.kind == "emotion"]
    text = "".join(x.value for x in pieces if x.kind == "text")
    assert emos == ["happy", "excited"]
    assert text == "Hello there! Wow [link](x)"


def test_action_tags_are_extracted_and_unknown_actions_dropped():
    p = EmotionStream()
    pieces = []
    for chunk in ["[emotion:joyful] Yay! [act", "ion:dance] Let's go", " [action:teleport] now", " [action:party]"]:
        pieces += p.feed(chunk)
    pieces += p.flush()
    acts = [x.value for x in pieces if x.kind == "action"]
    text = "".join(x.value for x in pieces if x.kind == "text")
    assert acts == ["dance", "celebrate"] and p.actions == ["dance", "celebrate"]
    assert text.strip() == "Yay! Let's go now"


def test_unknown_emotion_falls_back_to_neutral():
    p = EmotionStream()
    pieces = p.feed("[emotion:grumpycat] hi")
    assert pieces[0].value == "neutral"


# ------------------------------------------------------------------ http
async def test_health_and_auth(client):
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    ready = (await client.get("/readyz")).json()
    assert ready == {"database": True, "chat_configured": True, "voice_configured": True}
    assert (await client.get("/v1/config")).status_code == 401
    assert (await client.get("/v1/config", headers={"Authorization": "Bearer nope"})).status_code == 401
    cfg = (await client.get("/v1/config", headers=AUTH)).json()
    assert cfg["voice"] is True and "love" in cfg["emotions"]


@respx.mock
async def test_chat_streams_emotion_and_text(client):
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, text=anthropic_sse(["[emotion:exc", "ited] Hi! [action:wave]", "I can help."]),
                                    headers={"content-type": "text/event-stream"}))
    r = await client.post("/v1/chat", headers=AUTH, json={"message": "hello", "language": "hi-IN"})
    assert r.status_code == 200
    events = parse_sse(r.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "meta" and "emotion" in kinds and kinds[-1] == "done"
    done = events[-1][1]
    assert done["emotion"] == "excited" and done["text"] == "Hi! I can help."
    assert ("action", {"action": "wave"}) in events and done["actions"] == ["wave"]
    sent = json.loads(route.calls[0].request.content)
    assert sent["stream"] is True and "Hindi" in sent["system"] and sent["messages"][-1]["content"] == "hello"
    assert route.calls[0].request.headers["x-api-key"] == "sk-ant-test"

    # follow-up keeps history
    conv = events[0][1]["conversation_id"]
    r2 = await client.post("/v1/chat", headers=AUTH, json={"message": "and again", "conversation_id": conv})
    sent2 = json.loads(route.calls[1].request.content)
    assert [m["role"] for m in sent2["messages"]] == ["user", "assistant", "user"]
    msgs = (await client.get(f"/v1/conversations/{conv}/messages", headers=AUTH)).json()
    assert len(msgs) == 4 and msgs[1]["emotion"] == "excited"


@respx.mock
async def test_chat_upstream_error_is_reported_in_stream(client):
    respx.post("https://api.anthropic.com/v1/messages").mock(return_value=httpx.Response(400, json={"error": {"message": "bad"}}))
    r = await client.post("/v1/chat", headers=AUTH, json={"message": "hello"})
    events = parse_sse(r.text)
    assert events[-1][0] == "error"


@respx.mock
async def test_chat_retries_on_overload(client):
    route = respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=[
        httpx.Response(529, json={"error": {"message": "overloaded"}}, headers={"retry-after": "0"}),
        httpx.Response(200, text=anthropic_sse(["[emotion:happy] ok"]), headers={"content-type": "text/event-stream"}),
    ])
    r = await client.post("/v1/chat", headers=AUTH, json={"message": "hello"})
    assert parse_sse(r.text)[-1][1]["text"] == "ok"
    assert route.call_count == 2


OPENAI_BODY = "".join([
    'data: {"choices":[{"delta":{"content":"[emotion:happy] Backup here"}}]}\n\n',
    'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":3}}\n\n',
    "data: [DONE]\n\n",
])


@respx.mock
async def test_fallback_to_openai_when_claude_fails(tmp_path):
    app = create_app(settings(tmp_path, llm_fallback_provider="openai", openai_api_key="sk-oa"))
    claude = respx.post("https://api.anthropic.com/v1/messages").mock(return_value=httpx.Response(401, json={"error": {"message": "bad key"}}))
    openai = respx.post("https://api.openai.com/v1/chat/completions").mock(return_value=httpx.Response(200, text=OPENAI_BODY))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/v1/chat", headers=AUTH, json={"message": "hi"})
            usage = (await c.get("/admin/usage", headers={"X-Admin-Key": ADMIN})).json()
    assert claude.called and openai.called
    assert parse_sse(r.text)[-1][1]["text"] == "Backup here"
    assert "openai" in json.dumps(usage)


@respx.mock
async def test_fallback_not_used_when_claude_works(tmp_path):
    app = create_app(settings(tmp_path, llm_fallback_provider="openai", openai_api_key="sk-oa"))
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, text=anthropic_sse(["[emotion:happy] Claude here"]), headers={"content-type": "text/event-stream"}))
    openai = respx.post("https://api.openai.com/v1/chat/completions").mock(return_value=httpx.Response(200, text=OPENAI_BODY))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/v1/chat", headers=AUTH, json={"message": "hi"})
    assert parse_sse(r.text)[-1][1]["text"] == "Claude here" and not openai.called


@respx.mock
async def test_only_backup_key_uses_backup_directly(tmp_path):
    app = create_app(settings(tmp_path, anthropic_api_key=None, llm_fallback_provider="openai", openai_api_key="sk-oa"))
    openai = respx.post("https://api.openai.com/v1/chat/completions").mock(return_value=httpx.Response(200, text=OPENAI_BODY))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get("/readyz")).json()["chat_configured"] is True
            r = await c.post("/v1/chat", headers=AUTH, json={"message": "hi"})
    assert parse_sse(r.text)[-1][1]["text"] == "Backup here" and openai.called


@respx.mock
async def test_openai_provider(tmp_path):
    app = create_app(settings(tmp_path, llm_provider="openai", openai_api_key="sk-oa"))
    body = "".join([
        'data: {"choices":[{"delta":{"content":"[emotion:love] So"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":" sweet"}}]}\n\n',
        'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":3}}\n\n',
        "data: [DONE]\n\n",
    ])
    respx.post("https://api.openai.com/v1/chat/completions").mock(return_value=httpx.Response(200, text=body))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/v1/chat", headers=AUTH, json={"message": "hi"})
    done = parse_sse(r.text)[-1][1]
    assert done == {"text": "So sweet", "emotion": "love", "actions": [], "usage": {"input_tokens": 5, "output_tokens": 3}}


@respx.mock
async def test_tts_and_stt(client):
    tts = respx.post("https://api.sarvam.ai/text-to-speech").mock(
        return_value=httpx.Response(200, json={"request_id": "r1", "audios": [base64.b64encode(b"ID3fakeaudio").decode()]}))
    r = await client.post("/v1/tts", headers=AUTH, json={"text": "[emotion:happy] Namaste!", "language_code": "hi-IN"})
    assert r.status_code == 200 and r.content == b"ID3fakeaudio" and r.headers["content-type"] == "audio/mpeg"
    sent = json.loads(tts.calls[0].request.content)
    assert sent["text"] == "Namaste!" and sent["language_code"] == "hi-IN" and sent["model"] == "bulbul:v3"
    assert tts.calls[0].request.headers["api-subscription-key"] == "sarvam-test"

    respx.post("https://api.sarvam.ai/speech-to-text").mock(
        return_value=httpx.Response(200, json={"request_id": "r2", "transcript": " what's the weather ", "language_code": "en-IN"}))
    r = await client.post("/v1/stt", headers=AUTH, files={"file": ("a.webm", b"x" * 2000, "audio/webm")})
    assert r.json() == {"transcript": "what's the weather", "language_code": "en-IN"}
    r = await client.post("/v1/stt", headers=AUTH, files={"file": ("a.exe", b"x" * 2000, "application/x-msdownload")})
    assert r.status_code == 415


async def test_admin_tokens_and_usage(client):
    assert (await client.post("/admin/tokens", json={"name": "x"})).status_code == 401
    h = {"X-Admin-Key": ADMIN}
    created = (await client.post("/admin/tokens", headers=h, json={"name": "Laptop"})).json()
    assert created["token"].startswith("pom_")
    new_auth = {"Authorization": f"Bearer {created['token']}"}
    assert (await client.get("/v1/config", headers=new_auth)).status_code == 200
    assert (await client.delete(f"/admin/tokens/{created['id']}", headers=h)).status_code == 204
    assert (await client.get("/v1/config", headers=new_auth)).status_code == 401
    usage = (await client.get("/admin/usage", headers=h)).json()
    assert "by_kind" in usage


async def test_rate_limit(tmp_path):
    app = create_app(settings(tmp_path, rate_limit_per_minute=1, rate_limit_burst=1, anthropic_api_key=None))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            first = await c.post("/v1/chat", headers=AUTH, json={"message": "hi"})
            second = await c.post("/v1/chat", headers=AUTH, json={"message": "hi"})
    assert first.status_code == 503  # model not configured, but the request was admitted
    assert second.status_code == 429 and "retry-after" in second.headers


def test_action_tags_are_extracted():
    p = EmotionStream()
    pieces = p.feed("[emotion:joy] [action:da") + p.feed("nce] Let's party! [action:moonwalk] [action:zoomies]") + p.flush()
    kinds = [(x.kind, x.value) for x in pieces if x.kind != "text"]
    assert kinds == [("emotion", "joyful"), ("action", "dance"), ("action", "run")]
    assert "".join(x.value for x in pieces if x.kind == "text").strip() == "Let's party!"
    assert p.actions == ["dance", "run"]


@respx.mock
async def test_chat_streams_actions(client):
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, text=anthropic_sse(["[emotion:excited] [action:dance] Yay!"]),
                                    headers={"content-type": "text/event-stream"}))
    r = await client.post("/v1/chat", headers=AUTH, json={"message": "dance for me"})
    events = parse_sse(r.text)
    assert ("action", {"action": "dance"}) in events
    assert events[-1][1]["actions"] == ["dance"] and events[-1][1]["text"] == "Yay!"
    cfg = (await client.get("/v1/config", headers=AUTH)).json()
    assert "dance" in cfg["actions"]


# ------------------------------------------------------------------ API keys from the app
@pytest.fixture
async def keys_client(tmp_path):
    app = create_app(settings(tmp_path, anthropic_api_key=None, sarvam_api_key=None, allow_device_key_management=True,
                              llm_fallback_provider="openai", secret_file=str(tmp_path / "secret")))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            yield c, app


@respx.mock
async def test_save_list_use_and_delete_key(keys_client, tmp_path):
    c, app = keys_client
    assert (await c.get("/readyz")).json()["chat_configured"] is False
    check = respx.post("https://api.anthropic.com/v1/messages").mock(return_value=httpx.Response(200, json={"content": []}))
    r = await c.put("/v1/keys/anthropic", headers=AUTH, json={"key": "sk-ant-api03-goodkey1234"})
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["source"] == "app" and item["hint"] == "…1234" and item["role"] == "Main brain"
    assert check.calls[0].request.headers["x-api-key"] == "sk-ant-api03-goodkey1234"
    # applied live, never returned in full, encrypted at rest
    assert (await c.get("/readyz")).json()["chat_configured"] is True
    listing = (await c.get("/v1/keys", headers=AUTH)).json()
    assert "goodkey" not in json.dumps(listing)
    assert {k["provider"]: k["role"] for k in listing} == {"anthropic": "Main brain", "openai": "Backup brain", "sarvam": "Voice"}
    raw_db = (tmp_path / "t.db").read_bytes()
    assert b"goodkey" not in raw_db
    assert json.loads(check.calls[0].request.content)["max_tokens"] == 1
    # chat now uses the saved key
    chat = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, text=anthropic_sse(["[emotion:happy] hi"]), headers={"content-type": "text/event-stream"}))
    await c.post("/v1/chat", headers=AUTH, json={"message": "hi"})
    assert chat.calls[0].request.headers["x-api-key"] == "sk-ant-api03-goodkey1234"
    assert (await c.post("/v1/keys/anthropic/test", headers=AUTH)).json()["ok"] is True
    # survives a restart
    app2 = create_app(app.state.base_settings)
    async with app2.router.lifespan_context(app2):
        assert app2.state.settings.anthropic_api_key.get_secret_value() == "sk-ant-api03-goodkey1234"
    r = await c.delete("/v1/keys/anthropic", headers=AUTH)
    assert r.status_code == 200 and r.json()["configured"] is False
    assert (await c.get("/readyz")).json()["chat_configured"] is False


@respx.mock
async def test_rejected_key_is_not_saved(keys_client):
    c, _ = keys_client
    respx.post("https://api.openai.com/v1/chat/completions").mock(return_value=httpx.Response(401, json={"error": {"message": "bad"}}))
    r = await c.put("/v1/keys/openai", headers=AUTH, json={"key": "sk-wrong-key-0000"})
    assert r.status_code == 400 and "rejected" in r.json()["detail"]
    assert (await c.get("/v1/keys", headers=AUTH)).json()[1]["configured"] is False


async def test_key_management_needs_auth_and_server_opt_in(keys_client, tmp_path):
    c, _ = keys_client
    assert (await c.get("/v1/keys")).status_code == 401
    assert (await c.put("/v1/keys/nope", headers=AUTH, json={"key": "12345678"})).status_code == 404
    assert (await c.delete("/v1/keys/openai", headers=AUTH)).status_code == 404
    off = tmp_path / "off"
    off.mkdir()
    app = create_app(settings(off))  # allow_device_key_management defaults to False
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c2:
            assert (await c2.get("/v1/keys", headers=AUTH)).status_code == 403
            assert (await c2.get("/v1/config", headers=AUTH)).json()["manage_keys"] is False


async def test_env_key_cannot_be_deleted_from_app(tmp_path):
    app = create_app(settings(tmp_path, allow_device_key_management=True, secret_file=str(tmp_path / "secret")))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            item = (await c.get("/v1/keys", headers=AUTH)).json()[0]
            assert item["source"] == "server" and item["hint"] == "…test"
            assert (await c.delete("/v1/keys/anthropic", headers=AUTH)).status_code == 409


@respx.mock
async def test_key_errors_explain_the_problem(keys_client):
    c, _ = keys_client
    route = respx.post("https://api.anthropic.com/v1/messages")
    route.mock(return_value=httpx.Response(401, json={"error": {"message": "invalid x-api-key"}}))
    r = await c.put("/v1/keys/anthropic", headers=AUTH, json={"key": "sk-ant-usr-notanapikey00"})
    assert "invalid x-api-key" in r.json()["detail"] and "sign-in token" in r.json()["detail"]
    route.mock(return_value=httpx.Response(400, json={"error": {"message": "Your credit balance is too low"}}))
    r = await c.put("/v1/keys/anthropic", headers=AUTH, json={"key": "sk-ant-api03-nocredit00"})
    assert r.status_code == 400 and "no credit" in r.json()["detail"]
    assert (await c.get("/v1/keys", headers=AUTH)).json()[0]["configured"] is False
