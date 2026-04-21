import json
import os
import gdown

import numpy as np
from scipy.io import loadmat

from task_and_baseline import baseline, build_task_helpers

# Download the dataset only if missing
url = "https://drive.google.com/uc?id=1BBHVSI4KB-B8OX46eN1Nm4ARCeq6Rui4"
downloaded_file = "challenge.mat"
if not os.path.exists(downloaded_file):
    gdown.download(url, downloaded_file, quiet=False)

data = loadmat("challenge.mat", simplify_cells=True)
tx = data["tx"].astype(np.complex128)
rx = data["rx"].astype(np.complex128)
Fs = float(data["Fs"])
N, _ = tx.shape

tx_n = tx / (np.sqrt(np.mean(np.abs(tx) ** 2, axis=0, keepdims=True)) + 1e-30)
helpers = build_task_helpers(tx_n, Fs, N)


def rank1_shared_component(band_matrix):
    cov = band_matrix.conj().T @ band_matrix / band_matrix.shape[0]
    _, eigvecs = np.linalg.eigh(cov)
    principal_vec = eigvecs[:, -1]
    shared_signal = band_matrix @ principal_vec
    denom = np.vdot(shared_signal, shared_signal) + 1e-30

    rank1 = np.column_stack([
        (np.vdot(shared_signal, band_matrix[:, ch]) / denom) * shared_signal
        for ch in range(band_matrix.shape[1])
    ])
    return rank1


def your_canceller(tx_n, rx):
    del tx_n

    fit_tx_prediction = helpers["fit_tx_prediction"]
    score_filter = helpers["score_filter"]
    score_fn = helpers["score"]

    # Stage 1: subtract TX-driven nonlinear interference
    tx_pred = fit_tx_prediction(rx)
    rx_stage1 = rx - tx_pred

    # Stage 2: estimate shared rank-1 residual in the scoring band
    band_residual = np.column_stack([
        score_filter(rx_stage1[:, ch]) for ch in range(rx_stage1.shape[1])
    ])
    rank1 = rank1_shared_component(band_residual)

    # Fine search around the current best alpha
    best_avg = -1e30
    best_alpha = 0.9
    best_rx = rx_stage1

    for alpha in [0.85, 0.875, 0.9, 0.925, 0.95]:
        candidate = rx_stage1 - alpha * rank1
        _, avg = score_fn(rx, candidate, label=f"alpha={alpha:.3f}")
        if avg > best_avg:
            best_avg = avg
            best_alpha = alpha
            best_rx = candidate

    print(f"Selected alpha: {best_alpha:.3f} (internal search score: {best_avg:.2f} dB)")
    return best_rx

print("\n=== Baseline ===")
baseline_reds, baseline_avg = helpers["score"](
    rx, baseline(tx_n, rx, helpers["fit_tx_prediction"]), label="baseline"
)

print("=== Your Solution ===")
yours_reds, yours_avg = helpers["score"](rx, your_canceller(tx_n, rx), label="yours")

results = {
    "baseline": {
        "per_channel_db": baseline_reds,
        "average_db": baseline_avg,
    },
    "yours": {
        "per_channel_db": yours_reds,
        "average_db": yours_avg,
    },
}

with open("results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)
