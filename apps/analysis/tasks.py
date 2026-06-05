"""
Celery tasks for the analysis app.

Each task has one job. The process_dataset task is the entry point —
it will grow significantly in Phase 5 when the analysis engine is built.
For now it transitions the Dataset through its lifecycle states so the
full Celery wiring can be verified end-to-end.
"""

import logging

from celery import shared_task

from .models import Dataset

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def process_dataset(self, dataset_id: str) -> dict:
    """
    Entry point for all dataset processing.

    bind=True gives the task access to `self` so it can retry itself
    on transient failures (network blips, memory spikes) with
    exponential back-off — up to max_retries times.

    Args:
        dataset_id: String representation of the Dataset UUID primary key.

    Returns:
        A dict with the outcome so the result backend has something
        meaningful to store.
    """
    try:
        dataset = Dataset.objects.get(pk=dataset_id)
    except Dataset.DoesNotExist:
        # Do not retry — if the record is gone, retrying won't help.
        logger.error("process_dataset called with unknown dataset_id=%s", dataset_id)
        return {"status": "error", "detail": "Dataset not found."}

    try:
        dataset.status = Dataset.Status.PROCESSING
        dataset.save(update_fields=["status", "updated_at"])

        # ── Analysis engine goes here in Phase 5 ──────────────────────────────
        # For now we just confirm the task runs and mark the record ready.
        logger.info("Processing dataset %s (%s)", dataset_id, dataset.original_filename)

        dataset.status = Dataset.Status.READY
        dataset.save(update_fields=["status", "updated_at"])

        return {"status": "ready", "dataset_id": dataset_id}

    except Exception as exc:
        dataset.status = Dataset.Status.FAILED
        dataset.error_message = str(exc)
        dataset.save(update_fields=["status", "error_message", "updated_at"])

        logger.exception("process_dataset failed for dataset_id=%s", dataset_id)

        # Retry with exponential back-off: 2^1=2s, 2^2=4s, 2^3=8s.
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)