"""
Views for the analysis app.

Each view has exactly one responsibility. The upload view accepts a file,
validates it, persists the record, and redirects — nothing more. Analysis
logic lives in the processing engine (Phase 5), not here.
"""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import DatasetUploadForm
from .models import Dataset


@require_http_methods(["GET", "POST"])
def upload(request: HttpRequest) -> HttpResponse:
    """
    GET  — render the upload form.
    POST — validate, save the file, redirect to a detail page (Phase 5).

    Using @require_http_methods rejects PUT/DELETE/PATCH at the decorator
    level before any view logic runs — cheap, explicit method enforcement.
    """
    if request.method == "POST":
        form = DatasetUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = form.cleaned_data["file"]

            dataset = Dataset(
                original_filename=uploaded_file.name,
                file_size_bytes=uploaded_file.size,
            )
            # Save without committing to DB first so the upload path
            # callable receives the UUID primary key correctly.
            dataset.pk = dataset.id  # uuid4 already set by default
            dataset.file = uploaded_file
            dataset.save()

            # Placeholder redirect — wired to the detail view in Phase 5.
            return redirect("analysis:upload")

        # Form invalid — fall through and re-render with errors.
    else:
        form = DatasetUploadForm()

    return render(request, "analysis/upload.html", {"form": form})