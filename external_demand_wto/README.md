# WTO native-volume v15 replication

This directory runs the frozen national quarterly export equation for the China external-demand paper.

## What the workflow does

1. Downloads China's seasonally adjusted quarterly merchandise export-volume index and 20 partners' seasonally adjusted quarterly merchandise import-volume indices from the WTO DBnomics mirror.
2. Keeps all missing partner values as missing and calculates quarter-specific selected-market weight coverage.
3. Uses the audited 2005–2007 fixed destination weights, excluding Hong Kong and Macao.
4. Reconstructs three-year rolling lagged destination weights from imports from China and current-dollar GDP, with numerical checks against the audited workbook benchmarks.
5. Downloads the BIS broad real effective exchange rate for China and converts it to quarterly growth.
6. Estimates the pre-specified v15 models with HAC(4) inference.
7. Exports data, coverage diagnostics, Table 2 inputs, a figure, model summaries, download hashes, and a run status file as a GitHub Actions artifact.

## Interpretation boundary

The estimated coefficients are conditional associations. They are not labelled as causal elasticities. The workflow never replaces missing partner observations with zero and stops if the reconstructed weights fail the benchmark checks.
