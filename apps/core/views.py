"""Views for the core app — global pages and error handlers."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def home(request: HttpRequest) -> HttpResponse:
    """Render the application landing page."""
    return render(request, "core/home.html")


def page_not_found(request: HttpRequest, exception: Exception) -> HttpResponse:
    """Custom 404 handler — unknown URL or missing resource."""
    return render(request, "404.html", status=404)


def server_error(request: HttpRequest) -> HttpResponse:
    """
    Custom 500 handler — unhandled server exception.

    Note: this view deliberately receives no exception argument.
    Django's 500 machinery calls it without one.
    """
    return render(request, "500.html", status=500)