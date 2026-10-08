"""Register the daily GST Evidence schedule."""

import asyncio
import os

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleUpdate,
)


SCHEDULE_ID = "gst-evidence-daily-schedule"


async def main() -> None:
    client = await Client.connect(os.getenv("TEMPORAL_SERVER", "localhost:7233"))
    schedule = Schedule(
        action=ScheduleActionStartWorkflow(
            "GSTEvidenceWorkflow",
            id="gst-evidence-daily-workflow",
            task_queue="gst-evidence-task-queue",
        ),
        spec=ScheduleSpec(
            cron_expressions=["0 13 * * *"],
            time_zone_name="Asia/Kolkata",
        ),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
    )
    try:
        await client.create_schedule(SCHEDULE_ID, schedule)
        action = "created"
    except ScheduleAlreadyRunningError:
        await client.get_schedule_handle(SCHEDULE_ID).update(
            lambda _: ScheduleUpdate(schedule=schedule)
        )
        action = "updated"
    print(f"GST Evidence schedule {action}: daily at 13:00 Asia/Kolkata")


if __name__ == "__main__":
    asyncio.run(main())
