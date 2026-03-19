# Futures Validation

## Scope

Validation in this repo is about invariants, deterministic behavior, and assumption visibility. It is not a claim of exchange-private fill validation.

## Invariants and What Is Tested

### Snapshot coverage rules

- The first accepted depth diff must cover the snapshot update id.
- Stale diffs older than the snapshot are ignored.
- This behavior is exercised in the book-sync tests.

### Diff continuity rules

- Later depth diffs are checked against the previous accepted `u` through `pu`.
- A continuity break raises or records a gap instead of silently advancing the book.
- Gap handling is covered by the gap-resync tests.

### FIFO / price-time assumptions

- Resting queue is modeled explicitly at each price level.
- Queue consumption happens from the front of the level.
- Later venue additions stay behind earlier resting orders at the same price.

### Partial fill handling

- A resting order can fill in multiple chunks as queue is consumed.
- Remaining quantity stays active until fully filled or canceled.

### Queue-ahead behavior

- Strategy orders only fill after visible queue ahead has been consumed.
- Queue-ahead deterioration is visible to the strategy layer and can trigger repost logic.

### Deterministic replay expectation

- The same input file and config should produce the same replay and simulation outputs.
- Tests cover deterministic behavior on a fixed synthetic event stream.

### Markout / inventory / PnL sanity checks

- Inventory updates are consistent with signed fills.
- Unrealized PnL is marked from the current reconstructed mid.
- Markout windows drain deterministically from the stored fill history.

## Current Test Coverage

- [`tests/test_book_sync.py`](../tests/test_book_sync.py)
- [`tests/test_gap_resync.py`](../tests/test_gap_resync.py)
- [`tests/test_fill_model.py`](../tests/test_fill_model.py)
- [`tests/test_futures_invariants.py`](../tests/test_futures_invariants.py)

## Limitations

- Validation is against documented assumptions, not private venue truth.
- Public data cannot prove exact passive-fill attribution.
- The baseline strategy is intentionally simple, so validation is focused on replay and matching correctness rather than alpha quality.

## Non-Goals

- No claim of production exchange matching equivalence.
- No claim of venue-private fill reconstruction.
- No benchmark numbers are treated as universal without hardware and dataset context.
