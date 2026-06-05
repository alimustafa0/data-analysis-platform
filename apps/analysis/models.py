"""
Models for the analysis app.

A single Dataset record represents one user-uploaded file and tracks
it through its full lifecycle: uploaded → processing → ready → failed.
"""

import uuid
from pathlib import Path

from django.db import models
from django.utils.translation import gettext_lazy as _


def dataset_upload_path(instance: "Dataset", filename: str) -> str:
    """
    Store every upload under a UUID-namespaced directory.

    uploads/
      └── <uuid>/
            └── original_filename.csv

    The UUID prefix prevents filename collisions and makes the path
    impossible to guess — basic security-through-obscurity for file URLs.
    """
    extension = Path(filename).suffix.lower()
    return f"uploads/{instance.pk}/{instance.pk}{extension}"


class Dataset(models.Model):
    """Represents a single user-uploaded dataset and its analysis state."""

    class Status(models.TextChoices):
        UPLOADED = "uploaded", _("Uploaded")
        PROCESSING = "processing", _("Processing")
        READY = "ready", _("Ready")
        FAILED = "failed", _("Failed")

    # ─── Identity ─────────────────────────────────────────────────────────────
    id: models.UUIDField = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text=_("Public identifier — safe to expose in URLs."),
    )
    original_filename: models.CharField = models.CharField(
        max_length=255,
        help_text=_("The filename as the user uploaded it."),
    )

    # ─── Storage ──────────────────────────────────────────────────────────────
    file: models.FileField = models.FileField(
        upload_to=dataset_upload_path,
        help_text=_("The uploaded file, stored under a UUID-namespaced path."),
    )

    # ─── Lifecycle ────────────────────────────────────────────────────────────
    status: models.CharField = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UPLOADED,
        db_index=True,
        help_text=_("Current processing state of this dataset."),
    )
    error_message: models.TextField = models.TextField(
        blank=True,
        help_text=_("Populated when status=failed. Empty otherwise."),
    )

    # ─── Metadata ─────────────────────────────────────────────────────────────
    row_count: models.PositiveIntegerField = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("Populated after successful processing."),
    )
    column_count: models.PositiveIntegerField = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("Populated after successful processing."),
    )
    file_size_bytes: models.PositiveBigIntegerField = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        help_text=_("Size of the uploaded file in bytes."),
    )

    # ─── Timestamps ───────────────────────────────────────────────────────────
    created_at: models.DateTimeField = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Dataset")
        verbose_name_plural = _("Datasets")

    def __str__(self) -> str:
        return f"{self.original_filename} [{self.status}]"

    @property
    def is_ready(self) -> bool:
        """True when analysis results are available to display."""
        return self.status == self.Status.READY

    @property
    def has_failed(self) -> bool:
        """True when processing failed and an error message is available."""
        return self.status == self.Status.FAILED
    

class AnalysisResult(models.Model):
    """
    Stores the computed analysis for one Dataset.

    Keeping results in the database means the UI renders instantly on
    revisit — no recomputation. The heavy JSON fields use PostgreSQL
    JSONB in production (fast, indexable) and SQLite JSON in development.
    """

    dataset: models.OneToOneField = models.OneToOneField(
        Dataset,
        on_delete=models.CASCADE,
        related_name="result",
        primary_key=True,
    )

    # ── Shape ──────────────────────────────────────────────────────────────────
    row_count: models.PositiveIntegerField = models.PositiveIntegerField()
    column_count: models.PositiveIntegerField = models.PositiveIntegerField()
    column_names: models.JSONField = models.JSONField(
        help_text=_("Ordered list of column names.")
    )
    dtypes: models.JSONField = models.JSONField(
        help_text=_("Mapping of column name → inferred dtype string.")
    )

    # ── Quality ────────────────────────────────────────────────────────────────
    missing_counts: models.JSONField = models.JSONField(
        help_text=_("Mapping of column name → missing value count.")
    )
    missing_pct: models.JSONField = models.JSONField(
        help_text=_("Mapping of column name → missing value percentage.")
    )
    duplicate_row_count: models.PositiveIntegerField = models.PositiveIntegerField(
        default=0
    )

    # ── Statistics ─────────────────────────────────────────────────────────────
    numeric_stats: models.JSONField = models.JSONField(
        help_text=_("Descriptive stats for numeric columns (mean, std, quartiles…).")
    )
    categorical_stats: models.JSONField = models.JSONField(
        help_text=_("Value counts and cardinality for categorical columns.")
    )
    correlation_matrix: models.JSONField = models.JSONField(
        help_text=_("Pearson correlation matrix for numeric columns.")
    )

    # ── Sample ─────────────────────────────────────────────────────────────────
    sample_rows: models.JSONField = models.JSONField(
        help_text=_("First 10 rows as a list of dicts for the preview table.")
    )

    computed_at: models.DateTimeField = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Analysis Result")
        verbose_name_plural = _("Analysis Results")

    def __str__(self) -> str:
        return f"Result for {self.dataset.original_filename}"
    

