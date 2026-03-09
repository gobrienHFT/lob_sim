# Options scenario matrix

Same-seed comparison across all current options presets. Each run uses seed `7` and `180` steps so the scenario parameters, not a hand-picked path, drive the differences.

| Scenario | Ending PnL | Realized PnL | Gross spread | Signed markout | Toxic fill rate | Adverse fill rate | Hedge trades | Max inventory | Final net delta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| calm_market | 800.61 | 1143.77 | 1214.36 | 519.31 | 6.9% | 44.0% | 45 | 106 | 35.71 |
| volatile_market | 13066.87 | 13972.34 | 14065.57 | 11644.42 | 26.9% | 42.1% | 72 | 143 | -35.76 |
| toxic_flow | 4068.17 | 7393.54 | 7481.06 | -5339.31 | 55.9% | 48.0% | 76 | 157 | 8.94 |
| inventory_stress | 6587.10 | 6666.82 | 6817.91 | 3725.75 | 16.0% | 45.5% | 81 | 206 | 17.40 |

## Scenario notes

### calm_market

- Lower-volatility quoting with modest toxic flow and lighter hedge pressure.
- Calm underlying path with smaller jumps and tighter option quote width.
- Lower customer arrival intensity and mostly non-toxic flow.
- Lower hedge pressure because delta excursions are less violent.
- In this run it finished with ending PnL `800.61`, signed markout `519.31`, toxic fill rate `6.9%`, hedge trades `45`, and max inventory `106` contracts.

### volatile_market

- Higher realized volatility and jump risk, forcing wider quotes and faster hedging.
- Large spot swings and more jump risk push fair values around quickly.
- Healthy flow, but the fast underlying path makes inventory harder to warehouse.
- Higher hedge pressure because delta moves faster and wider.
- In this run it finished with ending PnL `13066.87`, signed markout `11644.42`, toxic fill rate `26.9%`, hedge trades `72`, and max inventory `143` contracts.

### toxic_flow

- Flow is more informed, so markouts matter and toxic fills are easier to discuss.
- Moderate volatility, but adverse-selection drift is deliberately stronger.
- A larger share of customer trades are informed against the quoted price.
- Moderate hedge pressure, but post-trade markouts are the real story.
- In this run it finished with ending PnL `4068.17`, signed markout `-5339.31`, toxic fill rate `55.9%`, hedge trades `76`, and max inventory `157` contracts.

### inventory_stress

- Larger clips and looser hedge thresholds force more inventory warehousing.
- Normal volatility, but inventory accumulates quickly because clips are larger.
- Faster flow and bigger trade sizes push single-contract positions harder.
- Delta is allowed to run further before hedging, so inventory skew dominates.
- In this run it finished with ending PnL `6587.10`, signed markout `3725.75`, toxic fill rate `16.0%`, hedge trades `81`, and max inventory `206` contracts.

## What this proves

- The options case study is regime-sensitive: the same seed produces meaningfully different PnL, markout, hedging, and inventory outcomes when the scenario parameters change.
- Toxic flow, realized volatility, and hedge thresholds move the outputs in visible ways, which makes the quoting and risk logic falsifiable rather than decorative.
- The demo is not relying on one flattering path; it shows how the same dealer logic behaves across calmer, faster, more toxic, and more inventory-heavy conditions.

## What this does not prove

- It does not prove exchange realism for options, because the options side is still a synthetic dealer simulation rather than a venue order-book replay.
- It does not prove statistical robustness across many seeds, long samples, or calibrated market regimes; this is a compact case-study comparison, not a full backtest study.
- It does not prove production readiness. The value here is transparent market-making logic and interpretable outputs, not infrastructure scale.

Plot: [scenario_comparison.png](scenario_comparison.png)
