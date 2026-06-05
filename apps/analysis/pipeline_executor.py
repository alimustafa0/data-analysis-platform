"""
PipelineExecutor — orchestrates step-by-step execution of an AnalysisPipeline.

Design principles:
    - The executor owns the DataFrame lifecycle: loads it once, threads it
      through every transformation step, saves the final version at the end.
    - Each step handler is a pure method: receives a DataFrame, returns a
      (DataFrame, result_dict) pair. This makes individual steps unit-testable.
    - All values stored in result dicts are plain Python types (int, float,
      str, list, dict) — no numpy scalars, no pandas objects. JSON-safe by
      construction.
    - Failures are isolated per step. A failing cleaning step does not
      prevent subsequent steps from attempting to run.
"""

import io
import logging
from pathlib import Path
from typing import Any

import pandas as pd
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from .models import AnalysisPipeline, PipelineStep

logger = logging.getLogger(__name__)


# ─── Model registry ───────────────────────────────────────────────────────────

def _build_model_registry() -> dict:
    """
    Build the model registry lazily so missing optional packages
    (xgboost, lightgbm) degrade gracefully rather than crashing at import.
    """
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.linear_model import (
        Lasso, LinearRegression, LogisticRegression, Ridge,
    )
    from sklearn.neighbors import KNeighborsClassifier
    from sklearn.svm import SVC
    from sklearn.cluster import AgglomerativeClustering, DBSCAN, KMeans

    registry: dict = {
        "logistic_regression":  lambda: LogisticRegression(max_iter=1000, random_state=42),
        "random_forest_clf":    lambda: RandomForestClassifier(n_estimators=100, random_state=42),
        "svm_clf":              lambda: SVC(probability=True, random_state=42),
        "knn_clf":              lambda: KNeighborsClassifier(n_neighbors=5),
        "linear_regression":    lambda: LinearRegression(),
        "ridge":                lambda: Ridge(alpha=1.0),
        "lasso":                lambda: Lasso(alpha=1.0),
        "random_forest_reg":    lambda: RandomForestRegressor(n_estimators=100, random_state=42),
        "kmeans":               lambda: KMeans(n_clusters=3, random_state=42, n_init="auto"),
        "dbscan":               lambda: DBSCAN(eps=0.5, min_samples=5),
        "agglomerative":        lambda: AgglomerativeClustering(n_clusters=3),
    }

    try:
        from xgboost import XGBClassifier, XGBRegressor
        registry["xgboost_clf"] = lambda: XGBClassifier(
            random_state=42, eval_metric="logloss", verbosity=0
        )
        registry["xgboost_reg"] = lambda: XGBRegressor(random_state=42, verbosity=0)
    except ImportError:
        logger.warning("xgboost not installed — XGBoost models unavailable.")

    try:
        from lightgbm import LGBMClassifier, LGBMRegressor
        registry["lightgbm_clf"] = lambda: LGBMClassifier(random_state=42, verbose=-1)
        registry["lightgbm_reg"] = lambda: LGBMRegressor(random_state=42, verbose=-1)
    except ImportError:
        logger.warning("lightgbm not installed — LightGBM models unavailable.")

    return registry


