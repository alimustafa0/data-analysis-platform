"""
Views for the analysis app.
"""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import DatasetUploadForm
from .models import Dataset
from .tasks import process_dataset


@require_http_methods(["GET", "POST"])
def upload(request: HttpRequest) -> HttpResponse:
    """
    GET  — render the upload form.
    POST — validate, save, dispatch background task, redirect.
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

            # Dispatch to Celery — returns immediately, never blocks the view.
            process_dataset.delay(str(dataset.pk))

            return redirect("analysis:upload")

    else:
        form = DatasetUploadForm()

    return render(request, "analysis/upload.html", {"form": form})