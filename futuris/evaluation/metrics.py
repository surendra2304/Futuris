"""Pure statistical evaluation metrics for continuous, probabilistic, and interval forecasts."""

import numpy as np


def mae(actual: np.ndarray | list[float], predicted: np.ndarray | list[float]) -> float:
    """Mean Absolute Error."""
    y_true, y_pred = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    if len(y_true) == 0:
        return 0.0
    return float(np.mean(np.abs(y_true - y_pred)))


def rmse(actual: np.ndarray | list[float], predicted: np.ndarray | list[float]) -> float:
    """Root Mean Squared Error."""
    y_true, y_pred = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    if len(y_true) == 0:
        return 0.0
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def mape(
    actual: np.ndarray | list[float],
    predicted: np.ndarray | list[float],
    eps: float = 1e-5,
) -> float:
    """Mean Absolute Percentage Error with zero-division safeguard (returns percentage)."""
    y_true, y_pred = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    if len(y_true) == 0:
        return 0.0
    denominator = np.where(np.abs(y_true) < eps, eps, np.abs(y_true))
    return float(np.mean(np.abs((y_true - y_pred) / denominator)) * 100.0)


def brier_score(
    actual_events: np.ndarray | list[bool],
    probabilities: np.ndarray | list[float],
) -> float:
    """Brier score for binary probabilistic event forecasts in [0, 1]."""
    y_true = np.asarray(actual_events, dtype=float)
    y_prob = np.asarray(probabilities, dtype=float)
    if len(y_true) == 0:
        return 0.0
    return float(np.mean((y_prob - y_true) ** 2))


def log_loss(
    actual_events: np.ndarray | list[bool],
    probabilities: np.ndarray | list[float],
    eps: float = 1e-15,
) -> float:
    """Binary cross-entropy / log-loss with epsilon boundary clamping."""
    y_true = np.asarray(actual_events, dtype=float)
    y_prob = np.clip(np.asarray(probabilities, dtype=float), eps, 1.0 - eps)
    if len(y_true) == 0:
        return 0.0
    return float(-np.mean(y_true * np.log(y_prob) + (1.0 - y_true) * np.log(1.0 - y_prob)))


def calibration_error(
    actual_events: np.ndarray | list[bool],
    probabilities: np.ndarray | list[float],
    num_bins: int = 10,
) -> float:
    """Expected Calibration Error (ECE) over binned reliability."""
    y_true = np.asarray(actual_events, dtype=float)
    y_prob = np.asarray(probabilities, dtype=float)
    n = len(y_true)
    if n == 0:
        return 0.0

    bins = np.linspace(0.0, 1.0, num_bins + 1)
    ece = 0.0

    for i in range(num_bins):
        bin_lower, bin_upper = bins[i], bins[i + 1]
        if i < num_bins - 1:
            mask = (y_prob >= bin_lower) & (y_prob < bin_upper)
        else:
            mask = (y_prob >= bin_lower) & (y_prob <= bin_upper)

        bin_count = np.sum(mask)
        if bin_count > 0:
            bin_conf = np.mean(y_prob[mask])
            bin_acc = np.mean(y_true[mask])
            ece += (bin_count / n) * abs(bin_acc - bin_conf)

    return float(ece)


def interval_coverage(
    actual: np.ndarray | list[float],
    lower_bounds: np.ndarray | list[float],
    upper_bounds: np.ndarray | list[float],
) -> float:
    """Fraction of observed outcomes bounded inside [lower, upper]."""
    y_true = np.asarray(actual, dtype=float)
    y_low = np.asarray(lower_bounds, dtype=float)
    y_high = np.asarray(upper_bounds, dtype=float)
    if len(y_true) == 0:
        return 1.0
    inside = (y_true >= y_low) & (y_true <= y_high)
    return float(np.mean(inside))


def interval_width(
    lower_bounds: np.ndarray | list[float],
    upper_bounds: np.ndarray | list[float],
) -> float:
    """Mean sharpness / width of prediction intervals (upper - lower)."""
    y_low = np.asarray(lower_bounds, dtype=float)
    y_high = np.asarray(upper_bounds, dtype=float)
    if len(y_low) == 0:
        return 0.0
    return float(np.mean(np.maximum(0.0, y_high - y_low)))


def ranking_precision_recall(
    actual_events: np.ndarray | list[bool],
    probabilities: np.ndarray | list[float],
    cutoff: float = 0.50,
) -> tuple[float, float]:
    """Precision and Recall for alert thresholding at a given probability cutoff."""
    y_true = np.asarray(actual_events, dtype=bool)
    y_prob = np.asarray(probabilities, dtype=float)
    predicted_positive = y_prob >= cutoff

    tp = np.sum(predicted_positive & y_true)
    fp = np.sum(predicted_positive & ~y_true)
    fn = np.sum(~predicted_positive & y_true)

    precision = float(tp / (tp + fp)) if (tp + fp) > 0 else 1.0
    recall = float(tp / (tp + fn)) if (tp + fn) > 0 else 1.0
    return precision, recall
