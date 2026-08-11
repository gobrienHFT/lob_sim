# Stress Case Notes

This pack is synthetic by design. It should be read as an executable invariant fixture, not as a claim about live exchange fill truth or alpha.

## Event Counts

- Event trace rows: `46`
- Event type counts: `{"cancel_ack": 2, "cancel_requested": 2, "decision": 4, "fill": 1, "market_record": 14, "markout": 1, "order_arrival": 4, "order_arrival_scheduled": 4, "order_rejected": 2, "queue_consumption": 12}`
- Replay event counts: `{"agg_trade": 4, "book_gap_count": 0, "depth_changes_applied": 12, "depth_update": 8, "exchange_info": 1, "records_processed": 14, "snapshot": 1}`

## Fill And Queue Evidence

- Fill-source mix: `{"agg_trade": 1, "depth_update": 0, "taker_order": 0}`
- Queue consumption: `{"overlap_window_seconds": 0.125, "sources": {"agg_trade": {"modeled_lots": 5, "observed_lots": 5, "overlap_netted_lots": 0, "queue_consumed_lots": 5, "unmatched_lots": 0}, "depth_update": {"modeled_lots": 14, "observed_lots": 14, "overlap_netted_lots": 0, "queue_consumed_lots": 0, "unmatched_lots": 14}}, "total_modeled_lots": 19, "total_observed_lots": 19, "total_overlap_netted_lots": 0, "total_queue_consumed_lots": 5, "total_unmatched_lots": 14}`
- Markout by source: `{"agg_trade": {"adverse_fill_rate_1s": 0.0, "adverse_samples": 0, "avg_markout_1s": 0.2, "qty": 0.001, "samples": 1}, "depth_update": {"adverse_fill_rate_1s": 0.0, "adverse_samples": 0, "avg_markout_1s": 0.0, "qty": 0.0, "samples": 0}, "taker_order": {"adverse_fill_rate_1s": 0.0, "adverse_samples": 0, "avg_markout_1s": 0.0, "qty": 0.0, "samples": 0}}`
- Arrival queue samples: `2`
- Max arrival queue ahead lots: `2`

## Limits

- The feed rows are exchange-shaped but synthetic.
- Public L2 data cannot prove private queue identity or exchange execution reports.
- The scripted strategy exists only to put rare mechanics in one compact pack.
