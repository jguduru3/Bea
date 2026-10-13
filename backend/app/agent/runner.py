"""The agent loop: give Bea a goal, she plans, uses tools and reports back.

Each run is an asyncio task on the server. Every step is stored as an AgentEvent (the activity
history) and pushed live to anyone watching the run. When a tool needs approval the run pauses on
a future that the approve/deny endpoint resolves, so a person can answer minutes later from the
desktop app; unanswered approvals are denied after ``approval_timeout_s``.

The loop is written by hand rather than with the SDK's tool runner because runs must wait on
approvals that arrive through other HTTP requests, resume server-tool ``pause_turn`` stops, and
record every step as it happens.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import anthropic
from fastapi import FastAPI
from sqlalchemy import select, update

from ..db import AgentEvent, AgentRun, Approval, Notification, UsageEvent, new_id, utcnow
from ..emotion import EmotionStream, guess, strip_tags
from . import registry
from .prompts import context_block, system_prompt
from .registry import ApprovalDenied, ToolContext, ToolError

log = logging.getLogger("pom.agent")

SERVER_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 10},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 10},
]
SERVER_LABELS = {"web_search": "Searching the web", "web_fetch": "Reading a web page"}
BETAS = ["server-side-fallback-2026-07-01"]
LIVE_ONLY = {"delta", "emotion", "action"}  # streamed to watchers, not stored
MAX_PAUSE_RESUMES = 5
TOOL_TIMEOUT_S = 180


class AgentUnavailable(Exception):
    pass


@dataclass
class RunHandle:
    run_id: str
    token_id: str
    task: asyncio.Task | None = None
    seq: int = 0
    watchers: list[asyncio.Queue] = field(default_factory=list)
    approvals: dict[str, asyncio.Future] = field(default_factory=dict)
    done: bool = False


def default_client_factory(settings) -> anthropic.AsyncAnthropic:
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
    if not key:
        raise AgentUnavailable("Bea's agent runs on Claude. Add your Claude API key in Settings > API keys.")
    return anthropic.AsyncAnthropic(api_key=key, base_url=settings.anthropic_base_url, max_retries=2)


def _dump(block) -> dict:
    """An SDK content block as a plain dict, exactly as the API sent it (safe to send back)."""
    return block.to_dict(exclude_none=True) if hasattr(block, "to_dict") else dict(block)


class AgentManager:
    def __init__(self, app: FastAPI, client_factory=default_client_factory) -> None:
        self.app = app
        self.client_factory = client_factory
        self.runs: dict[str, RunHandle] = {}

    @property
    def settings(self):
        return self.app.state.settings

    @property
    def sessionmaker(self):
        return self.app.state.db.sessionmaker

    # -------------------------------------------------------------- lifecycle
    async def recover(self) -> None:
        """After a restart, nothing is running: close out runs and approvals left behind."""
        async with self.sessionmaker() as s:
            await s.execute(update(AgentRun).where(AgentRun.status.in_(["running", "waiting_approval"]))
                            .values(status="failed", result="Stopped because the server restarted.", finished_at=utcnow()))
            await s.execute(update(Approval).where(Approval.status == "pending").values(status="expired", decided_at=utcnow()))
            await s.commit()

    async def shutdown(self) -> None:
        tasks = [h.task for h in self.runs.values() if h.task and not h.task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def available(self) -> tuple[bool, str]:
        if not self.settings.agent_enabled:
            return False, "The agent is turned off on this server (POM_AGENT_ENABLED=false)."
        if not self.settings.provider_configured("anthropic"):
            return False, "Bea's agent runs on Claude. Add your Claude API key in Settings > API keys."
        return True, ""

    # -------------------------------------------------------------- starting runs
    async def start(self, token_id: str, goal: str, *, client: dict | None = None, parent_id: str | None = None,
                    trigger: str = "user", schedule_id: str | None = None) -> str:
        ok, why = self.available()
        if not ok:
            raise AgentUnavailable(why)
        active = sum(1 for h in self.runs.values() if h.token_id == token_id and not h.done)
        if active >= self.settings.agent_max_concurrent_runs:
            raise AgentUnavailable(f"{active} tasks are already running. Wait for one to finish or cancel it.")
        history: list = []
        async with self.sessionmaker() as s:
            if parent_id:
                parent = await s.get(AgentRun, parent_id)
                if parent is None or parent.token_id != token_id:
                    raise AgentUnavailable("That conversation was not found.")
                if parent.status in ("running", "waiting_approval"):
                    raise AgentUnavailable("Bea is still working on the previous message in this conversation.")
                history = json.loads(parent.transcript or "[]")
            run = AgentRun(token_id=token_id, goal=goal, parent_id=parent_id, trigger=trigger, schedule_id=schedule_id)
            s.add(run)
            await s.commit()
        handle = RunHandle(run.id, token_id)
        self.runs[run.id] = handle
        handle.task = asyncio.create_task(self._run(handle, goal, history, client or {}, trigger))
        return run.id

    # -------------------------------------------------------------- events
    async def emit(self, handle: RunHandle, type_: str, data: dict) -> None:
        ev = {"type": type_, "data": data, "seq": None}
        if type_ not in LIVE_ONLY:
            handle.seq += 1
            ev["seq"] = handle.seq
            async with self.sessionmaker() as s:
                s.add(AgentEvent(run_id=handle.run_id, seq=handle.seq, type=type_, data=json.dumps(data, ensure_ascii=False, default=str)))
                await s.commit()
        for q in list(handle.watchers):
            q.put_nowait(ev)

    async def watch(self, token_id: str, run_id: str, after: int = 0) -> AsyncIterator[dict]:
        """Replay stored steps after `after`, then follow the run live until it ends."""
        handle = self.runs.get(run_id)
        queue: asyncio.Queue | None = None
        if handle and not handle.done and handle.token_id == token_id:
            queue = asyncio.Queue()
            handle.watchers.append(queue)
        try:
            last = after
            async with self.sessionmaker() as s:
                run = await s.get(AgentRun, run_id)
                if run is None or run.token_id != token_id:
                    return
                rows = (await s.execute(select(AgentEvent).where(AgentEvent.run_id == run_id, AgentEvent.seq > after)
                                        .order_by(AgentEvent.seq))).scalars().all()
            for r in rows:
                last = r.seq
                yield {"type": r.type, "data": json.loads(r.data), "seq": r.seq}
            if queue is None:
                return
            while True:
                ev = await queue.get()
                if ev["seq"] is not None and ev["seq"] <= last:
                    continue
                yield ev
                if ev["type"] == "done":
                    return
        finally:
            if queue is not None and handle:
                handle.watchers.remove(queue)

    # -------------------------------------------------------------- approvals
    async def _request_approval(self, handle: RunHandle, tool: str, summary: str, details: dict) -> bool:
        async with self.sessionmaker() as s:
            ap = Approval(id=new_id(), token_id=handle.token_id, run_id=handle.run_id, tool=tool, summary=summary[:500],
                          details=json.dumps(details, ensure_ascii=False, default=str))
            s.add(ap)
            s.add(Notification(token_id=handle.token_id, kind="approval", title="Bea needs your OK", body=summary[:500],
                               run_id=handle.run_id, approval_id=ap.id))
            await s.execute(update(AgentRun).where(AgentRun.id == handle.run_id).values(status="waiting_approval"))
            await s.commit()
        fut = asyncio.get_running_loop().create_future()
        handle.approvals[ap.id] = fut
        await self.emit(handle, "approval", {"approval_id": ap.id, "tool": tool, "summary": summary, "details": details})
        try:
            approved, note = await asyncio.wait_for(fut, timeout=self.settings.approval_timeout_s)
            status = "approved" if approved else "denied"
        except asyncio.TimeoutError:
            approved, note, status = False, None, "expired"
        finally:
            handle.approvals.pop(ap.id, None)
        async with self.sessionmaker() as s:
            await s.execute(update(Approval).where(Approval.id == ap.id).values(status=status, note=note, decided_at=utcnow()))
            await s.execute(update(AgentRun).where(AgentRun.id == handle.run_id).values(status="running"))
            await s.execute(update(Notification).where(Notification.approval_id == ap.id).values(read=True))
            await s.commit()
        await self.emit(handle, "approval_decided", {"approval_id": ap.id, "status": status, "note": note})
        if not approved and note:
            raise ApprovalDenied(f"The person declined ({summary}) and said: {note!r}. Don't retry it; adjust to what they said.")
        return approved

    async def decide(self, token_id: str, approval_id: str, approved: bool, note: str | None = None) -> str:
        async with self.sessionmaker() as s:
            ap = await s.get(Approval, approval_id)
            if ap is None or ap.token_id != token_id:
                return "not_found"
            if ap.status != "pending":
                return ap.status
            handle = self.runs.get(ap.run_id)
            fut = handle.approvals.get(approval_id) if handle else None
            if fut is None or fut.done():
                ap.status, ap.decided_at = "expired", utcnow()
                await s.commit()
                return "expired"
        fut.set_result((approved, (note or "").strip() or None))
        return "approved" if approved else "denied"

    async def cancel(self, token_id: str, run_id: str) -> bool:
        handle = self.runs.get(run_id)
        if not handle or handle.token_id != token_id or handle.done or not handle.task:
            return False
        handle.task.cancel()
        return True

    # -------------------------------------------------------------- the loop
    async def _run(self, handle: RunHandle, goal: str, history: list, client: dict, trigger: str) -> None:
        settings = self.settings
        ctx = ToolContext(app=self.app, settings=settings, token_id=handle.token_id, run_id=handle.run_id,
                          http=self.app.state.http, background=(trigger != "user"))
        ctx.emit = lambda t, d: self.emit(handle, t, d)
        ctx.request_approval = lambda tool, summary, details: self._request_approval(handle, tool, summary, details)
        ctx.state["timezone"] = client.get("timezone") or "UTC"
        status, final_text, emotion, actions = "failed", "", "neutral", []
        usage = {"input_tokens": 0, "output_tokens": 0}
        messages = list(history)
        try:
            api = self.client_factory(settings)
            tools = await registry.tools_for(ctx)
            by_name = {t.name: t for t in tools}
            system = system_prompt(settings, await registry.group_notes(ctx, {t.group for t in tools}))
            ctx_text = await context_block(self.sessionmaker, handle.token_id, client, ctx.background)
            messages.append({"role": "user", "content": [{"type": "text", "text": ctx_text}, {"type": "text", "text": goal}]})
            await self.emit(handle, "run_started", {"goal": goal, "trigger": trigger, "tools": sorted(by_name)})
            tool_defs = [t.definition() for t in tools] + SERVER_TOOLS
            parser = EmotionStream()
            resumes = 0
            for _step in range(settings.agent_max_steps):
                msg = await self._turn(api, handle, system, tool_defs, messages, parser)
                u = msg.usage
                usage["input_tokens"] += (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0)
                usage["output_tokens"] += u.output_tokens or 0
                content = [_dump(b) for b in msg.content]
                messages.append({"role": "assistant", "content": content})
                await self._log_blocks(handle, msg.content)
                if msg.stop_reason == "pause_turn" and resumes < MAX_PAUSE_RESUMES:
                    resumes += 1
                    continue  # the server resumes its own tool loop from the trailing assistant turn
                if msg.stop_reason == "refusal":
                    final_text = "I can't help with that one."
                    status, emotion = "failed", "sad"
                    await self.emit(handle, "error", {"message": "Claude declined this request."})
                    break
                tool_uses = [b for b in msg.content if b.type == "tool_use"]
                if msg.stop_reason == "max_tokens" and tool_uses:
                    status, final_text = "failed", "I ran out of room mid-step. Try breaking the task into smaller parts."
                    break
                if msg.stop_reason == "tool_use" and tool_uses:
                    results = []
                    for tu in tool_uses:
                        results.append(await self._call_tool(ctx, handle, by_name.get(tu.name), tu))
                    messages.append({"role": "user", "content": results})
                    continue
                final_text = strip_tags("".join(b.text for b in msg.content if b.type == "text"))
                emotion = parser.first or guess(final_text)
                actions = parser.actions
                status = "done"
                break
            else:
                status = "failed"
                final_text = f"I stopped after {settings.agent_max_steps} steps without finishing. Here's where I got to; ask me to continue."
            if final_text:
                await self.emit(handle, "message", {"text": final_text, "emotion": emotion})
        except asyncio.CancelledError:
            status, final_text, emotion = "cancelled", "Stopped.", "neutral"
        except AgentUnavailable as exc:
            final_text, emotion = str(exc), "confused"
            await self.emit(handle, "error", {"message": final_text})
        except anthropic.AuthenticationError:
            final_text, emotion = "Claude rejected the API key. Check it in Settings > API keys.", "sad"
            await self.emit(handle, "error", {"message": final_text})
        except anthropic.RateLimitError:
            final_text, emotion = "Claude is rate-limiting us right now. Please try again in a minute.", "sad"
            await self.emit(handle, "error", {"message": final_text})
        except anthropic.APIStatusError as exc:
            final_text, emotion = f"Claude returned an error ({exc.status_code}): {exc.message}", "sad"
            await self.emit(handle, "error", {"message": final_text})
        except anthropic.APIConnectionError:
            final_text, emotion = "I couldn't reach Claude. Check the server's internet connection.", "sad"
            await self.emit(handle, "error", {"message": final_text})
        except Exception:  # noqa: BLE001
            log.exception("agent run crashed", extra={"run_id": handle.run_id})
            final_text, emotion = "Something went wrong on the server while I was working on that.", "sad"
            await self.emit(handle, "error", {"message": final_text})
        finally:
            await asyncio.shield(self._finish(handle, status, final_text, emotion, actions, usage, messages, trigger))

    async def _turn(self, api, handle: RunHandle, system: str, tools: list, messages: list, parser: EmotionStream):
        """One model turn, streamed so the person sees the answer as it is written."""
        s = self.settings
        async with api.beta.messages.stream(
            model=s.agent_model, max_tokens=32000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tools, messages=messages,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": s.agent_effort},
            betas=BETAS, fallbacks="default",
        ) as stream:
            async for event in stream:
                if event.type == "content_block_delta" and getattr(event.delta, "type", "") == "text_delta":
                    for piece in parser.feed(event.delta.text):
                        if piece.kind == "text":
                            await self.emit(handle, "delta", {"text": piece.value})
                        else:
                            await self.emit(handle, piece.kind, {piece.kind: piece.value})
            for piece in parser.flush():
                await self.emit(handle, "delta", {"text": piece.value})
            return await stream.get_final_message()

    async def _log_blocks(self, handle: RunHandle, blocks) -> None:
        """Record thinking summaries and the server-side tools (web search/fetch) in the activity log."""
        for b in blocks:
            if b.type == "thinking" and getattr(b, "thinking", ""):
                await self.emit(handle, "thinking", {"text": b.thinking[:2000]})
            elif b.type == "server_tool_use":
                inp = b.input if isinstance(b.input, dict) else {}
                what = inp.get("query") or inp.get("url") or ""
                await self.emit(handle, "tool", {"id": b.id, "name": b.name, "label": SERVER_LABELS.get(b.name, b.name),
                                                 "summary": str(what)[:200], "server": True})
            elif b.type in ("web_search_tool_result", "web_fetch_tool_result"):
                c = b.content
                if isinstance(c, list):
                    titles = [getattr(r, "title", "") for r in c][:5]
                    await self.emit(handle, "tool_result", {"id": b.tool_use_id, "ok": True,
                                                            "preview": f"{len(c)} results: " + "; ".join(t for t in titles if t)})
                else:
                    err = getattr(c, "error_code", None)
                    await self.emit(handle, "tool_result", {"id": b.tool_use_id, "ok": err is None,
                                                            "preview": f"error: {err}" if err else "page read"})

    async def _call_tool(self, ctx: ToolContext, handle: RunHandle, tool, tu) -> dict:
        def result(content, error=False):
            r = {"type": "tool_result", "tool_use_id": tu.id, "content": content}
            if error:
                r["is_error"] = True
            return r

        if tool is None:
            return result(f"Unknown tool {tu.name}.", True)
        problem = tool.validate(tu.input)
        if problem:
            await self.emit(handle, "tool_result", {"id": tu.id, "name": tu.name, "ok": False, "preview": problem})
            return result(json.dumps({"INVALID_INPUT": problem}), True)
        summary = tool.describe(tu.input)
        await self.emit(handle, "tool", {"id": tu.id, "name": tu.name, "label": tool.label or tu.name, "summary": summary, "server": False})
        try:
            if tool.approval == "always":
                await ctx.approve_or_raise(tu.name, summary, {"input": tu.input})
            timeout = TOOL_TIMEOUT_S if tool.approval == "never" else None
            out = await asyncio.wait_for(tool.handler(ctx, tu.input), timeout)
        except ApprovalDenied as exc:
            await self.emit(handle, "tool_result", {"id": tu.id, "name": tu.name, "ok": False, "preview": "Not approved"})
            return result(str(exc), True)
        except ToolError as exc:
            await self.emit(handle, "tool_result", {"id": tu.id, "name": tu.name, "ok": False, "preview": str(exc)[:300]})
            return result(str(exc), True)
        except asyncio.TimeoutError:
            await self.emit(handle, "tool_result", {"id": tu.id, "name": tu.name, "ok": False, "preview": "Timed out"})
            return result("The tool timed out.", True)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("tool crashed", extra={"tool": tu.name, "run_id": handle.run_id})
            await self.emit(handle, "tool_result", {"id": tu.id, "name": tu.name, "ok": False, "preview": "Internal error"})
            return result("The tool failed with an internal error.", True)
        preview = out if isinstance(out, str) else " ".join(b.get("text", "[image]") for b in out if isinstance(b, dict))
        await self.emit(handle, "tool_result", {"id": tu.id, "name": tu.name, "ok": True, "preview": preview[:300]})
        return result(out)

    async def _finish(self, handle: RunHandle, status: str, text: str, emotion: str, actions: list, usage: dict,
                      messages: list, trigger: str) -> None:
        try:
            for fut in handle.approvals.values():
                if not fut.done():
                    fut.cancel()
            async with self.sessionmaker() as s:
                run = await s.get(AgentRun, handle.run_id)
                run.status, run.result, run.emotion = status, text, emotion
                run.input_tokens, run.output_tokens = usage["input_tokens"], usage["output_tokens"]
                run.transcript = json.dumps(messages if status == "done" else [], ensure_ascii=False, default=str)
                run.finished_at = utcnow()
                await s.execute(update(Approval).where(Approval.run_id == handle.run_id, Approval.status == "pending")
                                .values(status="expired", decided_at=utcnow()))
                s.add(UsageEvent(token_id=handle.token_id, kind="agent", provider="anthropic", model=self.settings.agent_model,
                                 units_in=usage["input_tokens"], units_out=usage["output_tokens"], ok=status == "done"))
                if trigger != "user":
                    title = "Background task finished" if status == "done" else "Background task didn't finish"
                    s.add(Notification(token_id=handle.token_id, kind="task_done", title=f"{title}: {run.goal[:80]}",
                                       body=text[:2000], run_id=handle.run_id))
                await s.commit()
            await self.emit(handle, "done", {"status": status, "result": text, "emotion": emotion, "actions": actions, "usage": usage})
        finally:
            handle.done = True
            # keep finished handles briefly for late watchers; old ones are dropped
            finished = [rid for rid, h in self.runs.items() if h.done]
            for rid in finished[:-50]:
                self.runs.pop(rid, None)
