# lob_sim

`lob_sim` is a Python research repo that combines exchange-aware Binance futures replay with a transparent options market-making case study focused on quoting, inventory, hedging, and PnL.

This repo demonstrates:
- futures L2 replay with queue-aware passive fill modelling
- an options dealer case study with fair value, reservation price, toxic flow, markout, and hedging
- readable research outputs: terminal summaries, markdown reports, CSV artifacts, and charts

## Start Here for Options Interviewers

If you only open one thing in this repo, make it the options case study in [lob_sim/options/demo.py](lob_sim/options/demo.py).

Quickest Windows launcher:

```bat
run_options_mm_case.bat
```

Quickest macOS/Linux launcher:

```bash
bash run_options_mm_case.sh
```

Quickest CLI command:

```bash
python -m lob_sim.cli options-demo --scenario calm_market --steps 360 --out-dir outputs --verbose --log-mode compact
```

Committed sample artifact pack:
- [docs/sample_outputs/README.md](docs/sample_outputs/README.md)
- [demo_report.md](docs/sample_outputs/toxic_flow_seed7/demo_report.md)
- [summary.json](docs/sample_outputs/toxic_flow_seed7/summary.json)
- [fills_head.csv](docs/sample_outputs/toxic_flow_seed7/fills_head.csv)
- [checkpoints_head.csv](docs/sample_outputs/toxic_flow_seed7/checkpoints_head.csv)
- [overview_dashboard.png](docs/sample_outputs/toxic_flow_seed7/overview_dashboard.png)
- [scenario_matrix.md](docs/sample_outputs/scenario_matrix_seed7/scenario_matrix.md)
- [scenario_matrix.csv](docs/sample_outputs/scenario_matrix_seed7/scenario_matrix.csv)

Sample charts:

[![Overview dashboard](docs/sample_outputs/toxic_flow_seed7/overview_dashboard.png)](docs/sample_outputs/toxic_flow_seed7/overview_dashboard.png)
[![PnL over time](docs/sample_outputs/toxic_flow_seed7/pnl_over_time.png)](docs/sample_outputs/toxic_flow_seed7/pnl_over_time.png)
[![Inventory over time](docs/sample_outputs/toxic_flow_seed7/inventory_over_time.png)](docs/sample_outputs/toxic_flow_seed7/inventory_over_time.png)
[![Net delta over time](docs/sample_outputs/toxic_flow_seed7/net_delta_over_time.png)](docs/sample_outputs/toxic_flow_seed7/net_delta_over_time.png)
[![Toxic vs non-toxic markout](docs/sample_outputs/toxic_flow_seed7/toxic_vs_nontoxic_markout.png)](docs/sample_outputs/toxic_flow_seed7/toxic_vs_nontoxic_markout.png)
[![Scenario comparison](docs/sample_outputs/scenario_matrix_seed7/scenario_comparison.png)](docs/sample_outputs/scenario_matrix_seed7/scenario_comparison.png)

Useful follow-up files when running locally:
- [docs/options_mm_demo_guide.md](docs/options_mm_demo_guide.md)
- `outputs/demo_report.md`
- `outputs/fills.csv`

## Synthetic vs Real

- The futures side replays recorded Binance USD-M futures data and models queue-aware passive fills.
- The options side is synthetic. It does not replay a live options venue order book.
- That is deliberate: the options module is meant to make fair value, quote skew, toxic flow, hedging, and PnL decomposition easy to inspect and explain.

## 60-Second Demo Path

1. Terminal summary
2. `overview_dashboard.png`
3. `demo_report.md`
4. `fills.csv`
5. `pnl_timeseries.csv`

If you are browsing on GitHub and not running the code, use the committed sample pack in [docs/sample_outputs/toxic_flow_seed7/](docs/sample_outputs/toxic_flow_seed7/).
For the same-seed preset comparison, open [docs/sample_outputs/scenario_matrix_seed7/](docs/sample_outputs/scenario_matrix_seed7/).

## Scenario Comparison

To show that the options demo is regime-sensitive rather than one flattering path, run:

```bash
python -m experiments.run_options_scenario_matrix --steps 180 --seed 7 --out-dir outputs
```