class AnalysisPipeline(models.Model):
    """
    Represents one full analysis run on a Dataset.

    A Dataset can have multiple pipelines — the user might run the
    data with different cleaning strategies or train several models.
    Each pipeline owns an ordered sequence of PipelineStep records.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")

    # ─── Identity ─────────────────────────────────────────────────────────────
    id: models.UUIDField = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    dataset: models.ForeignKey = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="pipelines",
        help_text=_("The source dataset this pipeline operates on."),
    )
    name: models.CharField = models.CharField(
        max_length=255,
        default="Analysis Pipeline",
        help_text=_("Human-readable label — useful when comparing multiple runs."),
    )

    # ─── State ────────────────────────────────────────────────────────────────
    status: models.CharField = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    error_message: models.TextField = models.TextField(
        blank=True,
        help_text=_("Top-level error if the pipeline itself fails to start."),
    )

    # ─── Artefacts ────────────────────────────────────────────────────────────
    processed_file: models.FileField = models.FileField(
        upload_to="pipelines/processed/",
        null=True,
        blank=True,
        help_text=_(
            "The cleaned and transformed dataset produced by the pipeline. "
            "Populated after the cleaning and encoding steps complete. "
            "Subsequent steps (model training, export) read from this file."
        ),
    )

    # ─── Timestamps ───────────────────────────────────────────────────────────
    created_at: models.DateTimeField = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Analysis Pipeline")
        verbose_name_plural = _("Analysis Pipelines")

    def __str__(self) -> str:
        return f"{self.name} → {self.dataset.original_filename} [{self.status}]"

    @property
    def is_complete(self) -> bool:
        """True when every step has finished (completed or skipped)."""
        return self.status == self.Status.COMPLETED

    @property
    def progress_pct(self) -> int:
        """
        Rough completion percentage based on step statuses.
        Used by the UI progress bar.
        """
        steps = self.steps.all()
        if not steps:
            return 0
        done = steps.filter(
            status__in=[PipelineStep.Status.COMPLETED, PipelineStep.Status.SKIPPED]
        ).count()
        return round(done / steps.count() * 100)


class PipelineStep(models.Model):
    """
    A single, atomic operation within an AnalysisPipeline.

    Each step has:
    - A type  (what kind of operation it is)
    - A config (the user's chosen options, or smart defaults if auto=True)
    - A result (the output after execution — metrics, paths, summaries)
    - Its own status and timestamps

    Keeping steps independent means failures are isolated, retries are
    surgical, and the UI can show granular progress.
    """

    class StepType(models.TextChoices):
        CLEANING = "cleaning", _("Data Cleaning")
        ENCODING = "encoding", _("Feature Encoding")
        SCALING = "scaling", _("Feature Scaling")
        FEATURE_SELECTION = "feature_selection", _("Feature Selection")
        EDA = "eda", _("Exploratory Analysis")
        STATISTICAL_TESTS = "statistical_tests", _("Statistical Tests")
        MODEL_TRAINING = "model_training", _("Model Training")
        REPORT = "report", _("Report Generation")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        SKIPPED = "skipped", _("Skipped")
        FAILED = "failed", _("Failed")

    # ─── Identity ─────────────────────────────────────────────────────────────
    id: models.UUIDField = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    pipeline: models.ForeignKey = models.ForeignKey(
        AnalysisPipeline,
        on_delete=models.CASCADE,
        related_name="steps",
    )

    # ─── Definition ───────────────────────────────────────────────────────────
    step_type: models.CharField = models.CharField(
        max_length=30,
        choices=StepType.choices,
        db_index=True,
    )
    order: models.PositiveSmallIntegerField = models.PositiveSmallIntegerField(
        help_text=_("Execution order within the pipeline. Lower = runs first."),
    )

    # ─── Configuration ────────────────────────────────────────────────────────
    config: models.JSONField = models.JSONField(
        default=dict,
        help_text=_(
            "User's chosen options for this step. "
            "Set {'auto': true} for any sub-option to use smart defaults. "
            "Example for cleaning: "
            "{'missing_values': {'price': {'strategy': 'median', 'auto': false}}, "
            "'outliers': {'method': 'iqr', 'action': 'cap', 'auto': true}}"
        ),
    )

    # ─── Output ───────────────────────────────────────────────────────────────
    result: models.JSONField = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            "Populated after execution. Structure varies by step type. "
            "Example for model_training: metrics, feature_importances, model_path."
        ),
    )

    # ─── State ────────────────────────────────────────────────────────────────
    status: models.CharField = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    error_message: models.TextField = models.TextField(blank=True)

    # ─── Timing ───────────────────────────────────────────────────────────────
    started_at: models.DateTimeField = models.DateTimeField(null=True, blank=True)
    completed_at: models.DateTimeField = models.DateTimeField(null=True, blank=True)
    created_at: models.DateTimeField = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order"]
        verbose_name = _("Pipeline Step")
        verbose_name_plural = _("Pipeline Steps")
        constraints = [
            models.UniqueConstraint(
                fields=["pipeline", "order"],
                name="unique_step_order_per_pipeline",
            )
        ]

    def __str__(self) -> str:
        return f"[{self.order}] {self.get_step_type_display()} — {self.status}"

    @property
    def duration_seconds(self) -> float | None:
        """Wall-clock execution time in seconds, or None if not yet complete."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None