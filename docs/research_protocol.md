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

The executable contracts live in `lob_sim.research.protocol`:

- `chronological_day_split(...)` sorts and deduplicates whole UTC days without
  shuffling, returns disjoint 60/20/20 partitions, and marks fewer than ten
  joint-valid days as diagnostic-only.
- `ResearchRegistry` content-addresses each strategy/configuration and rejects
  registrations after `freeze()`. Freeze the registry before opening the test
  partition and include its `registry_sha256` in the run manifest.
- `moving_block_bootstrap_mean(...)` and
  `paired_moving_block_bootstrap_mean_delta(...)` use overlapping blocks and a
  pinned SplitMix64 sampler. The interval is uncertainty for the supplied
  observations; it does not repair invalid feed intervals or create a claim of
  alpha.

Example:

```python
from lob_sim.research.protocol import (
    ResearchRegistry,
    chronological_day_split,
    paired_moving_block_bootstrap_mean_delta,
)

split = chronological_day_split(valid_utc_days)
registry = ResearchRegistry()
registry.register("baseline", {"profile": "baseline", "seed": 7})
registry.register("inventory", {"profile": "inventory_skew", "seed": 7})
registry_snapshot = registry.freeze()
interval = paired_moving_block_bootstrap_mean_delta(
    baseline_markouts,
    inventory_markouts,
    block_size=30,  # choose the observation count representing 30 minutes
    seed=7,
)
```
