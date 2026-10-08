import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from temporal.activities.gst_activity import (
    sync_gst_records,
    get_gst_records,
    run_and_persist_gst_batch,
)

from temporal.workflows.gst_workflow import GSTWorkflow

load_dotenv()


# ============================================================
# CONFIGURATION
# ============================================================

TEMPORAL_SERVER = os.getenv("TEMPORAL_SERVER", "localhost:7233")

TASK_QUEUE = "compliance-task-queue"

MAX_ACTIVITY_THREADS = 1


# ============================================================
# MAIN
# ============================================================

async def main():

    print(
        "===================================="
    )

    print(
        "Starting GST Compliance Worker"
    )

    print(
        "===================================="
    )

    # --------------------------------------------------------
    # Connect to Temporal
    # --------------------------------------------------------

    client = await Client.connect(
        TEMPORAL_SERVER
    )

    print(
        f"Temporal Server: {TEMPORAL_SERVER}"
    )

    print(
        f"Task Queue: {TASK_QUEUE}"
    )

    # --------------------------------------------------------
    # Thread pool
    # --------------------------------------------------------

    activity_executor = ThreadPoolExecutor(
        max_workers=MAX_ACTIVITY_THREADS
    )

    # --------------------------------------------------------
    # Create worker
    # --------------------------------------------------------

    worker = Worker(

        client,

        task_queue=TASK_QUEUE,

        workflows=[
            GSTWorkflow,
        ],

        activities=[
            sync_gst_records,
            get_gst_records,
            run_and_persist_gst_batch,
        ],

        activity_executor=activity_executor,
    )

    print(
        f"Max Activity Threads: "
        f"{MAX_ACTIVITY_THREADS}"
    )

    print()

    print(
        "GST Compliance Worker started."
    )

    print(
        "Waiting for Temporal tasks..."
    )

    print()

    # --------------------------------------------------------
    # Start worker
    # --------------------------------------------------------

    await worker.run()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    asyncio.run(main())
