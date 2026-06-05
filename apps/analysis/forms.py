"""
Forms for the analysis app.

Validation here is the first defensive layer — it checks what the user
claims the file is. The processing engine performs a second, deeper check
on the actual file content (magic bytes) in Phase 5.
"""

from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# Whitelist: only these extensions are accepted at the form level.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".csv", ".xlsx", ".xls", ".parquet"})

# 50 MB ceiling — large enough for real datasets, small enough to stay safe.
MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024


class DatasetUploadForm(forms.Form):
    """Accepts a single dataset file and enforces extension + size limits."""

    file = forms.FileField(
        label=_("Dataset file"),
        help_text=_("Accepted formats: CSV, Excel (.xlsx / .xls), Parquet. Max 50 MB."),
    )

    def clean_file(self):
        """
        Validate extension and size.

        Django calls clean_<fieldname>() automatically during form.is_valid().
        Raising ValidationError here causes form.errors to be populated and
        the view to re-render the form with the error message — never a raw
        exception page.
        """
        uploaded_file = self.cleaned_data["file"]

        # ── Extension check ───────────────────────────────────────────────────
        extension = Path(uploaded_file.name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise ValidationError(
                _("Unsupported file type '%(ext)s'. Allowed: %(allowed)s."),
                params={
                    "ext": extension,
                    "allowed": ", ".join(sorted(ALLOWED_EXTENSIONS)),
                },
            )

        # ── Size check ────────────────────────────────────────────────────────
        if uploaded_file.size > MAX_FILE_SIZE_BYTES:
            raise ValidationError(
                _("File exceeds the 50 MB limit (%(size)s MB uploaded)."),
                params={"size": round(uploaded_file.size / 1024 / 1024, 1)},
            )

        return uploaded_file