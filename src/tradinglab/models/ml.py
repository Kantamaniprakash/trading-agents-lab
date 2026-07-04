"""Walk-forward LightGBM alpha model.

Trains an expanding-window LGBMRegressor on the pooled multi-ticker feature
matrix from ``tradinglab.features.dataset`` and produces out-of-sample
predicted forward returns, which are then mapped to trading signals.

Walk-forward is by DATE (the MultiIndex level ``"date"``) across all tickers
pooled: train/test membership of a row is determined solely by its date. An
embargo of ``max(cfg.embargo_days, cfg.horizon)`` trading dates is removed
from the end of the training window before each test chunk to limit label
overlap leakage: the label at train date ``t`` uses ``close[t+horizon]``, so
the embargo must be at least ``horizon`` dates or training labels would read
prices from inside the test chunk.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from tradinglab.config import MLConfig


@dataclass
class WalkForwardResult:
    predictions: pd.Series             # MultiIndex (ticker, date), only test-period rows
    feature_importance: pd.DataFrame   # index=feature, cols=["gain"] averaged over refits
    n_refits: int


def _make_model(cfg: MLConfig) -> LGBMRegressor:
    """Construct the LightGBM regressor with the fixed research hyperparameters."""
    return LGBMRegressor(
        n_estimators=400,
        learning_rate=0.05,
        num_leaves=31,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_samples=50,
        random_state=cfg.random_state,
        verbose=-1,
    )


def walk_forward_predict(X: pd.DataFrame, y: pd.Series, cfg: MLConfig) -> WalkForwardResult:
    """Expanding-window walk-forward prediction of forward returns.

    Sorts the unique trading dates in ``X``; test dates are those on/after
    ``cfg.test_start``, split into consecutive chunks of ``cfg.retrain_step``
    dates. For each chunk the model is refit on all rows whose date falls
    strictly before ``chunk_start`` minus the effective embargo (in trading
    dates), then predicts every row in the chunk (all tickers pooled).

    The effective embargo is ``max(cfg.embargo_days, cfg.horizon)``: the label
    at a training date ``t`` is built from ``close[t+horizon]``, so an embargo
    shorter than ``horizon`` would let training labels read closes from inside
    the test chunk (temporal leakage). When the effective embargo exceeds
    ``cfg.embargo_days`` a :class:`UserWarning` is emitted.

    Returns a :class:`WalkForwardResult` with test-period predictions, gain
    feature importance averaged over refits (normalized to sum to 1), and the
    number of refits performed.
    """
    if "date" not in (X.index.names or []):
        raise ValueError("X must have a MultiIndex with a 'date' level")
    if not X.index.equals(y.index):
        y = y.reindex(X.index)
        if y.isna().any():
            raise ValueError("y could not be aligned to X.index without NaNs")

    effective_embargo = max(cfg.embargo_days, cfg.horizon)
    if effective_embargo > cfg.embargo_days:
        warnings.warn(
            f"embargo_days={cfg.embargo_days} is shorter than horizon={cfg.horizon}: "
            f"the label at a train date t uses close[t+horizon], which would fall "
            f"inside the test chunk. Growing the embargo to {effective_embargo} "
            f"trading dates to prevent temporal leakage.",
            UserWarning,
            stacklevel=2,
        )

    date_level = X.index.get_level_values("date")
    unique_dates = pd.DatetimeIndex(date_level.unique()).sort_values()
    test_start = pd.Timestamp(cfg.test_start)
    first_test_pos = int(np.searchsorted(unique_dates, test_start, side="left"))
    test_dates = unique_dates[first_test_pos:]

    if len(test_dates) == 0:
        raise ValueError(f"no trading dates on or after test_start={cfg.test_start}")
    if first_test_pos - effective_embargo <= 0:
        raise ValueError(
            f"insufficient training history before test_start={cfg.test_start} "
            f"(need > {effective_embargo} prior trading dates: "
            f"max(embargo_days={cfg.embargo_days}, horizon={cfg.horizon}))"
        )

    y_values = y.to_numpy(dtype=float)
    pred_chunks: list[pd.Series] = []
    importances: list[np.ndarray] = []

    for chunk_offset in range(0, len(test_dates), cfg.retrain_step):
        chunk = test_dates[chunk_offset : chunk_offset + cfg.retrain_step]
        chunk_start_pos = first_test_pos + chunk_offset
        train_dates = unique_dates[: chunk_start_pos - effective_embargo]

        train_mask = date_level.isin(train_dates)
        test_mask = date_level.isin(chunk)

        model = _make_model(cfg)
        model.fit(X.loc[train_mask], y_values[np.asarray(train_mask)])

        X_test = X.loc[test_mask]
        pred_chunks.append(
            pd.Series(model.predict(X_test), index=X_test.index, name="prediction")
        )
        importances.append(
            np.asarray(
                model.booster_.feature_importance(importance_type="gain"), dtype=float
            )
        )

    predictions = pd.concat(pred_chunks).sort_index()

    mean_gain = np.mean(np.vstack(importances), axis=0)
    total = mean_gain.sum()
    if total > 0:
        mean_gain = mean_gain / total
    feature_importance = pd.DataFrame({"gain": mean_gain}, index=list(X.columns))
    feature_importance.index.name = "feature"
    feature_importance = feature_importance.sort_values("gain", ascending=False)

    return WalkForwardResult(
        predictions=predictions,
        feature_importance=feature_importance,
        n_refits=len(importances),
    )


def predictions_to_signals(predictions: pd.Series, threshold: float = 0.0,
                           long_only: bool = False) -> dict[str, pd.Series]:
    """Map pooled predicted returns to per-ticker signal Series.

    Per ticker: ``+1`` if pred > threshold, ``-1`` if pred < -threshold,
    else ``0``; with ``long_only`` shorts become flat. ``threshold`` is in
    return space (e.g. 0.0005 is roughly 5 bps). Returned Series are indexed
    by date (the ticker level is dropped) per the shared signal semantics.
    """
    signals: dict[str, pd.Series] = {}
    for ticker, grp in predictions.groupby(level="ticker"):
        preds = grp.droplevel("ticker").sort_index()
        sig = pd.Series(0.0, index=preds.index, name=str(ticker))
        sig[preds > threshold] = 1.0
        sig[preds < -threshold] = -1.0
        if long_only:
            sig[sig < 0.0] = 0.0
        signals[str(ticker)] = sig
    return signals