class PipelineExecutor:
    """
    Loads a dataset, runs each configured PipelineStep in order,
    and saves the final transformed DataFrame back to the pipeline record.
    """

    def __init__(self, pipeline: AnalysisPipeline) -> None:
        self._pipeline = pipeline
        self._dataset = pipeline.dataset
        self._df: pd.DataFrame | None = None

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Entry point. Load → execute steps → save → mark pipeline status."""
        logger.info("PipelineExecutor starting pipeline_id=%s", self._pipeline.pk)

        try:
            self._df = self._load_dataset()
        except Exception as exc:
            logger.exception("Failed to load dataset for pipeline %s", self._pipeline.pk)
            self._pipeline.status = AnalysisPipeline.Status.FAILED
            self._pipeline.error_message = f"Dataset load failed: {exc}"
            self._pipeline.save(update_fields=["status", "error_message", "updated_at"])
            return

        for step in self._pipeline.steps.all():
            self._execute_step(step)

        # Save final transformed DataFrame as the pipeline's processed file.
        if self._df is not None:
            self._save_processed_file()

        # Pipeline is complete if no step failed.
        any_failed = self._pipeline.steps.filter(
            status=PipelineStep.Status.FAILED
        ).exists()
        self._pipeline.status = (
            AnalysisPipeline.Status.FAILED
            if any_failed
            else AnalysisPipeline.Status.COMPLETED
        )
        self._pipeline.save(update_fields=["status", "updated_at"])
        logger.info(
            "PipelineExecutor finished pipeline_id=%s status=%s",
            self._pipeline.pk,
            self._pipeline.status,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Step dispatch
    # ──────────────────────────────────────────────────────────────────────────

    def _execute_step(self, step: PipelineStep) -> None:
        """Wrap a single step with timing, status transitions, and error capture."""
        step.status = PipelineStep.Status.RUNNING
        step.started_at = timezone.now()
        step.save(update_fields=["status", "started_at"])

        try:
            result: dict[str, Any]

            if step.step_type == PipelineStep.StepType.CLEANING:
                self._df, result = self._run_cleaning(step)

            elif step.step_type == PipelineStep.StepType.ENCODING:
                self._df, result = self._run_encoding(step)

            elif step.step_type == PipelineStep.StepType.SCALING:
                self._df, result = self._run_scaling(step)

            elif step.step_type == PipelineStep.StepType.STATISTICAL_TESTS:
                result = self._run_statistical_tests(step)

            elif step.step_type == PipelineStep.StepType.MODEL_TRAINING:
                result = self._run_model_training(step)

            else:
                result = {"message": f"Step type '{step.step_type}' not yet implemented."}

            step.result = result
            step.status = PipelineStep.Status.COMPLETED

        except Exception as exc:
            logger.exception("Step %s failed for pipeline %s", step.step_type, self._pipeline.pk)
            step.status = PipelineStep.Status.FAILED
            step.error_message = str(exc)

        finally:
            step.completed_at = timezone.now()
            step.save(update_fields=["result", "status", "error_message", "completed_at"])

    # ──────────────────────────────────────────────────────────────────────────
    # Data loading / saving
    # ──────────────────────────────────────────────────────────────────────────

    def _load_dataset(self) -> pd.DataFrame:
        """Load the original uploaded file into a pandas DataFrame."""
        path = Path(self._dataset.file.path)
        ext = path.suffix.lower()

        loaders = {
            ".csv":     lambda p: pd.read_csv(p, on_bad_lines="skip"),
            ".xlsx":    lambda p: pd.read_excel(p, engine="openpyxl"),
            ".xls":     lambda p: pd.read_excel(p),
            ".parquet": lambda p: pd.read_parquet(p),
        }
        loader = loaders.get(ext)
        if loader is None:
            raise ValueError(f"Unsupported file extension: {ext}")
        return loader(path)

    def _save_processed_file(self) -> None:
        """Persist the final transformed DataFrame as CSV on the pipeline record."""
        if self._df is None:
            return
        buffer = io.BytesIO()
        self._df.to_csv(buffer, index=False)
        buffer.seek(0)
        stem = Path(self._dataset.original_filename).stem
        self._pipeline.processed_file.save(
            f"processed_{stem}.csv",
            ContentFile(buffer.read()),
            save=True,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Step handlers
    # ──────────────────────────────────────────────────────────────────────────

    def _run_cleaning(
        self, step: PipelineStep
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """
        Apply missing-value imputation, outlier handling, and duplicate removal.
        Each column's strategy comes from step.config — 'auto' triggers smart
        selection based on dtype and missing percentage.
        """
        df = self._df.copy()
        config = step.config
        result: dict[str, Any] = {
            "rows_before": len(df),
            "cols_before": len(df.columns),
            "actions": [],
        }

        # ── Missing values ─────────────────────────────────────────────────────
        mv_config: dict = config.get("missing_values", {})
        for col, col_cfg in mv_config.items():
            if col not in df.columns:
                continue
            missing_before = int(df[col].isna().sum())
            if missing_before == 0:
                continue

            strategy: str = col_cfg.get("strategy", "auto")
            if strategy == "auto":
                strategy = self._smart_imputation_strategy(df, col)

            if strategy == "none":
                pass
            elif strategy == "mean" and pd.api.types.is_numeric_dtype(df[col]):
                df[col] = df[col].fillna(df[col].mean())
            elif strategy == "median" and pd.api.types.is_numeric_dtype(df[col]):
                df[col] = df[col].fillna(df[col].median())
            elif strategy == "mode":
                mode_val = df[col].mode()
                if not mode_val.empty:
                    df[col] = df[col].fillna(mode_val.iloc[0])
            elif strategy == "drop_rows":
                df = df.dropna(subset=[col])
            elif strategy == "drop_column":
                df = df.drop(columns=[col])
            elif strategy == "forward_fill":
                df[col] = df[col].ffill()
            elif strategy == "backward_fill":
                df[col] = df[col].bfill()
            elif strategy == "constant":
                fill = 0 if pd.api.types.is_numeric_dtype(df[col]) else "Unknown"
                df[col] = df[col].fillna(fill)
            elif strategy == "knn" and col in df.columns:
                from sklearn.impute import KNNImputer
                if pd.api.types.is_numeric_dtype(df[col]):
                    imp = KNNImputer(n_neighbors=5)
                    df[[col]] = imp.fit_transform(df[[col]])
            elif strategy == "iterative" and col in df.columns:
                from sklearn.impute import IterativeImputer
                if pd.api.types.is_numeric_dtype(df[col]):
                    imp = IterativeImputer(max_iter=10, random_state=42)
                    df[[col]] = imp.fit_transform(df[[col]])

            if col in df.columns:
                result["actions"].append({
                    "column": col,
                    "action": f"imputed ({strategy})",
                    "missing_filled": missing_before - int(df[col].isna().sum()),
                })

        # ── Duplicates ─────────────────────────────────────────────────────────
        if config.get("drop_duplicates", False):
            rows_before = len(df)
            df = df.drop_duplicates()
            removed = rows_before - len(df)
            if removed:
                result["actions"].append({"action": "duplicates_removed", "count": removed})

        # ── Outliers ───────────────────────────────────────────────────────────
        outlier_cfg = config.get("outliers", {})
        if outlier_cfg.get("enabled", False):
            method = outlier_cfg.get("method", "iqr")
            action = outlier_cfg.get("action", "cap")
            numeric_cols = df.select_dtypes(include="number").columns.tolist()
            outlier_count = 0

            for col in numeric_cols:
                if method == "iqr":
                    q1, q3 = df[col].quantile(0.25), df[col].quantile(0.75)
                    iqr = q3 - q1
                    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                    mask = (df[col] < lower) | (df[col] > upper)
                elif method == "zscore":
                    z = (df[col] - df[col].mean()) / df[col].std()
                    mask = z.abs() > 3
                    lower, upper = df[col].mean() - 3 * df[col].std(), df[col].mean() + 3 * df[col].std()
                else:
                    continue

                n_outliers = int(mask.sum())
                if n_outliers == 0:
                    continue

                if action == "cap":
                    df[col] = df[col].clip(lower=lower, upper=upper)
                elif action == "remove":
                    df = df[~mask]
                elif action == "transform":
                    df[col] = df[col].apply(lambda x: x if x > 0 else 0)
                    df[col] = df[col].apply(lambda x: float(f"{x:.6f}") if x > 0 else 0)

                outlier_count += n_outliers

            if outlier_count:
                result["actions"].append({
                    "action": f"outliers_{action}d",
                    "method": method,
                    "count": outlier_count,
                })

        result["rows_after"] = len(df)
        result["cols_after"] = len(df.columns)
        result["rows_removed"] = result["rows_before"] - result["rows_after"]
        result["cols_removed"] = result["cols_before"] - result["cols_after"]
        return df, result

    def _run_encoding(
        self, step: PipelineStep
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Convert categorical columns to numeric representations."""
        from sklearn.preprocessing import LabelEncoder

        df = self._df.copy()
        strategy: str = step.config.get("strategy", "auto")
        result: dict[str, Any] = {"strategy": strategy, "encoded_columns": []}

        cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
        if not cat_cols:
            result["message"] = "No categorical columns found."
            return df, result

        for col in cat_cols:
            cardinality = int(df[col].nunique())
            method: str

            if strategy == "auto":
                method = "one_hot" if cardinality <= 10 else "label"
            else:
                method = strategy

            if method == "onehot" or method == "one_hot":
                dummies = pd.get_dummies(df[col], prefix=col, drop_first=True)
                df = pd.concat([df.drop(columns=[col]), dummies], axis=1)
                result["encoded_columns"].append(
                    {"column": col, "method": "one-hot", "cardinality": cardinality, "new_cols": len(dummies.columns)}
                )
            elif method == "label":
                le = LabelEncoder()
                df[col] = le.fit_transform(df[col].astype(str))
                result["encoded_columns"].append(
                    {"column": col, "method": "label", "cardinality": cardinality}
                )
            elif method == "ordinal":
                from sklearn.preprocessing import OrdinalEncoder
                oe = OrdinalEncoder()
                df[[col]] = oe.fit_transform(df[[col]].astype(str))
                result["encoded_columns"].append(
                    {"column": col, "method": "ordinal", "cardinality": cardinality}
                )

        return df, result

    def _run_scaling(
        self, step: PipelineStep
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Normalise numeric column ranges."""
        from sklearn.preprocessing import MinMaxScaler, RobustScaler, StandardScaler

        df = self._df.copy()
        strategy: str = step.config.get("strategy", "auto")
        result: dict[str, Any] = {"strategy": strategy, "scaled_columns": []}

        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        if not numeric_cols:
            result["message"] = "No numeric columns to scale."
            return df, result

        if strategy == "none":
            result["message"] = "Scaling skipped by user."
            return df, result

        SCALERS = {
            "standard": StandardScaler,
            "minmax":   MinMaxScaler,
            "robust":   RobustScaler,
        }

        if strategy == "auto":
            for col in numeric_cols:
                skewness = abs(float(df[col].skew()))
                scaler_key = "robust" if skewness > 1 else "standard"
                scaler = SCALERS[scaler_key]()
                df[[col]] = scaler.fit_transform(df[[col]])
                result["scaled_columns"].append({"column": col, "scaler": scaler_key})
        elif strategy in SCALERS:
            scaler = SCALERS[strategy]()
            df[numeric_cols] = scaler.fit_transform(df[numeric_cols])
            result["scaled_columns"] = [{"column": c, "scaler": strategy} for c in numeric_cols]

        return df, result

    def _run_statistical_tests(self, step: PipelineStep) -> dict[str, Any]:
        """
        Run normality tests on every numeric column.
        Shapiro-Wilk for n ≤ 5000, Kolmogorov-Smirnov for larger samples.
        """
        from scipy import stats

        df = self._df
        result: dict[str, Any] = {"tests": []}
        numeric_cols = df.select_dtypes(include="number").columns.tolist()

        for col in numeric_cols:
            data = df[col].dropna()
            if len(data) < 3:
                continue

            if len(data) <= 5000:
                stat, p_val = stats.shapiro(data)
                test_name = "Shapiro-Wilk"
            else:
                norm_data = (data - data.mean()) / data.std()
                stat, p_val = stats.kstest(norm_data, "norm")
                test_name = "Kolmogorov-Smirnov"

            result["tests"].append({
                "column":          col,
                "test":            test_name,
                "statistic":       round(float(stat), 4),
                "p_value":         round(float(p_val), 4),
                "is_normal":       bool(p_val > 0.05),
                "interpretation":  "Normal" if p_val > 0.05 else "Non-normal",
                "mean":            round(float(data.mean()), 4),
                "std":             round(float(data.std()), 4),
                "skewness":        round(float(data.skew()), 4),
            })

        if not result["tests"]:
            result["message"] = "No numeric columns available for statistical testing."

        return result

    def _run_model_training(self, step: PipelineStep) -> dict[str, Any]:
        """
        Train the selected model, evaluate it, extract feature importance,
        optionally run cross-validation, and save the model to disk.
        """
        from sklearn.model_selection import cross_val_score, train_test_split
        from sklearn.metrics import (
            accuracy_score,
            classification_report,
            mean_squared_error,
            r2_score,
        )
        import joblib

        df = self._df.copy()
        config = step.config

        target_col: str = config.get("target_column", "")
        if not target_col or target_col not in df.columns:
            return {"error": "Target column not specified or not found in dataset."}

        X = df.drop(columns=[target_col])
        y = df[target_col]

        # Only numeric features survive to model training.
        X = X.select_dtypes(include="number")
        if X.empty:
            return {
                "error": (
                    "No numeric features available. "
                    "Run encoding and scaling steps before model training."
                )
            }

        # Drop rows where X or y has NaN.
        valid_mask = X.notna().all(axis=1) & y.notna()
        X, y = X[valid_mask], y[valid_mask]

        if len(X) < 10:
            return {"error": "Fewer than 10 valid rows remain after filtering — cannot train."}

        # ── Task type detection ────────────────────────────────────────────────
        task_type: str = config.get("task_type", "auto")
        if task_type == "auto":
            task_type = (
                "regression"
                if pd.api.types.is_numeric_dtype(y) and int(y.nunique()) > 10
                else "classification"
            )

        # ── Model selection ────────────────────────────────────────────────────
        model_id: str = config.get("model_id", config.get("model", "auto"))
        registry = _build_model_registry()

        if model_id == "auto" or model_id not in registry:
            from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
            model = (
                RandomForestClassifier(n_estimators=100, random_state=42)
                if task_type == "classification"
                else RandomForestRegressor(n_estimators=100, random_state=42)
            )
            model_id = f"random_forest_{task_type[:3]}"
        else:
            model = registry[model_id]()

        # ── Train / test split ─────────────────────────────────────────────────
        test_size: float = float(config.get("test_size", 0.2))
        stratify = y if task_type == "classification" and y.nunique() <= 20 else None

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=42, stratify=stratify
        )

        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)

        result: dict[str, Any] = {
            "task_type":    task_type,
            "model":        model.__class__.__name__,
            "model_id":     model_id,
            "n_features":   int(X.shape[1]),
            "feature_names":list(X.columns),
            "n_train":      int(len(X_train)),
            "n_test":       int(len(X_test)),
        }

        # ── Evaluation metrics ─────────────────────────────────────────────────
        if task_type == "classification":
            report = classification_report(
                y_test, y_pred, output_dict=True, zero_division=0
            )
            result["metrics"] = {
                "accuracy": round(float(accuracy_score(y_test, y_pred)), 4),
            }
            # Keep macro avg and weighted avg only — cleaner JSON.
            for key in ("macro avg", "weighted avg"):
                if key in report:
                    result["metrics"][key.replace(" ", "_")] = {
                        k: round(float(v), 4)
                        for k, v in report[key].items()
                        if k != "support"
                    }
        else:
            mse = float(mean_squared_error(y_test, y_pred))
            result["metrics"] = {
                "mse":  round(mse, 4),
                "rmse": round(mse ** 0.5, 4),
                "r2":   round(float(r2_score(y_test, y_pred)), 4),
                "mae":  round(float((y_test - y_pred).abs().mean()), 4),
            }

        # ── Feature importance ─────────────────────────────────────────────────
        if hasattr(model, "feature_importances_"):
            pairs = sorted(
                zip(X.columns, model.feature_importances_),
                key=lambda x: x[1],
                reverse=True,
            )
            result["feature_importance"] = [
                {"feature": str(f), "importance": round(float(imp), 4)}
                for f, imp in pairs[:20]
            ]

        # ── Cross-validation ───────────────────────────────────────────────────
        use_cv: bool = config.get("cross_validation", True)
        if use_cv:
            cv_folds: int = int(config.get("cv_folds", 5))
            scoring = "accuracy" if task_type == "classification" else "r2"
            # Re-train on full X for CV (not just train split).
            cv_scores = cross_val_score(model, X, y, cv=cv_folds, scoring=scoring)
            result["cross_validation"] = {
                "scoring": scoring,
                "folds":   cv_folds,
                "scores":  [round(float(s), 4) for s in cv_scores],
                "mean":    round(float(cv_scores.mean()), 4),
                "std":     round(float(cv_scores.std()), 4),
            }

        # ── Persist model to disk ──────────────────────────────────────────────
        model_dir = (
            Path(settings.MEDIA_ROOT)
            / "pipelines"
            / "models"
            / str(self._pipeline.pk)
        )
        model_dir.mkdir(parents=True, exist_ok=True)
        model_path = model_dir / "model.joblib"
        joblib.dump(model, model_path)
        result["model_file"] = str(model_path.relative_to(settings.MEDIA_ROOT))

        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Utilities
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _smart_imputation_strategy(df: pd.DataFrame, col: str) -> str:
        """
        Choose the statistically appropriate missing-value strategy for a column
        based on its dtype and missing percentage.
        """
        missing_pct = df[col].isna().mean() * 100
        is_numeric = pd.api.types.is_numeric_dtype(df[col])

        if missing_pct == 0:
            return "none"
        if missing_pct > 50:
            return "drop_column"
        if missing_pct > 30:
            return "drop_rows"
        if is_numeric:
            skewness = abs(float(df[col].skew()))
            return "median" if skewness > 1 else "mean"
        return "mode"