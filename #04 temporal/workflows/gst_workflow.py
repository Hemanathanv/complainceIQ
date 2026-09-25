from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


with workflow.unsafe.imports_passed_through():

    from temporal.activities.gst_activity import (
        get_gst_records,
        run_gst_bot,
        update_gst_database,
    )


# ============================================================
# RETRY POLICY - GST BOT
# ============================================================

GST_BOT_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(
        seconds=30
    ),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(
        minutes=5
    ),
    maximum_attempts=3,
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

        # ====================================================
        # STEP 2
        # Process each GST record
        # ====================================================

        for record in gst_records:

            # PostgreSQL UUID as string
            record_id = record["id"]

            gstin = record["gstin"]

            workflow.logger.info(
                "------------------------------------"
            )

            workflow.logger.info(
                f"Processing ID: {record_id}"
            )

            workflow.logger.info(
                f"Processing GSTIN: {gstin}"
            )

            try:

                # ============================================
                # RUN GST BOT
                # ============================================

                output_file = (
                    await workflow.execute_activity(
                        run_gst_bot,

                        args=[
                            record_id,
                            gstin,
                        ],

                        start_to_close_timeout=timedelta(
                            minutes=30
                        ),

                        retry_policy=GST_BOT_RETRY_POLICY,
                    )
                )

                # ============================================
                # UPDATE DATABASE
                # ============================================

                await workflow.execute_activity(

                    update_gst_database,

                    args=[
                        record_id,
                        gstin,
                        output_file,
                    ],

                    start_to_close_timeout=timedelta(
                        minutes=5
                    ),

                    retry_policy=DATABASE_RETRY_POLICY,
                )

                # ============================================
                # SUCCESS
                # ============================================

                successful.append(
                    {
                        "id": record_id,
                        "gstin": gstin,
                    }
                )

                workflow.logger.info(
                    f"Completed successfully | "
                    f"ID={record_id} | "
                    f"GSTIN={gstin}"
                )

            except Exception as exc:

                # ============================================
                # FAILURE
                # ============================================

                failed.append(
                    {
                        "id": record_id,
                        "gstin": gstin,
                        "error": str(exc),
                    }
                )

                workflow.logger.error(
                    f"GSTIN failed | "
                    f"ID={record_id} | "
                    f"GSTIN={gstin} | "
                    f"Error={exc}"
                )

                # Continue processing next GSTIN
                continue

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