This writes:

- `outputs/scenario_matrix.csv`
- `outputs/scenario_matrix.md`
- `outputs/scenario_comparison.png`

## Options Market-Making Case Study

The options layer is implemented in:

- [lob_sim/options/black_scholes.py](lob_sim/options/black_scholes.py)
- [lob_sim/options/surface.py](lob_sim/options/surface.py)
- [lob_sim/options/markout.py](lob_sim/options/markout.py)
- [lob_sim/options/demo.py](lob_sim/options/demo.py)
- [experiments/run_options_case_study.py](experiments/run_options_case_study.py)
- [experiments/run_options_scenario_matrix.py](experiments/run_options_scenario_matrix.py)

It simulates:

- a small option chain across strikes and expiries
- Black-Scholes fair value and Greeks
- a simple skewed implied-vol surface
- scenario-driven customer arrivals, side, size, and toxicity
- inventory-aware reservation pricing from delta and vega exposure
- quote width widening from realized vol and gamma pressure
- delta hedging in the underlying
- realized and unrealized PnL decomposition
- scenario presets for `calm_market`, `volatile_market`, `toxic_flow`, and `inventory_stress`

### Simulation Loop

Each step:

1. Selects one option from the synthetic chain.
2. Builds a quote around fair value.
3. Samples customer flow side, size, and toxicity from the chosen scenario.
4. Applies the fill and updates inventory.
5. Hedges underlying delta if the risk trigger is breached.
6. Evolves underlying spot one step forward.
7. Marks the book and records fills, checkpoints, and path-level PnL.

### Quote Construction

The quote formula is:

`bid = fair_value - half_spread - reservation_price`

`ask = fair_value + half_spread - reservation_price`

Where:

- `fair_value` comes from Black-Scholes using current spot, time to expiry, and implied vol
- `reservation_price` shifts both sides when the dealer already carries too much delta or vega
- `half_spread` compensates for making a market and widens with realized vol and gamma pressure

### Markout Definition

Signed markout is measured against option fair value at a fixed future horizon from the realized simulation path:

`signed_markout = direction * (future_fair_value - fill_price) * qty * contract_size`

- `direction = +1` for a market-maker buy fill
- `direction = -1` for a market-maker sell fill
- positive signed markout is good for the dealer
- negative signed markout indicates adverse selection

### Output Artifacts

Each options run writes a clean pack into `outputs/`:

- `summary.json`
- `demo_report.md`
- `fills.csv`
- `checkpoints.csv`
- `pnl_timeseries.csv`
- `positions_final.csv`
- `overview_dashboard.png`
- `pnl_over_time.png`
- `realized_vs_unrealized.png`
- `spot_path.png`
- `inventory_over_time.png`
- `net_delta_over_time.png`
- `markout_distribution.png`
- `toxic_vs_nontoxic_markout.png`
- `top_traded_contracts.png`

A deterministic committed subset for `scenario=toxic_flow`, `steps=180`, `seed=7` lives in [docs/sample_outputs/toxic_flow_seed7/](docs/sample_outputs/toxic_flow_seed7/).

For a same-seed comparison across all current presets, run [experiments/run_options_scenario_matrix.py](experiments/run_options_scenario_matrix.py) and open `outputs/scenario_matrix.md` followed by `outputs/scenario_comparison.png`.

### How to Run the Options Demo

CLI:

```bash
python -m lob_sim.cli options-demo --scenario calm_market --steps 360 --out-dir outputs
python -m lob_sim.cli options-demo --scenario calm_market --steps 360 --out-dir outputs --verbose --log-mode compact
python -m experiments.run_options_case_study --scenario toxic_flow --steps 360 --out-dir outputs
```

Windows launchers:

```bat
run_options_mm_case.bat
run_options_mm_case.bat outputs 360 7 60 calm_market compact
run_options_mm_quick.bat
run_options_mm_quick.bat toxic_flow outputs 180 7
```

POSIX launcher:

```bash
bash run_options_mm_case.sh
bash run_options_mm_case.sh outputs 360 7 60 calm_market compact
```

