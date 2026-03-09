# Toxicity versus spread sensitivity

Deterministic sweep on the `toxic_flow` preset. Seed and step count stay fixed so the only moving parts are toxic-flow share and baseline quote width.

- Seed: `7`
- Steps: `180`
- Toxic flow probabilities: `0.20, 0.40, 0.55, 0.70`
- Base half-spreads: `0.06, 0.09, 0.12, 0.15`

| Toxic flow prob | Base half-spread | Ending PnL | Gross spread | Signed markout | Toxic fill rate | Adverse fill rate | Hedge trades | Max inventory |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.20 | 0.06 | -298.31 | 3212.97 | -3130.47 | 21.1% | 46.7% | 70 | 162 |
| 0.20 | 0.09 | 718.69 | 4229.97 | -2113.47 | 21.1% | 45.4% | 70 | 162 |
| 0.20 | 0.12 | 1735.69 | 5246.97 | -1096.47 | 21.1% | 44.7% | 70 | 162 |
| 0.20 | 0.15 | 2751.90 | 6263.19 | -80.25 | 21.1% | 44.7% | 70 | 162 |
| 0.40 | 0.06 | 5191.38 | 7392.13 | -1472.78 | 40.1% | 46.1% | 65 | 156 |
| 0.40 | 0.09 | 6163.38 | 8364.13 | -500.78 | 40.1% | 45.4% | 65 | 156 |
| 0.40 | 0.12 | 7135.38 | 9336.13 | 471.22 | 40.1% | 45.4% | 65 | 156 |
| 0.40 | 0.15 | 8107.38 | 10308.13 | 1443.22 | 40.1% | 45.4% | 65 | 156 |
| 0.55 | 0.06 | 3090.17 | 6503.06 | -6317.31 | 55.9% | 48.7% | 76 | 157 |
| 0.55 | 0.09 | 4068.17 | 7481.06 | -5339.31 | 55.9% | 48.0% | 76 | 157 |
| 0.55 | 0.12 | 5046.17 | 8459.06 | -4361.31 | 55.9% | 46.7% | 76 | 157 |
| 0.55 | 0.15 | 6024.17 | 9437.06 | -3383.31 | 55.9% | 46.7% | 76 | 157 |
| 0.70 | 0.06 | 5767.28 | 9944.37 | -5373.42 | 70.4% | 52.0% | 65 | 155 |
| 0.70 | 0.09 | 6706.28 | 10883.37 | -4434.42 | 70.4% | 51.3% | 65 | 155 |
| 0.70 | 0.12 | 7640.82 | 11817.92 | -3499.88 | 70.4% | 50.7% | 65 | 155 |
| 0.70 | 0.15 | 8567.82 | 12744.92 | -2572.88 | 70.4% | 50.0% | 65 | 155 |

## what this shows
- Wider baseline spreads improve gross spread capture, but they do not erase adverse-selection losses when the toxic share rises.
- As toxic flow probability increases, signed markout and adverse-fill rate generally deteriorate even with the same random seed.

## what this does not show
- This is still a synthetic dealer study, so it does not calibrate spread setting from real venue queue position or real customer flow.
- One fixed seed and one short horizon show local trade-offs, not a fully robust parameter search or production spread optimizer.

Plot: [toxicity_spread_heatmap.png](toxicity_spread_heatmap.png)
