import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv

from temporalio.client import (
    Client,
    ScheduleAlreadyRunningError,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleSpec,
    ScheduleUpdate,
)

load_dotenv()


# ============================================================
# CONFIGURATION
# ============================================================

TEMPORAL_SERVER = os.getenv("TEMPORAL_SERVER", "localhost:7233")

SCHEDULE_ID = "gst-daily-schedule"

TASK_QUEUE = "compliance-task-queue"


# ============================================================
# MAIN
# ============================================================

async def main():

    client = await Client.connect(
        TEMPORAL_SERVER
    )

    schedule = Schedule(
        action=ScheduleActionStartWorkflow(
            "GSTWorkflow",
            id="gst-daily-workflow",
            task_queue=TASK_QUEUE,
        ),
        spec=ScheduleSpec(
            cron_expressions=["0 12 * * *"],
            time_zone_name="Asia/Kolkata",
        ),
    )

    try:
        await client.create_schedule(SCHEDULE_ID, schedule)
        action = "created"
    except ScheduleAlreadyRunningError:
        handle = client.get_schedule_handle(SCHEDULE_ID)
        await handle.update(lambda _: ScheduleUpdate(schedule=schedule))
        action = "updated"

    print()

    print(
        f"GST daily schedule {action} successfully."
    )

    print(
        "Schedule: Every day at 12:00 PM IST"
    )

    print(
        f"Schedule ID: {SCHEDULE_ID}"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    asyncio.run(main())