The full launcher prints scenario assumptions, compact fill events, checkpoints, and the final summary block. The quick launcher runs a fast preset and prints only the headline metrics and artifact paths.

## Futures Replay Simulator

The futures side of the repo is a microstructure simulator for replaying recorded Binance futures depth and trade data and testing passive market-making logic.

### Data Capture

`python -m lob_sim.cli collect` records four message types into NDJSON:

- `exchangeInfo`
- `snapshot`
- `depthUpdate`
- `aggTrade`

This gives the simulator a deterministic event stream to replay later.

### Replay and Book Reconstruction

`python -m lob_sim.cli replay --file ...` rebuilds the venue book from those files:

- `exchangeInfo` defines tick size and lot size
- `snapshot` seeds the local book
- `depthUpdate` applies incremental depth changes
- `aggTrade` provides actual trade prints

[lob_sim/book/sync.py](lob_sim/book/sync.py) enforces diff continuity. If update IDs break, the replay detects a gap.

### Event-Driven Strategy Simulation

`python -m lob_sim.cli simulate --file ...` runs an event-driven strategy loop in [lob_sim/sim/engine.py](lob_sim/sim/engine.py).

The engine maintains one priority queue of internal events:

- `decision`
- `order_arrival`
- `order_cancel`
- `trade_execution`

For each replay timestamp, the engine:

1. Drains all internal events due before that timestamp.
2. Applies the market record to the reconstructed book.
3. Converts book reductions or trade prints into passive fills in the matching model.
4. Feeds those fills back through the same event queue as `trade_execution`.
5. Updates PnL, inventory, markouts, and kill-switch state.

This means the book evolves tick by tick, not in batch.

### Matching Engine

[lob_sim/sim/fill_model.py](lob_sim/sim/fill_model.py) stores price levels as FIFO queues:

- `dict[symbol][side][price_tick] -> deque[Order]`

That gives explicit exchange mechanics:

- price-time priority
- `limit`, `market`, and `cancel` order handling
- queue-ahead tracking
- partial fills
- best bid / ask lookup
- depth-level snapshots

Strategy orders and venue liquidity live in the same queue model, so queue position matters directly.

### Strategy Layer

[lob_sim/sim/mm_strategy.py](lob_sim/sim/mm_strategy.py) decides quotes by:

- reading the live best bid and ask from the local book
- computing midprice
- widening spread as short-horizon realized volatility rises
- skewing quotes when inventory builds
- canceling and reposting when queue-ahead size deteriorates

### Metrics and Outputs

[lob_sim/sim/metrics.py](lob_sim/sim/metrics.py) tracks:

- realized and unrealized PnL
- fill count and fill rate
- queue-ahead statistics
- inventory path
- adverse selection markouts
- regime performance buckets

Simulation outputs are written as JSON summary plus trade CSV.

### How to Run the Futures Simulator

```bash
python -m lob_sim.cli --env .env collect
python -m lob_sim.cli --env .env replay --file data/raw_....ndjson
python -m lob_sim.cli --env .env simulate --file data/raw_....ndjson
```

Windows batch runner:

```bat
run_futures_scenario.bat
run_futures_scenario.bat data\raw_....ndjson 5000
```

### Experiment Sweeps

```bash
python -m experiments.run_experiments --file data/raw_....ndjson --env .env
```

This writes CSV and PNG files to `experiments/output`.

## Architecture

```mermaid
flowchart LR
  A["Collector / Recorder"] --> B["Replay Reader"]
  B --> C["BookSynchronizer"]
  C --> D["LocalOrderBook"]
  D --> E["SimulationEngine"]
  E --> F["PassiveFillModel"]
  E --> G["MarketMakingStrategy"]
  F --> H["SimulationMetrics"]
  H --> I["Summary JSON / trade CSV"]
  E --> J["Options MM case study"]
  J --> K["Greeks / hedging / PnL report"]
```

## Limitations

- The futures queue model is explicit but still an approximation of venue-only participant behaviour.
- The options case study is synthetic rather than venue-calibrated; it is meant to show pricing, inventory, hedging, and risk logic clearly.
- The repo is a research and demo artifact rather than production exchange infrastructure.
