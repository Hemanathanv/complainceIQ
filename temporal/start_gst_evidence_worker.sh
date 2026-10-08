#!/bin/sh
set -eu

attempt=0
until python /app/temporal/schedules/gst_evidence_schedule.py; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "Could not register the GST Evidence schedule after 30 attempts." >&2
        exit 1
    fi
    echo "Temporal is not ready; retrying Evidence schedule registration in 5 seconds..."
    sleep 5
done

exec python /app/temporal/workers/gst_evidence_worker.py
