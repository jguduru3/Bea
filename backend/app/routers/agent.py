from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent import registry
from ..agent.registry import ToolContext
from ..agent.runner import AgentUnavailable
from ..agent.schedule import describe
from ..db import AgentEvent, AgentRun, ApiToken, Approval, Document, Goal, MemoryItem, Notification, ScheduledTask, utcnow
from ..ratelimit import rate_limited
from ..security import get_session, require_device

router = APIRouter(prefix="/v1", tags=["agent"], dependencies=[Depends(require_device)])


def _iso(dt):
    return dt.isoformat() if dt else None


def _run_out(r: AgentRun) -> dict:
    return {"id": r.id, "goal": r.goal, "status": r.status, "trigger": r.trigger, "parent_id": r.parent_id,
            "result": r.result, "emotion": r.emotion, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
            "created_at": _iso(r.created_at), "finished_at": _iso(r.finished_at)}


# ------------------------------------------------------------------ status
@router.get("/agent/status", summary="Is the agent usable, and which tools does it have for this person")
async def status(request: Request, token: ApiToken = Depends(require_device)):
    mgr = request.app.state.agent
    ok, why = mgr.available()
    ctx = ToolContext(app=request.app, settings=request.app.state.settings, token_id=token.id, run_id="", http=request.app.state.http)
    tools = await registry.tools_for(ctx)
    return {"available": ok, "reason": why, "model": request.app.state.settings.agent_model,
            "tools": [{"name": t.name, "group": t.group, "label": t.label, "approval": t.approval} for t in tools]}


# ------------------------------------------------------------------ runs
class RunIn(BaseModel):
    goal: str = Field(min_length=1, max_length=8000)
    thread_id: str | None = Field(default=None, description="A previous run to continue (follow-up message)")
    timezone: str | None = None
    language: str | None = None
    user_name: str | None = None
    voice: bool = False


@router.post("/agent/runs", status_code=201, summary="Give Bea a goal", dependencies=[Depends(rate_limited())])
async def start_run(body: RunIn, request: Request, token: ApiToken = Depends(require_device)):
    try:
        run_id = await request.app.state.agent.start(
            token.id, body.goal.strip(), parent_id=body.thread_id,
            client={"timezone": body.timezone, "language": body.language, "user_name": body.user_name, "voice": body.voice})
    except AgentUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run_id": run_id}


@router.get("/agent/runs", summary="Recent runs (activity history)")
async def list_runs(limit: int = Query(30, ge=1, le=200), token: ApiToken = Depends(require_device),
                    session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(AgentRun).where(AgentRun.token_id == token.id)
                                  .order_by(AgentRun.created_at.desc()).limit(limit))).scalars()
    return [_run_out(r) for r in rows]


@router.get("/agent/runs/{run_id}", summary="One run with every step")
async def get_run(run_id: str, token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    run = await session.get(AgentRun, run_id)
    if run is None or run.token_id != token.id:
        raise HTTPException(404, "Run not found")
    events = (await session.execute(select(AgentEvent).where(AgentEvent.run_id == run_id).order_by(AgentEvent.seq))).scalars()
    return {**_run_out(run), "events": [{"seq": e.seq, "type": e.type, "data": json.loads(e.data), "at": _iso(e.created_at)} for e in events]}


@router.get("/agent/runs/{run_id}/events", summary="Follow a run live (server-sent events)")
async def run_events(run_id: str, request: Request, after: int = 0, token: ApiToken = Depends(require_device)):
    mgr = request.app.state.agent

    async def stream():
        async for ev in mgr.watch(token.id, run_id, after):
            if await request.is_disconnected():
                break
            yield f"event: {ev['type']}\ndata: {json.dumps({'seq': ev['seq'], **ev['data']}, ensure_ascii=False, default=str)}\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/agent/runs/{run_id}/cancel", summary="Stop a run")
async def cancel_run(run_id: str, request: Request, token: ApiToken = Depends(require_device)):
    if not await request.app.state.agent.cancel(token.id, run_id):
        raise HTTPException(409, "That run isn't running.")
    return {"ok": True}


# ------------------------------------------------------------------ approvals
class DecisionIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=1000)


@router.get("/agent/approvals", summary="Approvals (pending by default)")
async def list_approvals(status: str = "pending", token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    q = select(Approval).where(Approval.token_id == token.id)
    if status != "all":
        q = q.where(Approval.status == status)
    rows = (await session.execute(q.order_by(Approval.created_at.desc()).limit(100))).scalars()
    return [{"id": a.id, "run_id": a.run_id, "tool": a.tool, "summary": a.summary, "details": json.loads(a.details),
             "status": a.status, "note": a.note, "created_at": _iso(a.created_at), "decided_at": _iso(a.decided_at)} for a in rows]


@router.post("/agent/approvals/{approval_id}", summary="Approve or deny a pending action")
async def decide(approval_id: str, body: DecisionIn, request: Request, token: ApiToken = Depends(require_device)):
    result = await request.app.state.agent.decide(token.id, approval_id, body.approve, body.note)
    if result == "not_found":
        raise HTTPException(404, "Approval not found")
    return {"status": result}


# ------------------------------------------------------------------ notifications
@router.get("/notifications", summary="Notifications for this person")
async def notifications(unread: bool = True, limit: int = Query(30, ge=1, le=200), token: ApiToken = Depends(require_device),
                        session: AsyncSession = Depends(get_session)):
    q = select(Notification).where(Notification.token_id == token.id)
    if unread:
        q = q.where(Notification.read.is_(False))
    rows = (await session.execute(q.order_by(Notification.created_at.desc()).limit(limit))).scalars()
    return [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "run_id": n.run_id, "approval_id": n.approval_id,
             "read": n.read, "created_at": _iso(n.created_at)} for n in rows]


