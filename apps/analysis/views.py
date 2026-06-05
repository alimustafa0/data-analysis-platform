"""
Views for the analysis app.
"""

import uuid
from pathlib import Path

from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.conf import settings

from .forms import DatasetUploadForm
from .models import AnalysisPipeline, AnalysisResult, Dataset, PipelineStep
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
    datasets = Dataset.objects.prefetch_related("pipelines").all()
    return render(request, "analysis/list.html", {"datasets": datasets})

@require_http_methods(["GET"])
def pipeline_create(request: HttpRequest, dataset_pk: uuid.UUID) -> HttpResponse:
    """
    Render the pipeline configuration wizard.

    The dataset's AnalysisResult is used to pre-fill smart defaults —
    for example, which columns have missing values and what the best
    imputation strategy is given their dtype and missing percentage.
    """
    dataset = get_object_or_404(Dataset, pk=dataset_pk)
    analysis_result = getattr(dataset, "result", None)

    if not analysis_result:
        # EDA hasn't completed yet — send them back to the result page.
        return redirect("analysis:result", pk=dataset_pk)

    # ── Build smart defaults ──────────────────────────────────────────────────
    # For each column, suggest the best missing-value strategy automatically.
    cleaning_suggestions: dict = {}
    for col in analysis_result.column_names:
        missing_pct = analysis_result.missing_pct.get(col, 0)
        dtype = analysis_result.dtypes.get(col, "String")
        is_numeric = any(t in dtype for t in ("Int", "Float", "UInt"))

        if missing_pct == 0:
            strategy = "none"
        elif missing_pct > 50:
            strategy = "drop_column"
        elif missing_pct > 30:
            strategy = "drop_rows"
        elif is_numeric:
            strategy = "median"   # Robust to skew.
        else:
            strategy = "mode"

        cleaning_suggestions[col] = {
            "missing_pct": missing_pct,
            "dtype": dtype,
            "is_numeric": is_numeric,
            "suggested_strategy": strategy,
        }

    # Model options grouped by task type — shown in the model training section.
    model_catalog: dict = {
        "classification": [
            {"id": "logistic_regression", "name": "Logistic Regression", "speed": "fast"},
            {"id": "random_forest_clf", "name": "Random Forest", "speed": "medium"},
            {"id": "xgboost_clf", "name": "XGBoost", "speed": "medium"},
            {"id": "lightgbm_clf", "name": "LightGBM", "speed": "fast"},
            {"id": "svm_clf", "name": "Support Vector Machine", "speed": "slow"},
            {"id": "knn_clf", "name": "K-Nearest Neighbors", "speed": "medium"},
        ],
        "regression": [
            {"id": "linear_regression", "name": "Linear Regression", "speed": "fast"},
            {"id": "ridge", "name": "Ridge Regression", "speed": "fast"},
            {"id": "lasso", "name": "Lasso Regression", "speed": "fast"},
            {"id": "random_forest_reg", "name": "Random Forest", "speed": "medium"},
            {"id": "xgboost_reg", "name": "XGBoost", "speed": "medium"},
            {"id": "lightgbm_reg", "name": "LightGBM", "speed": "fast"},
        ],
        "clustering": [
            {"id": "kmeans", "name": "K-Means", "speed": "fast"},
            {"id": "dbscan", "name": "DBSCAN", "speed": "medium"},
            {"id": "agglomerative", "name": "Agglomerative Clustering", "speed": "slow"},
        ],
    }

    return render(
        request,
        "analysis/pipeline_create.html",
        {
            "dataset": dataset,
            "result": analysis_result,
            "cleaning_suggestions": cleaning_suggestions,
            "model_catalog_json": model_catalog,
            "numeric_columns": [
                col for col, s in cleaning_suggestions.items() if s["is_numeric"]
            ],
            "categorical_columns": [
                col for col, s in cleaning_suggestions.items() if not s["is_numeric"]
            ],
            "steps_available": [
                ("cleaning", "Data Cleaning"),
                ("encoding", "Feature Encoding"),
                ("scaling", "Feature Scaling"),
                ("statistical_tests", "Statistical Tests"),
                ("model_training", "Model Training"),
                ("report", "Report"),
            ],
        },
    )


