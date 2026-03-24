# Design Choices

## Why the options demo is synthetic

The goal is transparent dealer pricing and risk. A small synthetic chain and scenario-driven flow keep fair value, reservation price, half-spread, and signed markout readable on one screen.

## Why the artifact focuses on dealer pricing and risk

The repo already has exchange-style microstructure on the futures side. The options artifact is aimed at quoting, inventory transfer pricing, hedging, and surface risk, so an explicit options matching engine would add complexity before it improved reviewer signal.

## Why delta is hedged while gamma and vega are warehoused

Delta is the fastest and cheapest risk to reduce because it can be hedged directly in the underlying. Gamma and vega are left on as warehoused surface risk so reservation price and follow-on risk decisions stay visible.

## Why signed markout is shown separately from PnL

Signed markout answers a fill-quality question in contract dollars. PnL answers whether the dealer still made or lost money after spread capture, hedging, and inventory marking, so combining them would blur two different judgments.

## Why scenario comparison matters more than one flattering run

A single path can look good for accidental reasons. Running the same logic through calm, volatile, toxic, and inventory-stress settings is a faster test of trader judgment than polishing one cherry-picked example.