class ReadIn(BaseModel):
    ids: list[str] | None = None  # None marks everything read


@router.post("/notifications/read")
async def mark_read(body: ReadIn, token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    q = update(Notification).where(Notification.token_id == token.id)
    if body.ids is not None:
        q = q.where(Notification.id.in_(body.ids))
    await session.execute(q.values(read=True))
    await session.commit()
    return {"ok": True}


# ------------------------------------------------------------------ goals, schedules, memory, documents
async def _owned(session: AsyncSession, model, item_id: str, token: ApiToken):
    row = await session.get(model, item_id)
    if row is None or row.token_id != token.id:
        raise HTTPException(404, "Not found")
    return row


@router.get("/agent/goals")
async def goals(token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Goal).where(Goal.token_id == token.id).order_by(Goal.created_at.desc()))).scalars()
    return [{"id": g.id, "title": g.title, "details": g.details, "steps": json.loads(g.steps or "[]"), "status": g.status,
             "due": g.due, "created_at": _iso(g.created_at), "updated_at": _iso(g.updated_at)} for g in rows]


class GoalPatch(BaseModel):
    status: str | None = Field(default=None, pattern="^(active|done|dropped)$")
    steps: list[dict] | None = None


@router.patch("/agent/goals/{goal_id}")
async def patch_goal(goal_id: str, body: GoalPatch, token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    g = await _owned(session, Goal, goal_id, token)
    if body.status:
        g.status = body.status
    if body.steps is not None:
        g.steps = json.dumps([{"text": str(st.get("text", ""))[:300], "done": bool(st.get("done"))} for st in body.steps])
    g.updated_at = utcnow()
    await session.commit()
    return {"ok": True}


@router.delete("/agent/goals/{goal_id}", status_code=204)
async def delete_goal(goal_id: str, token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    await session.delete(await _owned(session, Goal, goal_id, token))
    await session.commit()


@router.get("/agent/schedules")
async def schedules(token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(ScheduledTask).where(ScheduledTask.token_id == token.id)
                                  .order_by(ScheduledTask.active.desc(), ScheduledTask.next_run_at))).scalars()
    return [{"id": j.id, "title": j.title, "prompt": j.prompt, "kind": j.kind, "repeat": j.repeat, "timezone": j.timezone,
             "when": describe(j.repeat, j.next_run_at, j.timezone), "next_run_at": _iso(j.next_run_at),
             "last_run_at": _iso(j.last_run_at), "last_run_id": j.last_run_id, "active": j.active} for j in rows]


class SchedulePatch(BaseModel):
    active: bool


@router.patch("/agent/schedules/{schedule_id}")
async def patch_schedule(schedule_id: str, body: SchedulePatch, token: ApiToken = Depends(require_device),
                         session: AsyncSession = Depends(get_session)):
    j = await _owned(session, ScheduledTask, schedule_id, token)
    j.active = body.active
    await session.commit()
    return {"ok": True}


@router.post("/agent/schedules/{schedule_id}/run-now")
async def run_schedule_now(schedule_id: str, request: Request, token: ApiToken = Depends(require_device),
                           session: AsyncSession = Depends(get_session)):
    j = await _owned(session, ScheduledTask, schedule_id, token)
    if j.kind == "reminder":
        session.add(Notification(token_id=token.id, kind="reminder", title=j.title, body=j.prompt))
        await session.commit()
        return {"run_id": None}
    try:
        run_id = await request.app.state.agent.start(token.id, j.prompt, client={"timezone": j.timezone}, trigger="schedule", schedule_id=j.id)
    except AgentUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run_id": run_id}


@router.delete("/agent/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str, token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    await session.delete(await _owned(session, ScheduledTask, schedule_id, token))
    await session.commit()


@router.get("/agent/memories")
async def memories(token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(MemoryItem).where(MemoryItem.token_id == token.id).order_by(MemoryItem.created_at.desc()))).scalars()
    return [{"id": m.id, "text": m.text, "kind": m.kind, "created_at": _iso(m.created_at)} for m in rows]


class MemoryIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    kind: str = Field(default="fact", pattern="^(fact|preference|person|project)$")


@router.post("/agent/memories", status_code=201)
async def add_memory(body: MemoryIn, token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    m = MemoryItem(token_id=token.id, text=body.text.strip(), kind=body.kind)
    session.add(m)
    await session.commit()
    return {"id": m.id}


@router.delete("/agent/memories/{memory_id}", status_code=204)
async def delete_memory(memory_id: str, token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    await session.delete(await _owned(session, MemoryItem, memory_id, token))
    await session.commit()


@router.delete("/agent/memories", status_code=204, summary="Forget everything")
async def clear_memories(token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    await session.execute(delete(MemoryItem).where(MemoryItem.token_id == token.id))
    await session.commit()


@router.get("/agent/documents")
async def documents(token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Document).where(Document.token_id == token.id).order_by(Document.updated_at.desc()))).scalars()
    return [{"id": d.id, "title": d.title, "run_id": d.run_id, "size": len(d.content), "updated_at": _iso(d.updated_at)} for d in rows]


@router.get("/agent/documents/{document_id}")
async def document(document_id: str, token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    d = await _owned(session, Document, document_id, token)
    return {"id": d.id, "title": d.title, "content": d.content, "run_id": d.run_id, "updated_at": _iso(d.updated_at)}


@router.delete("/agent/documents/{document_id}", status_code=204)
async def delete_document(document_id: str, token: ApiToken = Depends(require_device), session: AsyncSession = Depends(get_session)):
    await session.delete(await _owned(session, Document, document_id, token))
    await session.commit()
