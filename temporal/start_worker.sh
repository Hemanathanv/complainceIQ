#!/bin/sh
set -eu

attempt=0
until python /app/temporal/schedules/gst_schedule.py; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "Could not register Temporal schedules after 30 attempts." >&2
        exit 1
    fi
    echo "Temporal is not ready; retrying schedule registration in 5 seconds..."
    sleep 5
done

exec python /app/temporal/workers/gst_worker.py
