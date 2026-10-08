from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


with workflow.unsafe.imports_passed_through():

    from temporal.activities.gst_activity import (
        sync_gst_records,
        get_gst_records,
        run_and_persist_gst_batch,
    )


# ============================================================
# RETRY POLICY - DATABASE
# ============================================================

DATABASE_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(
        seconds=10
    ),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(
        minutes=2
    ),
    maximum_attempts=3,
)


# ============================================================
# GST WORKFLOW
# ============================================================

@workflow.defn
class GSTWorkflow:

    @workflow.run
    async def run(self) -> dict:

        workflow.logger.info(
            "===================================="
        )

        workflow.logger.info(
            "Starting GST Compliance Workflow"
        )

        workflow.logger.info(
            "===================================="
        )

        # ====================================================
        # STEP 1
        # Get GST records
        # ====================================================

        await workflow.execute_activity(
            sync_gst_records,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DATABASE_RETRY_POLICY,
        )

        gst_records = await workflow.execute_activity(
            get_gst_records,
            start_to_close_timeout=timedelta(
                minutes=5
            ),
            retry_policy=DATABASE_RETRY_POLICY,
        )

        total_gstins = len(gst_records)

        workflow.logger.info(
            f"Total GST records: {total_gstins}"
        )

        successful = []
        failed = []

        # Scrape four records per activity, then persist each successful group
        # in one transaction. Full GST payloads stay in the activity process;
        # Temporal history only stores the compact summary.
        for offset in range(0, total_gstins, 4):
            batch = gst_records[offset:offset + 4]
            workflow.logger.info(
                "Processing GST batch %s-%s of %s",
                offset + 1,
                offset + len(batch),
                total_gstins,
            )
            batch_result = await workflow.execute_activity(
                run_and_persist_gst_batch,
                args=[batch],
                start_to_close_timeout=timedelta(hours=3),
                retry_policy=DATABASE_RETRY_POLICY,
            )
            successful.extend(batch_result["successful_records"])
            failed.extend(batch_result["failed_records"])

        # ====================================================
        # STEP 3
        # FINAL RESULT
        # ====================================================

        result = {
            "total": total_gstins,
            "successful": len(successful),
            "failed": len(failed),
            "successful_records": successful,
            "failed_records": failed,
        }

        workflow.logger.info(
            "===================================="
        )

        workflow.logger.info(
            f"GST Workflow Completed | "
            f"Total={total_gstins} | "
            f"Success={len(successful)} | "
            f"Failed={len(failed)}"
        )

        workflow.logger.info(
            "===================================="
        )

        return result
