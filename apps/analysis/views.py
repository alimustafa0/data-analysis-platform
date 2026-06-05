"""
Views for the analysis app.
"""

import uuid
from pathlib import Path

from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .forms import DatasetUploadForm
from .models import Dataset
from .tasks import process_dataset


def _format_file_size(size_bytes: int | None) -> str:
    """Convert a raw byte count to a human-readable string."""
    if not size_bytes:
        return "—"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


@require_http_methods(["GET", "POST"])
def upload(request: HttpRequest) -> HttpResponse:
    """
    GET  — render the upload form.
    POST — validate, persist, dispatch task, redirect to results.
    """
    if request.method == "POST":
        form = DatasetUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = form.cleaned_data["file"]

            dataset = Dataset(
                original_filename=uploaded_file.name,
                file_size_bytes=uploaded_file.size,
            )
            dataset.file = uploaded_file
            dataset.save()

            process_dataset.delay(str(dataset.pk))

            # Redirect immediately — the result page handles the processing state.
            return redirect("analysis:result", pk=dataset.pk)

    else:
        form = DatasetUploadForm()

    return render(request, "analysis/upload.html", {"form": form})


@require_http_methods(["GET"])
def result(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """
    Render the full analysis results dashboard.

    Chart payloads are built here — the template stays logic-free and
    only renders what it receives. Plotly consumes the JSON client-side.
    """
    dataset = get_object_or_404(Dataset, pk=pk)
    analysis_result = getattr(dataset, "result", None)

    # Build categorical chart payloads.
    categorical_charts: list[dict] = []
    if analysis_result and analysis_result.categorical_stats:
        for col, stats in analysis_result.categorical_stats.items():
            categorical_charts.append(
                {
                    "column": col,
                    "cardinality": stats["cardinality"],
                    "values": [item["value"] for item in stats["top_values"]],
                    "counts": [item["count"] for item in stats["top_values"]],
                }
            )

    # Build numeric chart payloads.
    numeric_charts: list[dict] = []
    if analysis_result and analysis_result.numeric_stats:
        for col, stats in analysis_result.numeric_stats.items():
            numeric_charts.append({"column": col, "stats": stats})

    return render(
        request,
        "analysis/result.html",
        {
            "dataset": dataset,
            "result": analysis_result,
            "file_size": _format_file_size(dataset.file_size_bytes),
            "categorical_charts": categorical_charts,
            "numeric_charts": numeric_charts,
            "numeric_stat_keys": ["count", "mean", "std", "min", "25%", "50%", "75%", "max"],
            "overview_stats": [
                ("Rows", f"{analysis_result.row_count:,}" if analysis_result else "—"),
                ("Columns", analysis_result.column_count if analysis_result else "—"),
                ("File size", _format_file_size(dataset.file_size_bytes)),
                ("Duplicate rows", analysis_result.duplicate_row_count if analysis_result else "—"),
            ],
        },
    )

@require_http_methods(["GET"])
def dataset_list(request: HttpRequest) -> HttpResponse:
    """Render a history of all uploaded datasets."""
    datasets = Dataset.objects.all()  # Already ordered by -created_at via Meta.
    return render(request, "analysis/list.html", {"datasets": datasets})