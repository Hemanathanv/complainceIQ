import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleSpec,
)

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


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

    # --------------------------------------------------------
    # Delete existing schedule
    # --------------------------------------------------------

    try:

        handle = client.get_schedule_handle(
            SCHEDULE_ID
        )

        await handle.delete()

        print(
            "Existing GST schedule deleted."
        )

    except Exception:

        print(
            "No existing GST schedule found."
        )

    # --------------------------------------------------------
    # Create new schedule
    # --------------------------------------------------------

    await client.create_schedule(

        SCHEDULE_ID,

        Schedule(

            action=ScheduleActionStartWorkflow(

                "GSTWorkflow",

                id="gst-daily-workflow",

                task_queue=TASK_QUEUE,
            ),

            spec=ScheduleSpec(

                cron_expressions=[
                    "0 12 * * *"
                ],

                time_zone_name="Asia/Kolkata",
            ),
        ),
    )

    print()

    print(
        "GST daily schedule created successfully."
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
