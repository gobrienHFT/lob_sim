# Recorded Futures Clip Notes

- Symbol: `BTCUSDT`
- Input type: recorded clipped event stream
- Passive fill present: yes
- Regenerate: `python scripts/refresh_futures_recorded_case.py`

## Continuity

- Snapshot seed: `lastUpdateId=10038350842115`
- First accepted diff: `ts_local=1772633474.137`, `U=10038350840154`, `u=10038350844766`, `pu=10038350840119`
- Coverage check: the first accepted diff covers the snapshot because `10038350840154 <= 10038350842115 <= 10038350844766`
- Next diff: `ts_local=1772633474.239`, `pu=10038350844766`, which matches the prior `u=10038350844766`
- The remaining diffs in the committed clip continue with `pu == previous u`, so this case does not rely on a continuity gap or resnapshot inside the selected window

## Level Reduction Example

- At `ts_local=1772633474.239`, the ask level at `71600.10` increases from `1` lot to `17` lots
- At `ts_local=1772633474.341`, the next depth diff reduces that same ask level from `17` lots to `1` lot
- This is the recorded level reduction that coincides with the simulator's passive ask fill in this clip

## Passive-Fill Example

- `trades.csv` contains one passive ask fill at `ts_local=1772633474.341`
- Fill details: side `ask`, price `71600.10`, qty `0.001`, `queue_ahead_lots=0`, `time_in_book_ms=113.13986778259277`
- This is a top-of-queue fill, not a queue-ahead fill. The recorded clip demonstrates a real recorded level reduction and a passive fill under the public-data inference model
