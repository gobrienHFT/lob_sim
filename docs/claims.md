# Claim / non-claim matrix

`lob_sim` is a deterministic research laboratory, not a live trading stack. The
public evidence should be read using this matrix:

| Area | Defensible claim | Explicit non-claim |
| --- | --- | --- |
| Capture | Receipt-sequenced, checksummed schema-v3 envelopes record route connect/failure boundaries and can be validated and replayed with visible partial tails. Consumer/writer failures stop capture instead of silently reconnecting. | Zero packet loss without venue-side telemetry; exchange timestamp causality; bounded asynchronous writer/soak proof until its release evidence exists. |
| Book reconstruction | Binance USD-M market-by-price epochs are reconstructed only while sequence and invariants are valid. | Participant identity, hidden liquidity, or private order-level FIFO. |
| Stream validity | Depth and trade routes have independent validity. Trade reconnects preserve a valid book but clear stale flow and terminate trade-dependent live/pending state before prospective recovery. | Recovering missed public trades or proving execution during an invalid interval. |
| Clocks | Schema-v3 logical time is `(recv_monotonic_ns, recv_seq)` and regression is fail-closed. | Measured exchange, network, or colocated latency. |
| Execution | Public-L2 fills are an explicit scenario envelope with evidence and validity state. | True Binance fills, counterfactual market impact, or true bounds. |
| Synthetic venue | The synthetic mode has exact participant/order IDs and price-time priority. | Historical Binance FIFO equivalence. |
| Differential proof | Python and Rust agree on generated fixed-point book batches, exact-synthetic new/cancel/replace lifecycles, integer-nanosecond scheduler transitions and per-symbol live-plus-pending lot reservations, including periodic state hashes. | End-to-end engine parity, public-L2 fill parity, portfolio-notional risk parity, accounting/markout parity or manifest parity. |
| Risk/accounting | Live-plus-pending exposure, post-only arrival checks, fees, gross/net PnL and missing marks are auditable. | Capital deployment, live profitability, or risk-model completeness. |
| Research | Paired scenario/latency studies can report markouts, uncertainty and robustness. | Alpha after an underpowered or holdout-tuned study. |
| Performance | Benchmarks document a pinned build, corpus and host. | Trading latency, Jane Street equivalence, or production readiness. |

Any report lacking a complete validity interval or a self-describing manifest is
diagnostic only.
