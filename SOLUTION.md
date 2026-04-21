# Solution

## Reproducibility
A minimal setup is:

python -m venv .venv
source .venv/bin/activate
pip install numpy scipy gdown

To run my solution, use:

python applicant_solution.py

This will generate `results.json` in the current directory.

## Final solution description
I started from the provided baseline and tried to keep my final method simple.

My solution has two main steps:

1. First, I use the provided helper `fit_tx_prediction` to remove the TX-driven nonlinear interference.
2. Then, I apply a shared rank-1 residual cancellation step in the scoring band across the 4 RX channels.

The baseline already does a reasonable job on the TX-related nonlinear part, but it does not explicitly handle the shared residual component across the receive channels. Because of that, I added a second step to estimate and subtract this shared structure.

To make this second step safer, I tested several scaling factors for the rank-1 cancellation:

{0.850, 0.875, 0.900, 0.925, 0.950}

In my final run, the best result was obtained with:
- selected alpha: 0.95
- average score: 7.03 dB

The final `results.json` shows:
- baseline average: 4.0178 dB
- final average: 7.0286 dB

## What improved the metric most
The main improvement came from adding the shared rank-1 residual cancellation after the baseline TX-only cancellation.

In my experiments, the baseline removed one important part of the interference, but there was still a shared component left across all 4 RX channels. Removing this extra shared component gave the biggest improvement and raised the score from about 4.02 dB to about 7.03 dB.

## Failed or discarded ideas
I also tried adding a second TX refit after the rank-1 residual cancellation step.

However, this version repeatedly failed the validity check because the unexplained component became too large compared with the residual. For this reason, I did not keep it in the final solution.

I also tried both coarser and finer searches for the scaling factor alpha. In the end, the best stable result was achieved near 0.95, while larger changes to the pipeline did not lead to a better valid score.