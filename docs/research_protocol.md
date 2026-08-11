# Market-making research protocol

The research layer is secondary to simulator validation. Compare the fixed
distance, inventory-skewed reservation-price, and causal imbalance baselines on
identical tapes, seeds, execution scenarios, and latency draws. Split whole UTC
days chronologically (60% calibration / 20% validation / 20% untouched test),
register configurations before opening the test partition, and report every
attempted variant.

Report gross spread capture, adverse selection, maker/taker fees, funding,
inventory marking, net PnL, time-weighted inventory, quote age, turnover,
cancel/fill races, drawdown and 100ms/1s/5s/30s signed markouts with observation
lag and coverage. Use moving-block bootstrap intervals (30-minute blocks, with
5/60-minute sensitivities). Fewer than ten joint-valid UTC days is a diagnostic
study, not a holdout claim.
