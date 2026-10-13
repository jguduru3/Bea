"""Background loop that fires reminders and starts scheduled tasks when they are due."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from ..db import Notification, ScheduledTask, utcnow
from .runner import AgentUnavailable
from .schedule import ScheduleError, next_run

log = logging.getLogger("pom.scheduler")


async def tick(app) -> int:
    """Run everything that is due. Returns how many items fired."""
    now = utcnow()
    fired = 0
    async with app.state.db.sessionmaker() as s:
        due = (await s.execute(select(ScheduledTask).where(ScheduledTask.active.is_(True), ScheduledTask.next_run_at <= now)
                               .order_by(ScheduledTask.next_run_at).limit(20))).scalars().all()
        for job in due:
            fired += 1
            job.last_run_at = now
            if job.kind == "reminder":
                s.add(Notification(token_id=job.token_id, kind="reminder", title=job.title, body=job.prompt))
            else:
                try:
                    job.last_run_id = await app.state.agent.start(
                        job.token_id, job.prompt, client={"timezone": job.timezone}, trigger="schedule", schedule_id=job.id)
                except AgentUnavailable as exc:
                    s.add(Notification(token_id=job.token_id, kind="info", title=f"Couldn't run \"{job.title}\"", body=str(exc)))
            try:
                job.next_run_at = next_run(job.repeat, job.timezone, now) if job.repeat else None
            except ScheduleError:
                job.next_run_at = None
            if job.next_run_at is None:
                job.active = False
        await s.commit()
    return fired


async def loop(app) -> None:
    while True:
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("scheduler tick failed")
        await asyncio.sleep(app.state.settings.scheduler_interval_s)
