"""
Celery tasks for the analysis app.
"""

import logging
from pathlib import Path

from celery import shared_task

from .engine import AnalysisEngine
from .models import AnalysisResult, Dataset

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def process_dataset(self, dataset_id: str) -> dict:
    """
    Load → analyse → persist results for a single Dataset record.

    The engine does all computation. This task only coordinates:
    it sets status flags, calls the engine, and writes the result.
    """
    try:
        dataset = Dataset.objects.get(pk=dataset_id)
    except Dataset.DoesNotExist:
        logger.error("process_dataset: unknown dataset_id=%s", dataset_id)
        return {"status": "error", "detail": "Dataset not found."}

    try:
        dataset.status = Dataset.Status.PROCESSING
        dataset.save(update_fields=["status", "updated_at"])

        report = AnalysisEngine(path=Path(dataset.file.path)).run()

        # Persist the result — update_or_create is safe on retry.
        AnalysisResult.objects.update_or_create(
            dataset=dataset,
            defaults={
                "row_count": report.row_count,
                "column_count": report.column_count,
                "column_names": report.column_names,
                "dtypes": report.dtypes,
                "missing_counts": report.missing_counts,
                "missing_pct": report.missing_pct,
                "duplicate_row_count": report.duplicate_row_count,
                "numeric_stats": report.numeric_stats,
                "categorical_stats": report.categorical_stats,
                "correlation_matrix": report.correlation_matrix,
                "sample_rows": report.sample_rows,
            },
        )

        dataset.status = Dataset.Status.READY
        dataset.row_count = report.row_count
        dataset.column_count = report.column_count
        dataset.save(update_fields=["status", "row_count", "column_count", "updated_at"])

        logger.info("process_dataset succeeded for dataset_id=%s", dataset_id)
        return {"status": "ready", "dataset_id": dataset_id}

    except Exception as exc:
        dataset.status = Dataset.Status.FAILED
        dataset.error_message = str(exc)
        dataset.save(update_fields=["status", "error_message", "updated_at"])
        logger.exception("process_dataset failed for dataset_id=%s", dataset_id)
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
    

@shared_task(bind=True, max_retries=2)
def run_pipeline(self, pipeline_id: str) -> dict:
    """
    Execute all steps of an AnalysisPipeline in order.

    Delegates all logic to PipelineExecutor — this task is intentionally
    thin: it only handles Celery lifecycle and top-level error capture.
    """
    from .models import AnalysisPipeline
    from .pipeline_executor import PipelineExecutor

    try:
        pipeline = AnalysisPipeline.objects.get(pk=pipeline_id)
    except AnalysisPipeline.DoesNotExist:
        logger.error("run_pipeline: unknown pipeline_id=%s", pipeline_id)
        return {"status": "error", "detail": "Pipeline not found."}

    try:
        executor = PipelineExecutor(pipeline)
        executor.run()
        return {"status": pipeline.status, "pipeline_id": pipeline_id}
    except Exception as exc:
        pipeline.status = AnalysisPipeline.Status.FAILED
        pipeline.error_message = str(exc)
        pipeline.save(update_fields=["status", "error_message", "updated_at"])
        logger.exception("run_pipeline failed for pipeline_id=%s", pipeline_id)
        raise self.retry(exc=exc, countdown=10)