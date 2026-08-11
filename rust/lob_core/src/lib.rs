#![forbid(unsafe_code)]

use std::collections::{BTreeMap, VecDeque};

use sha2::{Digest, Sha256};

#[cfg(feature = "python")]
type PythonPriceLevels = Vec<(i64, i64)>;
#[cfg(feature = "python")]
type PythonBookState = (PythonPriceLevels, PythonPriceLevels);
#[cfg(feature = "python")]
type PythonSyntheticOperation = (u8, u64, u64, bool, Option<i64>, i64, bool, bool);
#[cfg(feature = "python")]
type PythonSyntheticFill = (u64, u64, i64, i64);
#[cfg(feature = "python")]
type PythonSyntheticTraceRow = (
    bool,
    String,
    Option<String>,
    Vec<PythonSyntheticFill>,
    Option<String>,
);

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
    Rejected,
    Live,
    Filled,
    Cancelled,
    Expired,
}

impl OrderState {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Rejected => "rejected",
            Self::Live => "live",
            Self::Filled => "filled",
            Self::Cancelled => "cancelled",
            Self::Expired => "expired",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MboOrder {
    pub order_id: u64,
    pub participant_id: u64,
    pub side: Side,
    pub price_tick: Option<i64>,
    pub original_lots: i64,
    pub remaining_lots: i64,
    pub immediate_or_cancel: bool,
    pub post_only: bool,
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
        self.sequence += 1;
        self.submit_current(
            order_id,
            participant_id,
            side,
            price_tick,
            qty_lots,
            post_only,
            immediate_or_cancel,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn submit_current(
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
                state: OrderState::Rejected,
                reason: Some("duplicate_order_id"),
                fills: Vec::new(),
            };
        }
        if qty_lots <= 0 {
            return SubmitResult {
                accepted: false,
                state: OrderState::Rejected,
                reason: Some("invalid_quantity"),
                fills: Vec::new(),
            };
        }
        if price_tick.is_some_and(|price| price <= 0) {
            return SubmitResult {
                accepted: false,
                state: OrderState::Rejected,
                reason: Some("invalid_price"),
                fills: Vec::new(),
            };
        }
        if price_tick.is_none() && !immediate_or_cancel {
            return SubmitResult {
                accepted: false,
                state: OrderState::Rejected,
                reason: Some("market_order_requires_ioc"),
                fills: Vec::new(),
            };
        }
        if price_tick.is_none() && post_only {
            return SubmitResult {
                accepted: false,
                state: OrderState::Rejected,
                reason: Some("market_order_cannot_be_post_only"),
                fills: Vec::new(),
            };
        }
        if post_only && self.crosses(side, price_tick) {
            return SubmitResult {
                accepted: false,
                state: OrderState::Rejected,
                reason: Some("post_only_would_cross"),
                fills: Vec::new(),
            };
        }

        let mut incoming = MboOrder {
            order_id,
            participant_id,
            side,
            price_tick,
            original_lots: qty_lots,
            remaining_lots: qty_lots,
            immediate_or_cancel,
            post_only,
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
        self.sequence += 1;
        self.cancel_current(order_id)
    }

    fn cancel_current(&mut self, order_id: u64) -> bool {
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

    pub fn replace(
        &mut self,
        order_id: u64,
        new_order_id: u64,
        price_tick: Option<i64>,
        qty_lots: i64,
        post_only: bool,
    ) -> SubmitResult {
        self.sequence += 1;
        let Some(existing) = self.orders.get(&order_id) else {
            return SubmitResult {
                accepted: false,
                state: OrderState::Rejected,
                reason: Some("unknown_order"),
                fills: Vec::new(),
            };
        };
        if existing.state != OrderState::Live {
            return SubmitResult {
                accepted: false,
                state: existing.state,
                reason: Some("order_not_live"),
                fills: Vec::new(),
            };
        }
        let participant_id = existing.participant_id;
        let side = existing.side;
        if qty_lots <= 0 {
            return SubmitResult {
                accepted: false,
                state: OrderState::Live,
                reason: Some("invalid_quantity"),
                fills: Vec::new(),
            };
        }
        if price_tick.is_some_and(|price| price <= 0) {
            return SubmitResult {
                accepted: false,
                state: OrderState::Live,
                reason: Some("invalid_price"),
                fills: Vec::new(),
            };
        }
        if price_tick.is_none() {
            return SubmitResult {
                accepted: false,
                state: OrderState::Live,
                reason: Some("market_order_requires_ioc"),
                fills: Vec::new(),
            };
        }
        if self.orders.contains_key(&new_order_id) {
            return SubmitResult {
                accepted: false,
                state: OrderState::Live,
                reason: Some("duplicate_order_id"),
                fills: Vec::new(),
            };
        }
        if post_only && self.crosses(side, price_tick) {
            return SubmitResult {
                accepted: false,
                state: OrderState::Live,
                reason: Some("post_only_would_cross"),
                fills: Vec::new(),
            };
        }

        let cancelled = self.cancel_current(order_id);
        debug_assert!(cancelled);
        self.submit_current(
            new_order_id,
            participant_id,
            side,
            price_tick,
            qty_lots,
            post_only,
            false,
        )
    }

    #[must_use]
    pub fn canonical_bytes(&self) -> Vec<u8> {
        let mut state = String::new();
        for (price, queue) in &self.bids {
            state.push_str(&format!("bid:{price}:"));
            for (index, order_id) in queue.iter().enumerate() {
                if index > 0 {
                    state.push(',');
                }
                state.push_str(&order_id.to_string());
            }
            state.push(';');
        }
        for (price, queue) in &self.asks {
            state.push_str(&format!("ask:{price}:"));
            for (index, order_id) in queue.iter().enumerate() {
                if index > 0 {
                    state.push(',');
                }
                state.push_str(&order_id.to_string());
            }
            state.push(';');
        }
        for (order_id, order) in &self.orders {
            let side = match order.side {
                Side::Bid => "bid",
                Side::Ask => "ask",
            };
            let price = order
                .price_tick
                .map_or_else(|| "none".to_owned(), |value| value.to_string());
            state.push_str(&format!(
                "order:{order_id}:{}:{side}:{price}:{}:{}:{}:{}:{}:{};",
                order.participant_id,
                order.original_lots,
                order.remaining_lots,
                order.arrival_sequence,
                if order.immediate_or_cancel {
                    "IOC"
                } else {
                    "GTC"
                },
                order.post_only,
                order.state.as_str()
            ));
        }
        state.into_bytes()
    }

    #[must_use]
    pub fn state_sha256(&self) -> String {
        format!("{:x}", Sha256::digest(self.canonical_bytes()))
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

/// Run a deterministic exact-MBO operation trace for differential testing.
///
/// Operation tuples are `(kind, order_id, participant_id, is_bid,
/// price_tick, qty_lots, post_only, immediate_or_cancel)`, where kind `0` is
/// submit, kind `1` is cancel, and kind `2` is replace (using participant_id as
/// the new order ID). The returned row contains the lifecycle result, fills,
/// and a periodic full-state hash. This remains synthetic exchange scope and
/// does not imply historical venue FIFO knowledge.
#[cfg(feature = "python")]
#[pyo3::pyfunction]
fn run_synthetic_trace(
    operations: Vec<PythonSyntheticOperation>,
    checkpoint_interval: usize,
) -> pyo3::PyResult<Vec<PythonSyntheticTraceRow>> {
    if checkpoint_interval == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "checkpoint_interval must be positive",
        ));
    }
    let operation_count = operations.len();
    let mut exchange = SyntheticExchange::default();
    let mut rows = Vec::with_capacity(operation_count);

    for (index, operation) in operations.into_iter().enumerate() {
        let (kind, order_id, participant_id, is_bid, price_tick, qty_lots, post_only, ioc) =
            operation;
        let (accepted, state, reason, fills) = match kind {
            0 => {
                let result = exchange.submit(
                    order_id,
                    participant_id,
                    if is_bid { Side::Bid } else { Side::Ask },
                    price_tick,
                    qty_lots,
                    post_only,
                    ioc,
                );
                let fills = result
                    .fills
                    .into_iter()
                    .map(|fill| {
                        (
                            fill.maker_order_id,
                            fill.taker_order_id,
                            fill.price_tick,
                            fill.qty_lots,
                        )
                    })
                    .collect();
                (
                    result.accepted,
                    result.state.as_str().to_owned(),
                    result.reason.map(str::to_owned),
                    fills,
                )
            }
            1 => {
                let existing_state = exchange.order(order_id).map(|order| order.state);
                let cancelled = exchange.cancel(order_id);
                match existing_state {
                    None => (
                        false,
                        OrderState::Rejected.as_str().to_owned(),
                        Some("unknown_order".to_owned()),
                        Vec::new(),
                    ),
                    Some(OrderState::Live) => {
                        debug_assert!(cancelled);
                        (
                            true,
                            OrderState::Cancelled.as_str().to_owned(),
                            None,
                            Vec::new(),
                        )
                    }
                    Some(state) => (
                        false,
                        state.as_str().to_owned(),
                        Some("order_not_live".to_owned()),
                        Vec::new(),
                    ),
                }
            }
            2 => {
                let result =
                    exchange.replace(order_id, participant_id, price_tick, qty_lots, post_only);
                let fills = result
                    .fills
                    .into_iter()
                    .map(|fill| {
                        (
                            fill.maker_order_id,
                            fill.taker_order_id,
                            fill.price_tick,
                            fill.qty_lots,
                        )
                    })
                    .collect();
                (
                    result.accepted,
                    result.state.as_str().to_owned(),
                    result.reason.map(str::to_owned),
                    fills,
                )
            }
            _ => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unsupported synthetic operation kind: {kind}"
                )))
            }
        };
        let ordinal = index + 1;
        let checkpoint = if ordinal % checkpoint_interval == 0 || ordinal == operation_count {
            Some(exchange.state_sha256())
        } else {
            None
        };
        rows.push((accepted, state, reason, fills, checkpoint));
    }
    Ok(rows)
}

