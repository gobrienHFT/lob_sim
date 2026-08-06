# Bundled capture provenance

These files are historical regression fixtures. They predate capture schema v2 and are not valid sources for claims about profitability, venue latency, or subsecond adverse selection.

| Fixture | Records | SHA-256 | Intended use |
| --- | ---: | --- | --- |
| `raw_1772137284.ndjson.gz` | 1,297 | `b8b1dbf7885e13c30bf014e3ac80f8987068856400ab32f8d96858f715d513c3` | clock-unit/regression and resync regression |
| `raw_1772138833.ndjson.gz` | 816 | `3cd7b6211c3ba251e80c28a60cf92e217a5c81c46507524c272d8d5e8251a7d9` | accounting/fill sensitivity regression |
| `raw_1772140125.ndjson.gz` | 352 | `9acc7378147e7e36d4a327e7c4ad7948bf52e90bcb8234d993c0f65042299c88` | fast replay, checksum, and benchmark fixture |

Known limitations:

- snapshot-first collection creates an initial sequence gap for each symbol before a later snapshot recovers the book;
- files have no `captureMeta`, receive sequence, route, stream epoch, or sync epoch;
- top-level time is inconsistent in the oldest fixture; replay falls back to exchange `E`, normalizes obvious milliseconds, and reports clamps;
- independent venue-side completeness was not measured.

On the smallest fixture, current replay ends with:

- BTCUSDT update ID `9994093466130`, checksum `70de39c123669ad3d32c5e815bb79f57730800db195f810c811270cc2633f558`;
- ETHUSDT update ID `9994093472556`, checksum `075386926a1a7a9d2cbc29381b117d6c8f33e198ee6f55e1d3788901c0deb0d8`.

Those checksums identify final reconstructed state under the current exact tick/lot parser. They do not prove that Binance emitted no packets missing from the recording.
