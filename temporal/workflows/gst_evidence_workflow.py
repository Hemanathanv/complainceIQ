"""Daily GST Evidence collection for Infisical credentials."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from temporal.activities.gst_evidence_activity import (
        list_gst_evidence_gstins,
        run_gst_evidence,
    )


@workflow.defn
class GSTEvidenceWorkflow:
    @workflow.run
    async def run(self) -> dict:
        gstins = await workflow.execute_activity(
            list_gst_evidence_gstins,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        completed = []
        failed = []
        for gstin in gstins:
            try:
                result = await workflow.execute_activity(
                    run_gst_evidence,
                    gstin,
                    start_to_close_timeout=timedelta(hours=7),
                    heartbeat_timeout=timedelta(minutes=1),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                completed.append(result)
            except ActivityError:
                workflow.logger.exception("GST Evidence failed for %s", gstin)
                failed.append(gstin)
        return {
            "total": len(gstins),
            "completed": completed,
            "failed": failed,
        }
