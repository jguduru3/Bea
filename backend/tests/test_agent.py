"""Agent loop, approvals, memory, goals, schedules and the activity history.

Claude is replaced by a scripted fake that returns real SDK message objects, so these tests cover
everything on our side of the API without network calls.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from anthropic.types.beta import BetaMessage

from app.agent import registry, scheduler
from app.agent.registry import Tool, obj
from app.agent.schedule import ScheduleError, next_run, normalize_repeat, parse_when
from app.db import MemoryItem, ScheduledTask
from app.main import create_app

from .test_api import AUTH, settings

# ------------------------------------------------------------------ a scripted Claude


def message(content: list[dict], stop: str = "end_turn") -> BetaMessage:
    return BetaMessage.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content,
        "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5},
    })


def text(t: str) -> dict:
    return {"type": "text", "text": t}


def tool_use(name: str, inp: dict, id_: str = "toolu_1") -> dict:
    return {"type": "tool_use", "id": id_, "name": name, "input": inp}


class FakeStream:
    def __init__(self, msg: BetaMessage):
        self.msg = msg

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def __aiter__(self):
        for b in self.msg.content:
            if b.type == "text":
                yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=b.text))

    async def get_final_message(self):
        return self.msg


class FakeClaude:
    """Returns the scripted messages in order and records every request."""

    def __init__(self, script: list[BetaMessage]):
        self.script, self.requests = list(script), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=lambda o: o.to_dict() if hasattr(o, "to_dict") else str(o))))
        return FakeStream(self.script.pop(0))


async def make(tmp_path, script, **kw):
    fake = FakeClaude(script)
    app = create_app(settings(tmp_path, secret_file=str(tmp_path / "secret"), **kw), agent_client_factory=lambda s: fake)
    return app, fake


async def wait_done(c, run_id, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        run = (await c.get(f"/v1/agent/runs/{run_id}", headers=AUTH)).json()
        if run["status"] in ("done", "failed", "cancelled"):
            return run
        await asyncio.sleep(0.02)
    raise AssertionError(f"run still {run['status']}")


async def wait_for(pred, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        v = await pred()
        if v:
            return v
        await asyncio.sleep(0.02)
    raise AssertionError("condition never became true")


# Test-only tools to exercise the approval paths.
SENT: list[dict] = []


async def _send(ctx, data):
    SENT.append(data)
    return f"Sent to {data['to']}."

registry.register(Tool("test_send", "Send a test message.", obj({"to": {"type": "string"}}, ["to"]), _send,
                       group="zz_test", approval="always", summarize=lambda d: f"Send to {d['to']}", label="Sending"))


# ------------------------------------------------------------------ runs
async def test_simple_goal_streams_and_records_history(tmp_path):
    app, fake = await make(tmp_path, [message([text("[emotion:happy] [action:wave] Done! Here's the plan.")])])
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/v1/agent/runs", headers=AUTH, json={"goal": "Plan my week", "timezone": "Asia/Kolkata", "user_name": "Asha"})
            assert r.status_code == 201
            run = await wait_done(c, r.json()["run_id"])
            assert run["status"] == "done" and run["result"] == "Done! Here's the plan." and run["emotion"] == "happy"
            kinds = [e["type"] for e in run["events"]]
            assert kinds[0] == "run_started" and "message" in kinds and kinds[-1] == "done"
            done = run["events"][-1]["data"]
            assert done["actions"] == ["wave"]
            # the live event stream replays a finished run
            sse = (await c.get(f"/v1/agent/runs/{run['id']}/events", headers=AUTH)).text
            assert "event: run_started" in sse and "event: done" in sse
            history = (await c.get("/v1/agent/runs", headers=AUTH)).json()
            assert history[0]["id"] == run["id"]
    req = fake.requests[0]
    assert req["model"] == "claude-opus-5-5" and req["fallbacks"] == "default"
    assert {"type": "web_search_20260209", "name": "web_search", "max_uses": 10} in req["tools"]
    assert all(t.get("eager_input_streaming") for t in req["tools"] if "input_schema" in t)
    first = req["messages"][0]["content"]
    assert "<context>" in first[0]["text"] and "Asia/Kolkata" in first[0]["text"] and "Asha" in first[0]["text"]
    assert first[1]["text"] == "Plan my week"
    assert "<context>" not in req["system"][0]["text"]  # volatile facts stay out of the cached system prompt


async def test_tool_loop_runs_tools_and_returns_results(tmp_path):
    app, fake = await make(tmp_path, [
        message([text("Saving that."), tool_use("remember", {"text": "Prefers meetings after 11am", "kind": "preference"})], "tool_use"),
        message([text("[emotion:proud] Noted for next time.")]),
    ])
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/v1/agent/runs", headers=AUTH, json={"goal": "Remember I like late meetings"})
            run = await wait_done(c, r.json()["run_id"])
            assert run["status"] == "done"
            mems = (await c.get("/v1/agent/memories", headers=AUTH)).json()
            assert mems[0]["text"] == "Prefers meetings after 11am"
            tool_events = [e for e in run["events"] if e["type"] in ("tool", "tool_result")]
            assert tool_events[0]["data"]["label"] == "Remembering" and tool_events[1]["data"]["ok"] is True
    second = fake.requests[1]["messages"]
    assert second[1]["role"] == "assistant" and second[1]["content"][1]["type"] == "tool_use"
    assert second[2]["content"][0] == {"type": "tool_result", "tool_use_id": "toolu_1", "content": "Saved to memory (id " + mems[0]["id"] + ")."}


async def test_invalid_tool_input_is_returned_as_error(tmp_path):
    app, fake = await make(tmp_path, [
        message([tool_use("remember", {"txt": "oops"})], "tool_use"),
        message([text("[emotion:confused] Sorry, fixed.")]),
    ])
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            run = await wait_done(c, (await c.post("/v1/agent/runs", headers=AUTH, json={"goal": "x"})).json()["run_id"])
    res = fake.requests[1]["messages"][-1]["content"][0]
    assert res["is_error"] is True and "INVALID_INPUT" in res["content"]
    assert run["status"] == "done"


async def test_secrets_are_not_remembered(tmp_path):
    app, fake = await make(tmp_path, [
        message([tool_use("remember", {"text": "my api key: sk-ant-api03-abcdefghijkl"})], "tool_use"),
        message([text("[emotion:neutral] I won't store that.")]),
    ])
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await wait_done(c, (await c.post("/v1/agent/runs", headers=AUTH, json={"goal": "x"})).json()["run_id"])
            assert (await c.get("/v1/agent/memories", headers=AUTH)).json() == []
    assert fake.requests[1]["messages"][-1]["content"][0]["is_error"] is True


# ------------------------------------------------------------------ approvals
@pytest.mark.parametrize("approve", [True, False])
async def test_sensitive_action_waits_for_approval(tmp_path, approve):
    SENT.clear()
    app, fake = await make(tmp_path, [
        message([tool_use("test_send", {"to": "client@example.com"})], "tool_use"),
        message([text("[emotion:happy] All done.")]),
    ])
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            run_id = (await c.post("/v1/agent/runs", headers=AUTH, json={"goal": "Send it"})).json()["run_id"]

            async def pending():
                rows = (await c.get("/v1/agent/approvals", headers=AUTH)).json()
                return rows or None
            ap = (await wait_for(pending))[0]
            assert ap["summary"] == "Send to client@example.com" and ap["tool"] == "test_send"
            assert SENT == []  # nothing happens before the decision
            run = (await c.get(f"/v1/agent/runs/{run_id}", headers=AUTH)).json()
            assert run["status"] == "waiting_approval"
            notes = (await c.get("/v1/notifications", headers=AUTH)).json()
            assert notes[0]["kind"] == "approval" and notes[0]["approval_id"] == ap["id"]
            r = await c.post(f"/v1/agent/approvals/{ap['id']}", headers=AUTH, json={"approve": approve})
            assert r.json()["status"] == ("approved" if approve else "denied")
            run = await wait_done(c, run_id)
            assert run["status"] == "done"
            assert (await c.get("/v1/agent/approvals", headers=AUTH)).json() == []
    result = fake.requests[1]["messages"][-1]["content"][0]
    if approve:
        assert SENT == [{"to": "client@example.com"}] and result["content"] == "Sent to client@example.com."
    else:
        assert SENT == [] and result["is_error"] is True and "did not approve" in result["content"]


async def test_unanswered_approval_expires(tmp_path):
    SENT.clear()
    app, fake = await make(tmp_path, [
        message([tool_use("test_send", {"to": "x@y.z"})], "tool_use"),
        message([text("[emotion:neutral] Okay, I didn't send it.")]),
    ], approval_timeout_s=0)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            run = await wait_done(c, (await c.post("/v1/agent/runs", headers=AUTH, json={"goal": "x"})).json()["run_id"])
            history = (await c.get("/v1/agent/approvals?status=all", headers=AUTH)).json()
    assert SENT == [] and history[0]["status"] == "expired" and run["status"] == "done"


async def test_denial_with_note_is_passed_to_claude(tmp_path):
    app, fake = await make(tmp_path, [
        message([tool_use("test_send", {"to": "boss@x.com"})], "tool_use"),
        message([text("[emotion:neutral] Got it.")]),
    ])
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            run_id = (await c.post("/v1/agent/runs", headers=AUTH, json={"goal": "x"})).json()["run_id"]
            ap = (await wait_for(lambda: _first_pending(c)))
            await c.post(f"/v1/agent/approvals/{ap['id']}", headers=AUTH, json={"approve": False, "note": "send it tomorrow instead"})
            await wait_done(c, run_id)
    assert "send it tomorrow instead" in fake.requests[1]["messages"][-1]["content"][0]["content"]


async def _first_pending(c):
    rows = (await c.get("/v1/agent/approvals", headers=AUTH)).json()
    return rows[0] if rows else None


# ------------------------------------------------------------------ loop details
async def test_pause_turn_resumes_without_extra_user_message(tmp_path):
    app, fake = await make(tmp_path, [
        message([{"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "INR USD rate"}}], "pause_turn"),
        message([text("[emotion:curious] About 84 rupees.")]),
    ])
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            run = await wait_done(c, (await c.post("/v1/agent/runs", headers=AUTH, json={"goal": "rate?"})).json()["run_id"])
    assert run["status"] == "done"
    second = fake.requests[1]["messages"]
    assert second[-1]["role"] == "assistant" and second[-1]["content"][0]["type"] == "server_tool_use"
    logged = [e for e in run["events"] if e["type"] == "tool"]
    assert logged[0]["data"]["label"] == "Searching the web" and logged[0]["data"]["summary"] == "INR USD rate"


async def test_follow_up_continues_the_thread(tmp_path):
    app, fake = await make(tmp_path, [message([text("[emotion:happy] First answer.")]), message([text("[emotion:happy] Second.")])])
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            first = await wait_done(c, (await c.post("/v1/agent/runs", headers=AUTH, json={"goal": "one"})).json()["run_id"])
            r = await c.post("/v1/agent/runs", headers=AUTH, json={"goal": "two", "thread_id": first["id"]})
            await wait_done(c, r.json()["run_id"])
    msgs = fake.requests[1]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"] and msgs[2]["content"][1]["text"] == "two"
    assert msgs[1]["content"][0]["text"] == "[emotion:happy] First answer."  # replayed exactly as Claude sent it


async def test_agent_needs_a_claude_key(tmp_path):
    app, _ = await make(tmp_path, [], anthropic_api_key=None, llm_provider="openai", openai_api_key="sk-oa")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/v1/agent/runs", headers=AUTH, json={"goal": "x"})
            status = (await c.get("/v1/agent/status", headers=AUTH)).json()
    assert r.status_code == 409 and "Claude API key" in r.json()["detail"]
    assert status["available"] is False


async def test_runs_are_private_to_each_device(tmp_path):
    app, _ = await make(tmp_path, [message([text("[emotion:happy] hi")])])
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            run_id = (await c.post("/v1/agent/runs", headers=AUTH, json={"goal": "x"})).json()["run_id"]
            await wait_done(c, run_id)
            other = (await c.post("/admin/tokens", headers={"X-Admin-Key": "admin-secret"}, json={"name": "other"})).json()["token"]
            h2 = {"Authorization": f"Bearer {other}"}
            assert (await c.get(f"/v1/agent/runs/{run_id}", headers=h2)).status_code == 404
            assert (await c.get("/v1/agent/runs", headers=h2)).json() == []


# ------------------------------------------------------------------ goals, schedules, reminders
async def test_goals_and_schedules_tools(tmp_path):
    app, fake = await make(tmp_path, [
        message([tool_use("goal_create", {"title": "Launch Q4 report", "steps": ["Collect KPIs", "Draft", "Review"], "due": "2026-11-01"}, "toolu_a"),
                 tool_use("schedule_task", {"title": "Standup reminder", "prompt": "Standup in 5 minutes", "kind": "reminder",
                                            "repeat": "weekdays 09:55"}, "toolu_b")], "tool_use"),
        message([text("[emotion:proud] Set up.")]),
    ])
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await wait_done(c, (await c.post("/v1/agent/runs", headers=AUTH, json={"goal": "x", "timezone": "Asia/Kolkata"})).json()["run_id"])
            goals = (await c.get("/v1/agent/goals", headers=AUTH)).json()
            scheds = (await c.get("/v1/agent/schedules", headers=AUTH)).json()
    assert goals[0]["title"] == "Launch Q4 report" and len(goals[0]["steps"]) == 3
    assert scheds[0]["repeat"] == "weekdays 09:55" and scheds[0]["timezone"] == "Asia/Kolkata"
    nxt = datetime.fromisoformat(scheds[0]["next_run_at"]).astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert (nxt.hour, nxt.minute) == (9, 55) and nxt.weekday() < 5


async def test_scheduler_fires_reminders_and_starts_tasks(tmp_path):
    app, fake = await make(tmp_path, [message([text("[emotion:happy] 3 emails need replies.")])])
    async with app.router.lifespan_context(app):
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        async with app.state.db.sessionmaker() as s:
            tok = (await s.execute(__import__("sqlalchemy").select(__import__("app.db", fromlist=["ApiToken"]).ApiToken))).scalars().first()
            s.add(ScheduledTask(token_id=tok.id, title="Drink water", prompt="Time for water", kind="reminder", next_run_at=past))
            s.add(ScheduledTask(token_id=tok.id, title="Inbox check", prompt="Check inbox", kind="task", repeat="daily 09:00",
                                timezone="UTC", next_run_at=past))
            await s.commit()
        assert await scheduler.tick(app) == 2
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            runs = (await c.get("/v1/agent/runs", headers=AUTH)).json()
            run = await wait_done(c, runs[0]["id"])
            notes = (await c.get("/v1/notifications", headers=AUTH)).json()
            scheds = {j["title"]: j for j in (await c.get("/v1/agent/schedules", headers=AUTH)).json()}
    assert run["trigger"] == "schedule"
    kinds = {n["kind"]: n for n in notes}
    assert kinds["reminder"]["body"] == "Time for water" and "3 emails need replies." in kinds["task_done"]["body"]
    assert scheds["Drink water"]["active"] is False  # one-off
    assert scheds["Inbox check"]["active"] is True and datetime.fromisoformat(scheds["Inbox check"]["next_run_at"]) > past
    assert "background task" in fake.requests[0]["messages"][0]["content"][0]["text"]


def test_repeat_rules():
    assert normalize_repeat(" Weekly  MON,thu 17:00 ") == "weekly mon,thu 17:00"
    with pytest.raises(ScheduleError):
        normalize_repeat("every 2m")
    with pytest.raises(ScheduleError):
        normalize_repeat("sometimes")
    after = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)  # a Saturday
    assert next_run("weekdays 09:00", "UTC", after) == datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
    assert next_run("monthly 31 10:00", "UTC", after) == datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
    assert next_run("every 2h", "UTC", after) == after + timedelta(hours=2)
    ist = next_run("daily 09:00", "Asia/Kolkata", after)
    assert ist == datetime(2026, 10, 11, 3, 30, tzinfo=timezone.utc)
    assert parse_when("2026-10-12T09:00", "Asia/Kolkata") == datetime(2026, 10, 12, 3, 30, tzinfo=timezone.utc)


async def test_hub_endpoints_manage_memory_goals_documents(tmp_path):
    app, _ = await make(tmp_path, [
        message([tool_use("create_document", {"title": "Market brief", "content": "# Brief\nFindings..."})], "tool_use"),
        message([text("[emotion:proud] Wrote the brief.")]),
    ])
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            run = await wait_done(c, (await c.post("/v1/agent/runs", headers=AUTH, json={"goal": "brief"})).json()["run_id"])
            assert any(e["type"] == "document" for e in run["events"])
            docs = (await c.get("/v1/agent/documents", headers=AUTH)).json()
            doc = (await c.get(f"/v1/agent/documents/{docs[0]['id']}", headers=AUTH)).json()
            assert doc["content"].startswith("# Brief")
            mid = (await c.post("/v1/agent/memories", headers=AUTH, json={"text": "Works at Better Analytics"})).json()["id"]
            assert (await c.delete(f"/v1/agent/memories/{mid}", headers=AUTH)).status_code == 204
            assert (await c.get("/v1/agent/memories", headers=AUTH)).json() == []
