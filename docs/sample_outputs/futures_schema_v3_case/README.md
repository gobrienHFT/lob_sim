# Schema-v3 validity fixtures

These two tiny tapes are the committed reviewer fixtures for the capture
validity contract. They contain no live data and are intentionally small enough
to inspect in a code review.

## Clean claim-ready capture

`input_fixture.ndjson` contains a schema-v3 receive clock, global receipt
sequence, independent public/market connections, a snapshot bridge, one depth
update, one public trade, and a complete trailer. Run:

```bash
python -m lob_sim.cli audit --file docs/sample_outputs/futures_schema_v3_case/input_fixture.ndjson
```

Expected result: `ok=true`, `replay.validity.claim_ready=true`, two informational
`recovered` boundaries, and no invalidated boundaries.

## Adversarial fail-closed capture

`adversarial_fixture.ndjson` crosses a public reconnect boundary, carries a
rejected snapshot with a receive-clock regression, then records writer
overflow. The final book is deliberately not treated as execution-valid.

```bash
python -m lob_sim.cli audit --file docs/sample_outputs/futures_schema_v3_case/adversarial_fixture.ndjson
```

Expected result: `ok=false`, `claim_ready=false`, and explicit receipt-anchored
boundaries for the public stream, clock, rejected snapshot, and capture
overflow. A later trailer does not erase those reasons.

These fixtures demonstrate audit semantics only. They are not economic evidence,
venue-side packet-loss proof, private FIFO evidence, or a profitability claim.
