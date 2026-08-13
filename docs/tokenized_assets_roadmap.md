# Tokenized Assets Roadmap

This repo can support tokenized equities or other exchange-listed synthetic assets only after the product metadata and venue feed semantics are explicit. The roadmap below is a design path, not a claim that the current Binance USD-M adapter already handles stocks.

## What Carries Over

- Event-time replay.
- Snapshot plus diff continuity validation.
- Integer tick/lot normalization.
- Synthetic queue-ahead assumptions, with exact FIFO price-time priority reserved for the separate synthetic exchange.
- Signed markout, inventory, drawdown, and kill-switch metrics.
- Contract-multiplier-aware PnL, spread capture, fees, and markout once product metadata is normalized.
- Manifested input/config/source provenance.

## What Must Be Added

- Product metadata: issuer, underlying reference, trading venue, positive finite tick/lot sizes, quote currency, settlement currency, multiplier, corporate-action handling, and trading session rules.
- Venue adapter: a `ReplayFeedAdapter` implementation covering snapshot/diff schema, sequence semantics, trade-print semantics, and gap policy for the actual L2 source.
- Fee schedule: maker/taker fee currency, rebates, minimum fees, and product-specific tiers.
- Calendar/session model: halts, auctions, market holidays, overnight gaps, and session boundaries.
- Risk metadata: borrow/locate constraints if applicable, token redemption/issuer risk, and any venue-specific position limits.

## Adapter Checklist

1. Capture raw venue messages losslessly enough to replay.
2. Normalize to the replay contract or a venue-specific sibling behind `ReplayFeedAdapter` without discarding sequence IDs.
3. Prove first-diff snapshot coverage and ongoing continuity in tests.
4. Document whether trade prints identify aggressor side and whether they can conservatively inform queue consumption.
5. Reject invalid instrument metadata before replay: empty symbols, non-positive tick sizes, non-positive lot sizes, and non-finite multipliers.
6. Keep tokenized-asset assumptions in the manifest and docs before publishing sample outputs.

## Research Questions

- Does the venue's public L2 feed expose enough information for conservative passive-fill inference?
- Are tick/lot increments stable across corporate-action-like events?
- Does the product trade continuously or in sessions?
- Are fees charged in quote currency, base token, platform token, or something else?
- Is the instrument economically closer to spot equity, a derivative, or a venue-specific claim?

## Non-Goals

- No claim that public L2 data proves private fills.
- No claim that tokenized equities inherit equity-market microstructure.
- No production trading integration until venue-specific compliance, session, and risk constraints are modeled separately.
