"""Bea's built-in tools: plan, memory, goals, schedules and reminders, documents, notifications
and small desktop actions. None of them reach outside this server, so none need approval.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import delete, or_, select

from ..db import Document, Goal, MemoryItem, Notification, ScheduledTask, utcnow
from .registry import ToolContext, ToolError, obj, register_group, tool
from .schedule import ScheduleError, describe, next_run, normalize_repeat, parse_when

STR = {"type": "string"}


def _tz(ctx: ToolContext) -> str:
    return ctx.state.get("timezone") or "UTC"


# ------------------------------------------------------------------ plan
@tool("update_plan",
      "Show the person your plan for a multi-step task and keep it current. Call it at the start of any task "
      "that needs three or more steps, then again whenever a step starts or finishes. Each step has a short "
      "text and a status. Skip it for quick questions.",
      obj({"steps": {"type": "array", "items": obj({"text": STR, "status": {"enum": ["pending", "in_progress", "done", "skipped"]}},
                                                    ["text", "status"])}}, ["steps"]),
      label="Updating the plan")
async def update_plan(ctx: ToolContext, data: dict) -> str:
    ctx.state["plan"] = data["steps"]
    await ctx.emit("plan", {"steps": data["steps"]})
    done = sum(1 for s in data["steps"] if s["status"] in ("done", "skipped"))
    return f"Plan updated ({done}/{len(data['steps'])} steps finished)."


# ------------------------------------------------------------------ memory
@tool("remember",
      "Save something worth knowing in future conversations: the person's preferences, facts about them, "
      "people they work with, ongoing projects. Call it when they tell you something lasting, or say 'remember'. "
      "Never store passwords, keys or other secrets.",
      obj({"text": {"type": "string", "description": "One self-contained fact, e.g. 'Prefers meetings after 11am IST'."},
           "kind": {"enum": ["fact", "preference", "person", "project"]}}, ["text"]),
      summarize=lambda d: f"Remember: {d['text'][:120]}", label="Remembering")
async def remember(ctx: ToolContext, data: dict) -> str:
    text = data["text"].strip()
    if re.search(r"(sk-[a-z0-9-]{10,}|password\s*[:=]|api[_ -]?key\s*[:=])", text, re.I):
        raise ToolError("That looks like a secret (a password or key). Secrets must not be stored in memory.")
    async with ctx.sessionmaker() as s:
        row = MemoryItem(token_id=ctx.token_id, text=text[:1000], kind=data.get("kind", "fact"))
        s.add(row)
        await s.commit()
    return f"Saved to memory (id {row.id})."


@tool("recall",
      "Search what you remember about the person. Call it when their preferences, people or projects could "
      "change your answer and the context block doesn't already cover it.",
      obj({"query": {"type": "string", "description": "Words to look for; empty lists recent memories."}}),
      label="Checking memory")
async def recall(ctx: ToolContext, data: dict) -> str:
    words = [w for w in re.findall(r"\w{3,}", (data.get("query") or "").lower())][:6]
    async with ctx.sessionmaker() as s:
        q = select(MemoryItem).where(MemoryItem.token_id == ctx.token_id)
        if words:
            q = q.where(or_(*[MemoryItem.text.ilike(f"%{w}%") for w in words]))
        rows = (await s.execute(q.order_by(MemoryItem.created_at.desc()).limit(30))).scalars().all()
    if not rows:
        return "Nothing remembered about that."
    return "\n".join(f"- [{r.id}] ({r.kind}) {r.text}" for r in rows)


@tool("forget", "Delete a memory by id, when the person asks you to forget something or it is out of date.",
      obj({"memory_id": STR}, ["memory_id"]), label="Forgetting")
async def forget(ctx: ToolContext, data: dict) -> str:
    async with ctx.sessionmaker() as s:
        res = await s.execute(delete(MemoryItem).where(MemoryItem.id == data["memory_id"], MemoryItem.token_id == ctx.token_id))
        await s.commit()
    if not res.rowcount:
        raise ToolError("No memory with that id.")
    return "Forgotten."


# ------------------------------------------------------------------ goals
def _goal_line(g: Goal) -> str:
    steps = json.loads(g.steps or "[]")
    done = sum(1 for st in steps if st.get("done"))
    due = f", due {g.due}" if g.due else ""
    out = f"[{g.id}] {g.title} ({g.status}{due}, {done}/{len(steps)} steps done)"
    for i, st in enumerate(steps):
        out += f"\n    {i}. [{'x' if st.get('done') else ' '}] {st['text']}"
    return out


@tool("goal_create",
      "Start tracking a goal for the person, with an action plan. Use it when they set a goal or ask you to "
      "help them work toward something over days or weeks.",
      obj({"title": STR, "details": STR, "steps": {"type": "array", "items": STR},
           "due": {"type": "string", "description": "ISO date, optional"}}, ["title"]),
      summarize=lambda d: f"New goal: {d['title']}", label="Creating a goal")
async def goal_create(ctx: ToolContext, data: dict) -> str:
    async with ctx.sessionmaker() as s:
        g = Goal(token_id=ctx.token_id, title=data["title"][:300], details=data.get("details", ""), due=data.get("due"),
                 steps=json.dumps([{"text": t, "done": False} for t in data.get("steps", [])]))
        s.add(g)
        await s.commit()
    return "Goal created:\n" + _goal_line(g)


@tool("goal_update",
      "Update a goal: tick off or add steps, change details, due date or status.",
      obj({"goal_id": STR, "status": {"enum": ["active", "done", "dropped"]}, "details": STR, "due": STR,
           "add_steps": {"type": "array", "items": STR},
           "complete_steps": {"type": "array", "items": {"type": "integer"}, "description": "Step numbers to mark done"}},
          ["goal_id"]),
      label="Updating a goal")
async def goal_update(ctx: ToolContext, data: dict) -> str:
    async with ctx.sessionmaker() as s:
        g = await s.get(Goal, data["goal_id"])
        if g is None or g.token_id != ctx.token_id:
            raise ToolError("No goal with that id. Use goal_list to find it.")
        steps = json.loads(g.steps or "[]")
        for i in data.get("complete_steps", []):
            if 0 <= i < len(steps):
                steps[i]["done"] = True
        steps += [{"text": t, "done": False} for t in data.get("add_steps", [])]
        g.steps = json.dumps(steps)
        for k in ("status", "details", "due"):
            if k in data:
                setattr(g, k, data[k])
        g.updated_at = utcnow()
        await s.commit()
    return "Goal updated:\n" + _goal_line(g)


@tool("goal_list", "List the person's goals and their progress.",
      obj({"status": {"enum": ["active", "done", "dropped", "all"]}}), label="Reviewing goals")
async def goal_list(ctx: ToolContext, data: dict) -> str:
    async with ctx.sessionmaker() as s:
        q = select(Goal).where(Goal.token_id == ctx.token_id)
        if data.get("status", "active") != "all":
            q = q.where(Goal.status == data.get("status", "active"))
        rows = (await s.execute(q.order_by(Goal.created_at))).scalars().all()
    return "\n".join(_goal_line(g) for g in rows) or "No goals yet."


# ------------------------------------------------------------------ schedules and reminders
@tool("schedule_task",
      "Run something later or on repeat, even when the person is away. kind='reminder' just tells them the "
      "message at that time; kind='task' makes you work on the prompt then (e.g. 'Every weekday at 9, check my "
      "inbox and list what needs a reply'). Give run_at for a one-off (ISO, the person's local time) or repeat "
      "for recurring ('daily 09:00', 'weekdays 08:30', 'weekly mon,thu 17:00', 'monthly 1 10:00', 'every 2h').",
      obj({"title": STR, "prompt": {"type": "string", "description": "The reminder text, or the full instructions for the task"},
           "kind": {"enum": ["reminder", "task"]}, "run_at": STR, "repeat": STR}, ["title", "prompt", "kind"]),
      summarize=lambda d: f"Schedule {d['kind']} \"{d['title']}\" {d.get('repeat') or d.get('run_at', '')}",
      label="Scheduling")
async def schedule_task(ctx: ToolContext, data: dict) -> str:
    tz = _tz(ctx)
    now = utcnow()
    try:
        repeat = normalize_repeat(data["repeat"]) if data.get("repeat") else None
        if data.get("run_at"):
            first = parse_when(data["run_at"], tz)
            if first <= now:
                raise ToolError("That time is in the past. Check the current date and time in the context block.")
        elif repeat:
            first = next_run(repeat, tz, now)
        else:
            raise ToolError("Give run_at (one-off) or repeat (recurring).")
    except ScheduleError as exc:
        raise ToolError(str(exc)) from exc
    async with ctx.sessionmaker() as s:
        row = ScheduledTask(token_id=ctx.token_id, title=data["title"][:200], prompt=data["prompt"], kind=data["kind"],
                            repeat=repeat, timezone=tz, next_run_at=first)
        s.add(row)
        await s.commit()
    return f"Scheduled [{row.id}] {row.title}: {describe(repeat, first, tz)} ({tz})."


@tool("schedule_list", "List scheduled reminders and background tasks.", obj({}), label="Checking schedules")
async def schedule_list(ctx: ToolContext, data: dict) -> str:
    async with ctx.sessionmaker() as s:
        rows = (await s.execute(select(ScheduledTask).where(ScheduledTask.token_id == ctx.token_id, ScheduledTask.active.is_(True))
                                .order_by(ScheduledTask.next_run_at))).scalars().all()
    if not rows:
        return "Nothing scheduled."
    return "\n".join(f"[{r.id}] {r.kind}: {r.title} - {describe(r.repeat, r.next_run_at, r.timezone)}" for r in rows)


@tool("schedule_cancel", "Cancel a scheduled reminder or task by id.", obj({"schedule_id": STR}, ["schedule_id"]),
      label="Cancelling a schedule")
async def schedule_cancel(ctx: ToolContext, data: dict) -> str:
    async with ctx.sessionmaker() as s:
        row = await s.get(ScheduledTask, data["schedule_id"])
        if row is None or row.token_id != ctx.token_id:
            raise ToolError("No schedule with that id.")
        row.active = False
        await s.commit()
    return f"Cancelled \"{row.title}\"."


# ------------------------------------------------------------------ documents
@tool("create_document",
      "Write a report, brief, plan, agenda or any document longer than a chat reply, in Markdown. The person "
      "can open, copy and save it from Bea Hub. Use it for research findings, meeting agendas and summaries.",
      obj({"title": STR, "content": {"type": "string", "description": "The full document in Markdown"}}, ["title", "content"]),
      summarize=lambda d: f"Write document \"{d['title']}\"", label="Writing a document")
async def create_document(ctx: ToolContext, data: dict) -> str:
    async with ctx.sessionmaker() as s:
        doc = Document(token_id=ctx.token_id, title=data["title"][:300], content=data["content"], run_id=ctx.run_id)
        s.add(doc)
        await s.commit()
    await ctx.emit("document", {"document_id": doc.id, "title": doc.title})
    return f"Document saved (id {doc.id}). Mention it in your answer; the person can open it from Bea Hub."


@tool("update_document", "Replace the content of a document you wrote earlier.",
      obj({"document_id": STR, "content": STR, "title": STR}, ["document_id", "content"]), label="Editing a document")
async def update_document(ctx: ToolContext, data: dict) -> str:
    async with ctx.sessionmaker() as s:
        doc = await s.get(Document, data["document_id"])
        if doc is None or doc.token_id != ctx.token_id:
            raise ToolError("No document with that id.")
        doc.content = data["content"]
        if data.get("title"):
            doc.title = data["title"][:300]
        doc.updated_at = utcnow()
        await s.commit()
    await ctx.emit("document", {"document_id": doc.id, "title": doc.title})
    return "Document updated."


@tool("list_documents", "List documents you have written for the person, newest first.", obj({}), label="Listing documents")
async def list_documents(ctx: ToolContext, data: dict) -> str:
    async with ctx.sessionmaker() as s:
        rows = (await s.execute(select(Document).where(Document.token_id == ctx.token_id)
                                .order_by(Document.updated_at.desc()).limit(30))).scalars().all()
    return "\n".join(f"[{d.id}] {d.title} ({d.updated_at:%d %b %Y})" for d in rows) or "No documents yet."


@tool("read_document", "Read a document you wrote earlier.", obj({"document_id": STR}, ["document_id"]), label="Reading a document")
async def read_document(ctx: ToolContext, data: dict) -> str:
    async with ctx.sessionmaker() as s:
        doc = await s.get(Document, data["document_id"])
    if doc is None or doc.token_id != ctx.token_id:
        raise ToolError("No document with that id.")
    return f"# {doc.title}\n\n{doc.content}"


# ------------------------------------------------------------------ the person's desktop
@tool("notify",
      "Send the person a notification that pops up on their desktop. Use it in background tasks for anything "
      "that needs their attention, or to flag something important mid-task.",
      obj({"title": STR, "body": STR}, ["title"]), label="Notifying")
async def notify(ctx: ToolContext, data: dict) -> str:
    async with ctx.sessionmaker() as s:
        s.add(Notification(token_id=ctx.token_id, kind="info", title=data["title"][:200], body=data.get("body", ""), run_id=ctx.run_id))
        await s.commit()
    return "Notification sent."


@tool("open_on_desktop",
      "Open an https link in the person's own browser on their computer, e.g. a booking page they need to "
      "finish themselves or a document you found. Only when they are present (not in background tasks).",
      obj({"url": STR}, ["url"]), summarize=lambda d: f"Open {d['url']}", label="Opening a link for you")
async def open_on_desktop(ctx: ToolContext, data: dict) -> str:
    if ctx.background:
        raise ToolError("Nobody is at the computer during a background task; send a notification instead.")
    if not re.match(r"^https://[^\s]+$", data["url"]):
        raise ToolError("Only https links can be opened.")
    await ctx.emit("client_action", {"action": "open_url", "url": data["url"]})
    return "Opened in the person's browser."


@tool("copy_to_clipboard", "Put text on the person's clipboard so they can paste it, e.g. a drafted message.",
      obj({"text": STR}, ["text"]), label="Copying to clipboard")
async def copy_to_clipboard(ctx: ToolContext, data: dict) -> str:
    if ctx.background:
        raise ToolError("Nobody is at the computer during a background task.")
    await ctx.emit("client_action", {"action": "copy", "text": data["text"]})
    return "Copied to the clipboard."


register_group("core")
