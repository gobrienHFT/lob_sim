# Sanity Checks

Small pricing checks I would do before trusting the options case-study output.

## Put-call parity

For the same strike and expiry, call fair value minus put fair value should stay close to spot minus discounted strike. In this repo the rates are simple and the horizons are short, so the point is not penny-perfect calibration; it is that the call and put fair values stay internally consistent before reservation price and half-spread are applied.

## Call fair value should rise with spot

If spot goes up while strike, expiry, and implied vol stay fixed, call fair value should not go down. That quick bump test tells you the quoted bid and ask are anchored to a sensible fair value before inventory skew is layered on.

## Delta sign and gamma positivity

Calls should have positive delta, puts negative delta, and vanilla option gamma should stay positive. That is the basic shape check behind both fair value and reservation price: the dealer is managing risk around a convex options surface, not around arbitrary quote moves.

## Tiny reservation-price check

Suppose fair value is `2.10`, half-spread is `0.12`, and reservation price is `-0.08` because the book wants to attract customer sells and discourage more buying risk.

`bid = 2.10 - 0.12 - (-0.08) = 2.06`

`ask = 2.10 + 0.12 - (-0.08) = 2.30`

If `2` contracts trade on that bid with contract size `100`, the premium exchanged is `2.06 * 2 * 100 = 412` contract dollars. Signed markout is then judged later against future fair value, not against the half-spread itself.
