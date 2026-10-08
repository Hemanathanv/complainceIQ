"""Temporal worker dedicated to GST Evidence activities."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.worker import Worker

from temporal.activities.gst_evidence_activity import (
    list_gst_evidence_gstins,
    run_gst_evidence,
)
from temporal.workflows.gst_evidence_workflow import GSTEvidenceWorkflow

load_dotenv()

TEMPORAL_SERVER = os.getenv("TEMPORAL_SERVER", "localhost:7233")
TASK_QUEUE = "gst-evidence-task-queue"


async def main() -> None:
    client = await Client.connect(TEMPORAL_SERVER)
    executor = ThreadPoolExecutor(max_workers=1)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[GSTEvidenceWorkflow],
        activities=[list_gst_evidence_gstins, run_gst_evidence],
        activity_executor=executor,
    )
    print(f"GST Evidence worker started on {TASK_QUEUE}", flush=True)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
