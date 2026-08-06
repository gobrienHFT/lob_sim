# Benchmark evidence

`baseline_windows_python313.json` is the checked milestone baseline for `data/raw_1772140125.ndjson.gz`.

Protocol:

- one warm-up;
- five fresh-engine timing runs with `tracemalloc` disabled;
- five separate fresh-engine peak Python-allocation runs with `tracemalloc` enabled;
- end-to-end scope from engine construction through complete fixture replay;
- fixture, public configuration, source tree, interpreter, OS, and CPU fingerprinted.

Headline values are medians, not best runs:

- duration: `0.1842558 s`;
- throughput: `1910.387624 records/s`;
- peak traced Python allocation: `1,109,418 bytes`.

There is no external comparator. The fixture contains only 352 heterogeneous records, so this is a regression baseline rather than a capacity or latency claim. Use the report's guarded `reproduction_command`; it checks fingerprints before replay.
