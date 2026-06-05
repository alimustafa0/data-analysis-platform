"""Root URL configuration."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.core.urls", namespace="core")),
    path("analysis/", include("apps.analysis.urls", namespace="analysis")),
] + static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])