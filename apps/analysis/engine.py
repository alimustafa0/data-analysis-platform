"""
AnalysisEngine — pure domain logic, zero Django/Celery dependencies.

Accepts a file path, returns a fully populated AnalysisReport dataclass.
Testable in complete isolation: no database, no broker, no HTTP.

Design notes
────────────
- Polars loads and type-infers the raw file at high speed.
- Pandas handles describe() and corr() because its ecosystem for those
  operations is more mature and produces friendlier output.
- All public methods return plain Python dicts/lists so they serialise
  to JSON without custom encoders.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

logger = logging.getLogger(__name__)

# Maximum rows kept in memory for the categorical value-count scan.
# Datasets larger than this are sampled to keep memory bounded.
_CATEGORICAL_SAMPLE_LIMIT: int = 500_000


@dataclass
class AnalysisReport:
    """
    Structured container for all computed analysis artefacts.

    Every attribute maps directly to an AnalysisResult model field,
    which makes the save step in the task a trivial field assignment.
    """

    row_count: int = 0
    column_count: int = 0
    column_names: list[str] = field(default_factory=list)
    dtypes: dict[str, str] = field(default_factory=dict)
    missing_counts: dict[str, int] = field(default_factory=dict)
    missing_pct: dict[str, float] = field(default_factory=dict)
    duplicate_row_count: int = 0
    numeric_stats: dict[str, Any] = field(default_factory=dict)
    categorical_stats: dict[str, Any] = field(default_factory=dict)
    correlation_matrix: dict[str, Any] = field(default_factory=dict)
    sample_rows: list[dict[str, Any]] = field(default_factory=list)


class AnalysisEngine:
    """
    Orchestrates the full EDA pipeline for a single dataset file.

    Usage
    ─────
        engine = AnalysisEngine(path=Path("/media/uploads/uuid/file.csv"))
        report = engine.run()
    """

    # File extensions → Polars reader method name.
    _READERS: dict[str, str] = {
        ".csv": "read_csv",
        ".parquet": "read_parquet",
        ".xlsx": "read_excel",
        ".xls": "read_excel",
    }

    def __init__(self, path: Path) -> None:
        self._path = path
        self._extension = path.suffix.lower()

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> AnalysisReport:
        """Execute the full pipeline and return a populated AnalysisReport."""
        logger.info("AnalysisEngine starting for %s", self._path.name)

        pl_df = self._load_polars()
        pd_df = self._to_pandas(pl_df)

        report = AnalysisReport(
            row_count=len(pl_df),
            column_count=len(pl_df.columns),
            column_names=pl_df.columns,
            dtypes=self._extract_dtypes(pl_df),
            missing_counts=self._missing_counts(pl_df),
            missing_pct=self._missing_pct(pl_df),
            duplicate_row_count=int(pd_df.duplicated().sum()),
            numeric_stats=self._numeric_stats(pd_df),
            categorical_stats=self._categorical_stats(pl_df),
            correlation_matrix=self._correlation_matrix(pd_df),
            sample_rows=self._sample_rows(pl_df),
        )

        logger.info(
            "AnalysisEngine complete: %d rows × %d cols",
            report.row_count,
            report.column_count,
        )
        return report

    # ──────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _load_polars(self) -> pl.DataFrame:
        """
        Load the file into a Polars DataFrame using the correct reader.

        infer_schema_length=10000 samples more rows before committing to a
        dtype — reduces mis-inference on mixed-type columns.

        ignore_errors=True tells Polars to substitute null for any value it
        cannot cast to the inferred dtype rather than aborting the entire
        load. The missing-value analysis downstream will surface those nulls
        so the user sees them in the dashboard.
        """
        reader_name = self._READERS.get(self._extension)
        if reader_name is None:
            raise ValueError(f"Unsupported extension: {self._extension}")

        reader = getattr(pl, reader_name)

        # kwargs applied selectively — not every reader accepts every argument.
        if self._extension in {".csv"}:
            return reader(
                self._path,
                infer_schema_length=10_000,
                ignore_errors=True,
            )

        if self._extension in {".xlsx", ".xls"}:
            return reader(self._path, infer_schema_length=10_000)

        # .parquet — schema is embedded in the file, no inference needed.
        return reader(self._path)

    def _to_pandas(self, pl_df: pl.DataFrame) -> pd.DataFrame:
        """Convert Polars → Pandas for operations where Pandas excels."""
        return pl_df.to_pandas()

    def _extract_dtypes(self, df: pl.DataFrame) -> dict[str, str]:
        """Return a human-readable dtype string for every column."""
        return {col: str(df[col].dtype) for col in df.columns}

    def _missing_counts(self, df: pl.DataFrame) -> dict[str, int]:
        """Count null values per column."""
        return {col: int(df[col].null_count()) for col in df.columns}

    def _missing_pct(self, df: pl.DataFrame) -> dict[str, float]:
        """Return null percentage per column, rounded to 2 dp."""
        n = len(df)
        if n == 0:
            return {col: 0.0 for col in df.columns}
        return {
            col: round(df[col].null_count() / n * 100, 2)
            for col in df.columns
        }

    def _numeric_stats(self, df: pd.DataFrame) -> dict[str, Any]:
        """
        Descriptive statistics for numeric columns only.

        Returns a nested dict: {column_name: {stat_name: value}}.
        All values are cast to plain Python floats so they serialise
        cleanly to JSON without numpy type errors.
        """
        numeric_df = df.select_dtypes(include="number")
        if numeric_df.empty:
            return {}

        stats = numeric_df.describe(percentiles=[0.25, 0.5, 0.75]).to_dict()
        # Cast numpy scalars → Python floats for JSON serialisation.
        return {
            col: {k: (float(v) if v == v else None) for k, v in col_stats.items()}
            for col, col_stats in stats.items()
        }

    def _categorical_stats(self, df: pl.DataFrame) -> dict[str, Any]:
        """
        Value counts and cardinality for non-numeric columns.

        Limits to top-20 values per column to keep the JSON payload
        bounded regardless of dataset size.
        """
        result: dict[str, Any] = {}
        for col in df.columns:
            if df[col].dtype in (pl.Float32, pl.Float64, pl.Int8, pl.Int16,
                                  pl.Int32, pl.Int64, pl.UInt8, pl.UInt16,
                                  pl.UInt32, pl.UInt64):
                continue

            sample = df if len(df) <= _CATEGORICAL_SAMPLE_LIMIT else df.sample(
                n=_CATEGORICAL_SAMPLE_LIMIT, seed=42
            )
            counts = (
                sample[col]
                .value_counts()
                .sort("count", descending=True)
                .head(20)
            )
            result[col] = {
                "cardinality": int(df[col].n_unique()),
                "top_values": [
                    {"value": str(row[col]), "count": int(row["count"])}
                    for row in counts.to_dicts()
                ],
            }
        return result

    def _correlation_matrix(self, df: pd.DataFrame) -> dict[str, Any]:
        """
        Pearson correlation matrix for numeric columns.

        NaN entries (columns with zero variance) are replaced with None
        so the matrix serialises to valid JSON.
        """
        numeric_df = df.select_dtypes(include="number")
        if numeric_df.shape[1] < 2:
            return {}

        corr = numeric_df.corr()
        return {
            col: {
                other: (round(float(val), 4) if val == val else None)
                for other, val in row.items()
            }
            for col, row in corr.to_dict().items()
        }

    def _sample_rows(self, df: pl.DataFrame, n: int = 10) -> list[dict[str, Any]]:
        """Return the first n rows as a list of plain Python dicts."""
        return [
            {k: (v if not isinstance(v, float) or v == v else None)
             for k, v in row.items()}
            for row in df.head(n).to_dicts()
        ]