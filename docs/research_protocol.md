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
- `ResearchRegistry` content-addresses each strategy/configuration using strict
  finite JSON metadata and rejects registrations after `freeze()`. Freeze the
  registry before opening the test partition and include its `registry_sha256`
  in the study manifest or report alongside the simulator run manifest.
- `moving_block_bootstrap_mean(...)` and
  `paired_moving_block_bootstrap_mean_delta(...)` use overlapping blocks and a
  pinned SplitMix64 sampler. The interval is uncertainty for the supplied
  observations; it does not repair invalid feed intervals or create a claim of
  alpha.
- `paired_moving_block_bootstrap_ratio_delta(...)` resamples matched component
  sums, not individual fills or minute-level ratios. Different quantities and
  activity rates retain their actual denominators. Zero-activity periods are
  retained; a zero denominator in any replicate makes the interval unavailable
  instead of discarding that replicate or retrying it.
- `paired_clock_bootstrap(...)` and `paired_clock_ratio_bootstrap(...)` in
  `lob_sim.research.clock_bootstrap` enforce complete UTC periods and independent
  source/day/validity/contiguous strata. A 30-minute block means clock time,
  not an arbitrary count of fill observations. Short strata remain visible.

Example:

```python
from lob_sim.research.protocol import (
    ResearchRegistry,
    chronological_day_split,
)
from lob_sim.research.clock_bootstrap import paired_clock_ratio_bootstrap

split = chronological_day_split(valid_utc_days)
registry = ResearchRegistry()
registry.register("baseline", {"profile": "baseline", "seed": 7})
registry.register("inventory", {"profile": "inventory_skew", "seed": 7})
registry_snapshot = registry.freeze()
comparison = paired_clock_ratio_bootstrap(
    matched_minute_markout_components,  # PairedClockRatioPeriod objects
    block_minutes=30,
    seed=7,
)
```

The registered HMM study freezes its execution outcome contract alongside every
variant before extraction/test access. Study schema v2 adds quantity-weighted
signed markouts, observation-weighted adverse fractions and lag, pending-cancel
fill fractions, modeled queue evidence, marked spread capture, coverage, and
per-minute fill/fee/turnover activity. Every horizon uses its configured native
audit; absent 30-second observations are not invented.

Each successful fixed-clock HMM run publishes a content-hashed
`clock_outcomes.json` with rational numerator/denominator statistics and
resolved/invalidated/unresolved horizon counts. The reducer checks exact
single-symbol trade/execution agreement and rehashes both consumed streams,
including records outside complete minutes. It assigns later-resolved markouts
to their original integer-clock fill minute. Policy/baseline comparisons use
the same jointly eligible periods, retaining quiet minutes and excluding
invalid/stale/warming intervals. Fee/turnover activity is not marked PnL;
fill count per minute is not quote fill probability. Marked-net-PnL and
full-path drawdown intervals, representative data and held-out claims remain
separate unfinished requirements.
