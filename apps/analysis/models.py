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