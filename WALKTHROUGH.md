# Follow one run

`lob_sim` asks: given a recorded public book, when would a passive order fill
under a chosen queue and latency model? The book comes from messages. The
hypothetical order and its queue position belong to the simulation.

## Run the offline example

Follow the setup in [README](README.md), then run:

```bash
python -m lob_sim.cli --env .env.example demo
```

The default input is a six-record fixture. Read these JSON sections first:

| Section | What to inspect |
| --- | --- |
| `input` | Record counts, symbol metadata, and input hash. This legacy fixture has no schema-v3 receipt-clock coverage. |
| `deterministic_run` | State and audit hashes, modeled fills, and valuation completeness. Repeatable fixture PnL checks mechanics, not strategy performance. |
| `synthetic_exchange` | `fifo_ground_truth.matches` is true: `ask-a` fills before `ask-b`. The crossing post-only order is rejected. |

The historical feed gives quantities at prices but cannot locate our
hypothetical order among private participants. The synthetic exchange owns
every order and can check exact price-time priority.

## Why queue and cancel timing matter

Suppose ten lots are displayed at our bid when a one-lot buy joins. In a
back-of-visible-queue scenario, those ten lots sit ahead of us. A public sale of
four lots reduces that assumed queue to six; it does not fill our order. A depth
decrease could include cancellations, so the selected execution scenario
determines how much it contributes to queue depletion.

Now send a cancel. The order remains fillable until the modeled acknowledgement
arrives. That race changes inventory and fees. If feed continuity breaks,
affected execution state must be invalidated before another fill can be
inferred. Inspect these mechanics before comparing PnL.

## Follow the outputs

Start with the [committed walkthrough pack](docs/sample_outputs/futures_replay_walkthrough/README.md).
It can be read directly on GitHub:

1. Read the [input](docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson)
   and [walkthrough notes](docs/sample_outputs/futures_replay_walkthrough/walkthrough.md).
2. Find the modeled fill in [trades.csv](docs/sample_outputs/futures_replay_walkthrough/trades.csv).
3. Follow its market observations, order actions, and queue consumption in
   [event_trace.csv](docs/sample_outputs/futures_replay_walkthrough/event_trace.csv).
4. Check counts, inventory, fees, and markouts in
   [summary.json](docs/sample_outputs/futures_replay_walkthrough/summary.json).

For capture validity, open the [schema-v3 examples](docs/sample_outputs/futures_schema_v3_case/README.md),
which include clean and adversarial inputs. The
[fault-injection checks](docs/fault_injection.md) cover gaps and damaged captures.

Next, inspect the [recorded clip](docs/sample_outputs/futures_recorded_clip_case/README.md)
and its [case notes](docs/sample_outputs/futures_recorded_clip_case/case_notes.md).
Read the [strategy definitions](docs/futures_strategy_profiles.md) before the
[profile comparison](docs/strategy_results/futures_strategy_profile_reference.md).
The [parameter sweep](docs/strategy_results/futures_parameter_sweep_reference.md)
and [benchmark reference](docs/benchmark_results/futures_replay_reference.md)
document their inputs and measurement scope.

Compare [overlap reconciliation](docs/futures_overlap_sensitivity.md) and the
[latency sweep](docs/strategy_results/futures_latency_sweep_reference.md) to see
how execution assumptions affect the same recorded input.

## Find the code

| Question | Start here |
| --- | --- |
| Is the snapshot usable, and do updates connect? | [Book synchronizer](lob_sim/book/sync.py) |
| How does displayed consumption affect a hypothetical queue? | [Passive fill model](lob_sim/sim/fill_model.py) |
| When do orders arrive and cancels take effect? | [Simulation engine](lob_sim/sim/engine.py) |
| How are inventory, fees, and later marks accounted for? | [Metrics](lob_sim/sim/metrics.py) |
| How does exact synthetic matching work? | [Synthetic exchange](lob_sim/sim/synthetic_exchange.py) |
| What is compared between Python and Rust? | [Differential results](docs/differential_results/README.md) |

The [interview notes](docs/interview_packet.md) give a spoken explanation and
technical questions. The [results memo](docs/reviewer_results_memo.md) explains
the committed measurements.

## Check the run

The [determinism checker](scripts/check_futures_determinism.py) reruns the fixture
and compares state and trace hashes. The [pack auditor](scripts/audit_futures_pack.py)
checks agreement between the recorded input, fills, accounting, and manifests:

```bash
python scripts/check_futures_determinism.py --file docs/sample_outputs/futures_replay_walkthrough/input_fixture.ndjson --env .env.example
python scripts/audit_futures_pack.py --committed-futures
```

## What still needs proving

Python/Rust checks cover selected primitives and a composed contract, rather
than the complete simulation engine. Small fixtures test mechanics; they do
not establish performance on sustained market traffic or a held-out trading
result. Read the [assumptions and limits](docs/claims.md) before using a result
outside those conditions.

The separate [options case study](docs/options_case_study_notes.md) explores
dealer pricing and hedging with controlled inputs. It is optional reading after
the futures replay.