@require_http_methods(["POST"])
def pipeline_submit(request: HttpRequest, dataset_pk: uuid.UUID) -> HttpResponse:
    """
    Accept the wizard form submission, create the pipeline and its steps,
    dispatch the Celery execution task, and redirect to the pipeline status page.
    """
    dataset = get_object_or_404(Dataset, pk=dataset_pk)

    # ── Read step selections from POST ────────────────────────────────────────
    selected_steps: list[str] = request.POST.getlist("steps")

    pipeline = AnalysisPipeline.objects.create(
        dataset=dataset,
        name=request.POST.get("pipeline_name", "Analysis Pipeline"),
        status=AnalysisPipeline.Status.PENDING,
    )

    order = 1

    # ── Cleaning step ─────────────────────────────────────────────────────────
    if "cleaning" in selected_steps:
        cleaning_config: dict = {"missing_values": {}, "outliers": {}, "drop_duplicates": False}

        col_names = request.POST.getlist("col_names")
        for col in col_names:
            strategy = request.POST.get(f"mv_strategy_{col}", "auto")
            cleaning_config["missing_values"][col] = {
                "strategy": strategy,
                "auto": strategy == "auto",
            }

        cleaning_config["outliers"] = {
            "enabled": "outliers" in request.POST,
            "method": request.POST.get("outlier_method", "iqr"),
            "action": request.POST.get("outlier_action", "cap"),
            "auto": True,
        }
        cleaning_config["drop_duplicates"] = "drop_duplicates" in request.POST

        PipelineStep.objects.create(
            pipeline=pipeline,
            step_type=PipelineStep.StepType.CLEANING,
            order=order,
            config=cleaning_config,
        )
        order += 1

    # ── Encoding step ─────────────────────────────────────────────────────────
    if "encoding" in selected_steps:
        PipelineStep.objects.create(
            pipeline=pipeline,
            step_type=PipelineStep.StepType.ENCODING,
            order=order,
            config={
                "strategy": request.POST.get("encoding_strategy", "auto"),
                "auto": request.POST.get("encoding_strategy", "auto") == "auto",
            },
        )
        order += 1

    # ── Scaling step ──────────────────────────────────────────────────────────
    if "scaling" in selected_steps:
        PipelineStep.objects.create(
            pipeline=pipeline,
            step_type=PipelineStep.StepType.SCALING,
            order=order,
            config={
                "strategy": request.POST.get("scaling_strategy", "auto"),
                "auto": request.POST.get("scaling_strategy", "auto") == "auto",
            },
        )
        order += 1

    # ── Statistical tests step ────────────────────────────────────────────────
    if "statistical_tests" in selected_steps:
        PipelineStep.objects.create(
            pipeline=pipeline,
            step_type=PipelineStep.StepType.STATISTICAL_TESTS,
            order=order,
            config={"auto": True},
        )
        order += 1

    # ── Model training step ───────────────────────────────────────────────────
    if "model_training" in selected_steps:
        PipelineStep.objects.create(
            pipeline=pipeline,
            step_type=PipelineStep.StepType.MODEL_TRAINING,
            order=order,
            config={
                "target_column": request.POST.get("target_column", ""),
                "task_type": request.POST.get("task_type", "auto"),
                "model": request.POST.get("model_id", "auto"),
                "auto_params": request.POST.get("auto_params", "true") == "true",
                "test_size": float(request.POST.get("test_size", 0.2)),
                "cross_validation": "cross_validation" in request.POST,
                "cv_folds": int(request.POST.get("cv_folds", 5)),
            },
        )
        order += 1

    # ── Report step ───────────────────────────────────────────────────────────
    if "report" in selected_steps:
        PipelineStep.objects.create(
            pipeline=pipeline,
            step_type=PipelineStep.StepType.REPORT,
            order=order,
            config={"format": request.POST.get("report_format", "html")},
        )

    # Dispatch execution — returns immediately.
    from .tasks import run_pipeline
    run_pipeline.delay(str(pipeline.pk))

    return redirect("analysis:pipeline_status", pk=pipeline.pk)

@require_http_methods(["GET"])
def pipeline_status(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """Render the pipeline execution status and per-step results."""
    pipeline = get_object_or_404(AnalysisPipeline, pk=pk)
    steps = pipeline.steps.all()

    auto_refresh = pipeline.status in (
        AnalysisPipeline.Status.PENDING,
        AnalysisPipeline.Status.RUNNING,
    )

    model_step = steps.filter(
        step_type=PipelineStep.StepType.MODEL_TRAINING,
        status=PipelineStep.Status.COMPLETED,
    ).first()

    feature_importance: list[dict] = []
    model_metrics: dict = {}
    cv_result: dict = {}

    if model_step and model_step.result:
        feature_importance = model_step.result.get("feature_importance", [])
        model_metrics      = model_step.result.get("metrics", {})
        cv_result          = model_step.result.get("cross_validation", {})

    # Report URL — served directly from media storage.
    report_step = steps.filter(
        step_type=PipelineStep.StepType.REPORT,
        status=PipelineStep.Status.COMPLETED,
    ).first()

    report_url: str | None = None
    if report_step and report_step.result.get("report_file"):
        report_url = settings.MEDIA_URL + report_step.result["report_file"]

    return render(
        request,
        "analysis/pipeline_status.html",
        {
            "pipeline":           pipeline,
            "steps":              steps,
            "auto_refresh":       auto_refresh,
            "feature_importance": feature_importance,
            "model_metrics":      model_metrics,
            "cv_result":          cv_result,
            "has_model":          model_step is not None,
            "report_url":         report_url,
        },
    )

@require_http_methods(["GET"])
def model_download(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """
    Stream the trained model .joblib file as a download.

    The file path is stored in the completed MODEL_TRAINING step's result
    dict — we never construct paths from user input directly.
    """
    from django.http import FileResponse

    pipeline = get_object_or_404(AnalysisPipeline, pk=pk)

    step = pipeline.steps.filter(
        step_type=PipelineStep.StepType.MODEL_TRAINING,
        status=PipelineStep.Status.COMPLETED,
    ).first()

    if not step or not step.result.get("model_file"):
        from django.http import Http404
        raise Http404("No trained model found for this pipeline.")

    model_path = Path(settings.MEDIA_ROOT) / step.result["model_file"]

    if not model_path.exists():
        from django.http import Http404
        raise Http404("Model file not found on disk.")

    stem = Path(pipeline.dataset.original_filename).stem
    filename = f"model_{stem}_{step.result.get('model', 'model')}.joblib"

    return FileResponse(
        open(model_path, "rb"),
        as_attachment=True,
        filename=filename,
    )