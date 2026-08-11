#![forbid(unsafe_code)]

use std::collections::{BTreeMap, VecDeque};

#[cfg(feature = "python")]
type PythonPriceLevels = Vec<(i64, i64)>;
#[cfg(feature = "python")]
type PythonBookState = (PythonPriceLevels, PythonPriceLevels);

#[cfg(feature = "python")]
use pyo3::types::PyModuleMethods;

/// Receipt sequence breaks monotonic-clock ties. Exchange timestamps are not
/// part of this causal ordering key.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct LogicalTime {
    pub recv_monotonic_ns: u64,
    pub recv_seq: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Side {
    Bid,
    Ask,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct BookState {
    pub bids: BTreeMap<i64, i64>,
    pub asks: BTreeMap<i64, i64>,
}

impl BookState {
    #[must_use]
    pub fn is_uncrossed(&self) -> bool {
        match (self.bids.keys().next_back(), self.asks.keys().next()) {
            (Some(best_bid), Some(best_ask)) => best_bid < best_ask,
            _ => true,
        }
    }

    /// Apply a batch atomically. Invalid or crossed batches leave the previous
    /// valid state untouched.
    pub fn apply_batch(&mut self, changes: &[(Side, i64, i64)]) -> Result<(), &'static str> {
        let mut candidate = self.clone();
        for (side, price_tick, qty_lots) in changes {
            if *price_tick <= 0 {
                return Err("price tick must be positive");
            }
            if *qty_lots < 0 {
                return Err("quantity lots must be non-negative");
            }
            let levels = match side {
                Side::Bid => &mut candidate.bids,
                Side::Ask => &mut candidate.asks,
            };
            if *qty_lots == 0 {
                levels.remove(price_tick);
            } else {
                levels.insert(*price_tick, *qty_lots);
            }
        }
        if !candidate.is_uncrossed() {
            return Err("batch would cross the book");
        }
        *self = candidate;
        Ok(())
    }

    /// Stable bytes for differential comparisons with the independent Python
    /// oracle. Map iteration order is explicit through `BTreeMap`.
    #[must_use]
    pub fn canonical_bytes(&self) -> Vec<u8> {
        let mut bytes = Vec::new();
        for (price, qty) in &self.bids {
            bytes.extend_from_slice(format!("b:{price}:{qty};").as_bytes());
        }
        for (price, qty) in &self.asks {
            bytes.extend_from_slice(format!("a:{price}:{qty};").as_bytes());
        }
        bytes
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OrderState {
    Live,
    Filled,
    Cancelled,
    Expired,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MboOrder {
    pub order_id: u64,
    pub participant_id: u64,
    pub side: Side,
    pub price_tick: Option<i64>,
    pub original_lots: i64,
    pub remaining_lots: i64,
    pub arrival_sequence: u64,
    pub state: OrderState,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MboFill {
    pub maker_order_id: u64,
    pub taker_order_id: u64,
    pub price_tick: i64,
    pub qty_lots: i64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SubmitResult {
    pub accepted: bool,
    pub state: OrderState,
    pub reason: Option<&'static str>,
    pub fills: Vec<MboFill>,
}

/// Exact price-time priority exists only in this synthetic MBO mode. It is not
/// a model of historical Binance participant FIFO.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct SyntheticExchange {
    bids: BTreeMap<i64, VecDeque<u64>>,
    asks: BTreeMap<i64, VecDeque<u64>>,
    orders: BTreeMap<u64, MboOrder>,
    sequence: u64,
}

impl SyntheticExchange {
    #[must_use]
    pub fn best_bid_tick(&self) -> Option<i64> {
        self.bids.keys().next_back().copied()
    }

    #[must_use]
    pub fn best_ask_tick(&self) -> Option<i64> {
        self.asks.keys().next().copied()
    }

    #[must_use]
    pub fn order(&self, order_id: u64) -> Option<&MboOrder> {
        self.orders.get(&order_id)
    }

    fn crosses(&self, side: Side, price_tick: Option<i64>) -> bool {
        match side {
            Side::Bid => self
                .best_ask_tick()
                .is_some_and(|ask| price_tick.is_none_or(|limit| ask <= limit)),
            Side::Ask => self
                .best_bid_tick()
                .is_some_and(|bid| price_tick.is_none_or(|limit| bid >= limit)),
        }
    }

    fn best_opposite_tick(&self, side: Side) -> Option<i64> {
        match side {
            Side::Bid => self.best_ask_tick(),
            Side::Ask => self.best_bid_tick(),
        }
    }

    fn matchable(side: Side, limit: Option<i64>, resting_tick: i64) -> bool {
        limit.is_none_or(|price| match side {
            Side::Bid => resting_tick <= price,
            Side::Ask => resting_tick >= price,
        })
    }

    fn front_order_id(&self, side: Side, price_tick: i64) -> Option<u64> {
        let book = match side {
            Side::Bid => &self.asks,
            Side::Ask => &self.bids,
        };
        book.get(&price_tick)
            .and_then(|queue| queue.front().copied())
    }

    fn pop_filled_front(&mut self, aggressor_side: Side, price_tick: i64) {
        let book = match aggressor_side {
            Side::Bid => &mut self.asks,
            Side::Ask => &mut self.bids,
        };
        if let Some(queue) = book.get_mut(&price_tick) {
            queue.pop_front();
            if queue.is_empty() {
                book.remove(&price_tick);
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub fn submit(
        &mut self,
        order_id: u64,
        participant_id: u64,
        side: Side,
        price_tick: Option<i64>,
        qty_lots: i64,
        post_only: bool,
        immediate_or_cancel: bool,
    ) -> SubmitResult {
        if self.orders.contains_key(&order_id) {
            return SubmitResult {
                accepted: false,
                state: OrderState::Expired,
                reason: Some("duplicate order id"),
                fills: Vec::new(),
            };
        }
        if qty_lots <= 0 || price_tick.is_some_and(|price| price <= 0) {
            return SubmitResult {
                accepted: false,
                state: OrderState::Expired,
                reason: Some("invalid price or quantity"),
                fills: Vec::new(),
            };
        }
        if price_tick.is_none() && !immediate_or_cancel {
            return SubmitResult {
                accepted: false,
                state: OrderState::Expired,
                reason: Some("market order must be IOC"),
                fills: Vec::new(),
            };
        }
        if post_only && self.crosses(side, price_tick) {
            return SubmitResult {
                accepted: false,
                state: OrderState::Expired,
                reason: Some("post-only order would cross"),
                fills: Vec::new(),
            };
        }

        self.sequence += 1;
        let mut incoming = MboOrder {
            order_id,
            participant_id,
            side,
            price_tick,
            original_lots: qty_lots,
            remaining_lots: qty_lots,
            arrival_sequence: self.sequence,
            state: OrderState::Live,
        };
        let mut fills = Vec::new();

        while incoming.remaining_lots > 0 {
            let Some(resting_tick) = self.best_opposite_tick(side) else {
                break;
            };
            if !Self::matchable(side, price_tick, resting_tick) {
                break;
            }
            let maker_id = self
                .front_order_id(side, resting_tick)
                .expect("non-empty price level must have a front order");
            let maker_participant = self
                .orders
                .get(&maker_id)
                .expect("book order id must exist")
                .participant_id;
            if maker_participant == participant_id {
                incoming.state = OrderState::Cancelled;
                break;
            }

            let maker_remaining = self
                .orders
                .get(&maker_id)
                .expect("book order id must exist")
                .remaining_lots;
            let fill_lots = incoming.remaining_lots.min(maker_remaining);
            incoming.remaining_lots -= fill_lots;
            let maker_filled = {
                let maker = self
                    .orders
                    .get_mut(&maker_id)
                    .expect("book order id must exist");
                maker.remaining_lots -= fill_lots;
                if maker.remaining_lots == 0 {
                    maker.state = OrderState::Filled;
                    true
                } else {
                    false
                }
            };
            fills.push(MboFill {
                maker_order_id: maker_id,
                taker_order_id: order_id,
                price_tick: resting_tick,
                qty_lots: fill_lots,
            });
            if maker_filled {
                self.pop_filled_front(side, resting_tick);
            }
        }

        if incoming.remaining_lots == 0 {
            incoming.state = OrderState::Filled;
        } else if incoming.state == OrderState::Cancelled {
            // Self-trade prevention cancelled the aggressor.
        } else if immediate_or_cancel || price_tick.is_none() {
            incoming.state = OrderState::Expired;
        } else if let Some(price) = price_tick {
            let book = match side {
                Side::Bid => &mut self.bids,
                Side::Ask => &mut self.asks,
            };
            book.entry(price).or_default().push_back(order_id);
        }
        let state = incoming.state;
        self.orders.insert(order_id, incoming);
        SubmitResult {
            accepted: true,
            state,
            reason: None,
            fills,
        }
    }

    pub fn cancel(&mut self, order_id: u64) -> bool {
        let Some(order) = self.orders.get(&order_id) else {
            return false;
        };
        if order.state != OrderState::Live {
            return false;
        }
        let side = order.side;
        let Some(price_tick) = order.price_tick else {
            return false;
        };
        let book = match side {
            Side::Bid => &mut self.bids,
            Side::Ask => &mut self.asks,
        };
        if let Some(queue) = book.get_mut(&price_tick) {
            queue.retain(|queued_id| *queued_id != order_id);
            if queue.is_empty() {
                book.remove(&price_tick);
            }
        }
        self.orders
            .get_mut(&order_id)
            .expect("order must exist")
            .state = OrderState::Cancelled;
        true
    }

    #[must_use]
    pub fn canonical_bytes(&self) -> Vec<u8> {
        let mut bytes = Vec::new();
        for (price, queue) in &self.bids {
            bytes.extend_from_slice(format!("bid:{price}:{queue:?};").as_bytes());
        }
        for (price, queue) in &self.asks {
            bytes.extend_from_slice(format!("ask:{price}:{queue:?};").as_bytes());
        }
        for (order_id, order) in &self.orders {
            bytes.extend_from_slice(
                format!(
                    "order:{order_id}:{}:{:?}:{:?}:{}:{:?};",
                    order.participant_id,
                    order.side,
                    order.price_tick,
                    order.remaining_lots,
                    order.state
                )
                .as_bytes(),
            );
        }
        bytes
    }
}

#[cfg(feature = "python")]
#[pyo3::pyfunction]
fn logical_time_key(recv_monotonic_ns: u64, recv_seq: u64) -> (u64, u64) {
    (recv_monotonic_ns, recv_seq)
}

#[cfg(feature = "python")]
#[pyo3::pyfunction]
#[pyo3(signature = (best_bid=None, best_ask=None))]
fn uncrossed(best_bid: Option<i64>, best_ask: Option<i64>) -> bool {
    match (best_bid, best_ask) {
        (Some(bid), Some(ask)) => bid < ask,
        _ => true,
    }
}

#[cfg(feature = "python")]
#[pyo3::pyfunction]
fn apply_book_batch(
    bids: PythonPriceLevels,
    asks: PythonPriceLevels,
    changes: Vec<(bool, i64, i64)>,
) -> pyo3::PyResult<PythonBookState> {
    let mut book = BookState {
        bids: bids.into_iter().collect(),
        asks: asks.into_iter().collect(),
    };
    let typed_changes = changes
        .into_iter()
        .map(|(is_bid, price, qty)| (if is_bid { Side::Bid } else { Side::Ask }, price, qty))
        .collect::<Vec<_>>();
    book.apply_batch(&typed_changes)
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    Ok((
        book.bids.into_iter().collect(),
        book.asks.into_iter().collect(),
    ))
}

#[cfg(feature = "python")]
#[pyo3::pymodule]
fn lob_core(m: &pyo3::Bound<'_, pyo3::types::PyModule>) -> pyo3::PyResult<()> {
    m.add_function(pyo3::wrap_pyfunction!(logical_time_key, m)?)?;
    m.add_function(pyo3::wrap_pyfunction!(uncrossed, m)?)?;
    m.add_function(pyo3::wrap_pyfunction!(apply_book_batch, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{BookState, OrderState, Side, SyntheticExchange};

    #[test]
    fn invalid_book_batch_is_atomic() {
        let mut book = BookState::default();
        book.apply_batch(&[(Side::Bid, 100, 2), (Side::Ask, 101, 3)])
            .expect("valid seed");
        let before = book.clone();
        assert_eq!(
            book.apply_batch(&[(Side::Bid, 102, 1)]),
            Err("batch would cross the book")
        );
        assert_eq!(book, before);
    }

    #[test]
    fn synthetic_exchange_matches_exact_fifo() {
        let mut exchange = SyntheticExchange::default();
        assert!(
            exchange
                .submit(1, 10, Side::Ask, Some(101), 2, true, false)
                .accepted
        );
        assert!(
            exchange
                .submit(2, 20, Side::Ask, Some(101), 2, true, false)
                .accepted
        );
        let result = exchange.submit(3, 30, Side::Bid, Some(101), 3, false, true);
        assert_eq!(
            result
                .fills
                .iter()
                .map(|fill| (fill.maker_order_id, fill.qty_lots))
                .collect::<Vec<_>>(),
            vec![(1, 2), (2, 1)]
        );
        assert_eq!(
            exchange.order(1).expect("maker one").state,
            OrderState::Filled
        );
        assert_eq!(exchange.order(2).expect("maker two").remaining_lots, 1);
    }

    #[test]
    fn post_only_and_self_trade_prevention_fail_closed() {
        let mut exchange = SyntheticExchange::default();
        exchange.submit(1, 10, Side::Ask, Some(101), 1, true, false);
        let rejected = exchange.submit(2, 20, Side::Bid, Some(101), 1, true, false);
        assert!(!rejected.accepted);
        let prevented = exchange.submit(3, 10, Side::Bid, Some(101), 1, false, true);
        assert_eq!(prevented.state, OrderState::Cancelled);
        assert!(prevented.fills.is_empty());
        assert_eq!(
            exchange.order(1).expect("resting own order").state,
            OrderState::Live
        );
    }

    #[test]
    fn cancel_removes_live_order_and_canonical_state_is_stable() {
        let mut first = SyntheticExchange::default();
        let mut second = SyntheticExchange::default();
        for exchange in [&mut first, &mut second] {
            exchange.submit(1, 10, Side::Bid, Some(100), 1, true, false);
        }
        assert_eq!(first.canonical_bytes(), second.canonical_bytes());
        assert!(first.cancel(1));
        assert_eq!(first.best_bid_tick(), None);
        assert!(!first.cancel(1));
    }
}
