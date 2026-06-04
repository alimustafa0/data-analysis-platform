"""Views for the core app — global pages only."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def home(request: HttpRequest) -> HttpResponse:
    """Render the application landing page."""
    return render(request, "core/home.html")