#[cfg(feature = "python")]
#[pyo3::pymodule]
fn lob_core(m: &pyo3::Bound<'_, pyo3::types::PyModule>) -> pyo3::PyResult<()> {
    m.add_function(pyo3::wrap_pyfunction!(logical_time_key, m)?)?;
    m.add_function(pyo3::wrap_pyfunction!(uncrossed, m)?)?;
    m.add_function(pyo3::wrap_pyfunction!(apply_book_batch, m)?)?;
    m.add_function(pyo3::wrap_pyfunction!(run_synthetic_trace, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;

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
        assert_eq!(first.state_sha256(), second.state_sha256());
        assert_eq!(first.state_sha256().len(), 64);
        assert!(first.cancel(1));
        assert_eq!(first.best_bid_tick(), None);
        assert!(!first.cancel(1));
    }

    #[test]
    fn rejected_submissions_use_lifecycle_reasons_and_preserve_state() {
        let mut exchange = SyntheticExchange::default();
        exchange.submit(1, 10, Side::Ask, Some(101), 1, true, false);
        let before = exchange.state_sha256();

        let duplicate = exchange.submit(1, 20, Side::Bid, Some(100), 1, false, false);
        assert_eq!(duplicate.state, OrderState::Rejected);
        assert_eq!(duplicate.reason, Some("duplicate_order_id"));
        assert_eq!(exchange.state_sha256(), before);

        let invalid_market = exchange.submit(2, 20, Side::Bid, None, 1, true, true);
        assert_eq!(invalid_market.state, OrderState::Rejected);
        assert_eq!(
            invalid_market.reason,
            Some("market_order_cannot_be_post_only")
        );
        assert_eq!(exchange.state_sha256(), before);
    }

    #[test]
    fn replace_is_atomic_and_loses_time_priority() {
        let mut exchange = SyntheticExchange::default();
        exchange.submit(1, 10, Side::Bid, Some(100), 1, true, false);
        exchange.submit(2, 20, Side::Bid, Some(100), 1, true, false);

        let invalid = exchange.replace(1, 3, Some(0), 1, false);
        assert!(!invalid.accepted);
        assert_eq!(invalid.state, OrderState::Live);
        assert_eq!(invalid.reason, Some("invalid_price"));
        assert_eq!(
            exchange.bids.get(&100).expect("bid queue"),
            &VecDeque::from([1, 2])
        );

        let replaced = exchange.replace(1, 3, Some(100), 1, false);
        assert!(replaced.accepted);
        assert_eq!(replaced.state, OrderState::Live);
        assert_eq!(
            exchange.bids.get(&100).expect("bid queue"),
            &VecDeque::from([2, 3])
        );
        assert_eq!(
            exchange.order(1).expect("old order").state,
            OrderState::Cancelled
        );
        assert_eq!(exchange.order(3).expect("new order").arrival_sequence, 4);
    }
}
