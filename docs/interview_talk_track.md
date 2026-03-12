# Options Interview Talk Track

Fastest prep doc for a live walkthrough.

## 15-second overview

This is a transparent options market-making case study, not production infrastructure. It shows how a dealer quotes around Black-Scholes fair value using a half-spread and a reservation price, tracks toxic versus non-toxic flow through signed markout, hedges net delta in the underlying, and leaves warehoused vega visible across the surface.

## 45-second model explanation

The options side is a synthetic dealer simulation built to make the mechanics inspectable. Each step, the model selects one option from a small chain, prices it with Black-Scholes on a simple skewed vol surface, and builds a quote around fair value:

`bid = fair_value - half_spread - reservation_price`

`ask = fair_value + half_spread - reservation_price`

`half_spread` is the compensation for making a market and widens with realized volatility and portfolio gamma pressure. `reservation_price` is the inventory term: if the book already carries too much delta or vega in one direction, the quote shifts to discourage more of that risk and attract offsetting flow. Customer flow is synthetic and scenario-driven, with a configurable toxic-flow share. After a fill, the dealer updates inventory, optionally hedges net delta in the underlying if a risk trigger is breached, then marks the portfolio and records PnL and signed markout.

## 30-second walkthrough of one `fills.csv` row

I would read one row left to right. `step` and `spot_before` tell me where the underlying was. `contract`, `option_type`, `strike`, and `expiry_days` tell me what I quoted. `fair_value`, `bid`, `ask`, and `fill_price` show the model value and the market I made. `customer_side` and `mm_side` tell me who initiated the trade and what the dealer actually did. `reservation_price` and the half-spread components explain why the quote was skewed or widened. `toxic_flow` and `signed_markout` tell me whether the fill aged well one step later in contract dollars. `portfolio_delta_after_trade`, `hedge_qty`, and `portfolio_delta_after_hedge` show the risk-management response. `comment_flag` is just a short plain-English tag for the trade.

## 20-second limitations

This is a synthetic dealer study, not an options exchange replay. The option chain, flow process, and toxicity are transparent approximations rather than venue-calibrated models. The strategy hedges delta in the underlying, but gamma and vega are intentionally warehoused as surface risk so the quoting and inventory trade-offs stay visible.

## Likely questions

### Why is the demo synthetic?

Because the goal is clarity rather than venue plumbing. A synthetic setup lets me expose fair value, reservation price, signed markout, hedging, and PnL decomposition directly instead of burying them inside sparse options market data and exchange-specific mechanics.

### Why is Black-Scholes still useful here?

It is a clean baseline for fair value and Greeks. Even if a real desk would use richer vol dynamics, Black-Scholes is still a defensible starting point for showing how quoting, delta, gamma, and vega interact.

### How does reservation price work here?

Reservation price is an inventory-driven shift applied to both bid and ask. If the portfolio already has too much delta or vega in one direction, the quote moves to make more of that risk less attractive and offsetting flow more attractive.

### What does toxic flow mean in this model?

Toxic flow means the fill is more likely to be informed against the current quote. In practice, the next-step move is biased so signed markout is more likely to be negative for the dealer.

### How is markout defined here?

Signed markout compares the fill price with the model fair value at a fixed future horizon from the realized path and reports the result in contract dollars. Positive is good for the dealer, negative means the trade aged badly and indicates adverse selection.

### Why hedge delta but warehouse gamma and vega?

Delta is the fastest and cheapest risk to reduce because it can be hedged directly in the underlying. Gamma and vega are left on as warehoused surface risk so the demo keeps the inventory trade-off visible instead of pretending the book is fully neutralized.

### What would need real data to calibrate properly?

The implied-vol surface, customer arrival intensity, trade-size mix, toxic-flow share, markout horizon behavior, and hedge-cost assumptions would all need real market data for calibration.

### What is the biggest weakness?

There is no explicit options exchange matching engine. This is strongest as a dealer pricing and risk case study, not as a venue-realistic options microstructure simulator.

### What would you build next?

I would calibrate the flow and surface assumptions from real data, add cross-option hedging for gamma and vega, and build a separate recorder for live options market data if venue realism became the priority.

### What does the scenario system add?

It shows regime sensitivity. The same dealer logic can be run under calmer, more volatile, more toxic, or more inventory-heavy conditions, so the outputs are not just one flattering path.
