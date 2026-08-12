#![forbid(unsafe_code)]

use std::collections::{BTreeMap, BTreeSet, VecDeque};

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
type PythonSchedulerOperation = (u8, u64, u64, u64, bool);
#[cfg(feature = "python")]
type PythonSchedulerTraceRow = (bool, Option<String>, Vec<u64>, usize, Option<String>);
#[cfg(feature = "python")]
type PythonRiskOperation = (u8, u64, bool, i64);
#[cfg(feature = "python")]
type PythonRiskTraceRow = (bool, Option<String>, i64, i128, i128, Option<String>);
#[cfg(feature = "python")]
type PythonPortfolioOperation = (u8, u64, u32, bool, i64);
#[cfg(feature = "python")]
type PythonPortfolioTraceRow = (bool, Option<String>, i128, i128, i128, Option<String>);
#[cfg(feature = "python")]
type PythonAccountingOperation = (u8, u32, bool, i64, i64, i64);
#[cfg(feature = "python")]
type PythonAccountingTraceRow = (
    bool,
    Option<String>,
    i128,
    i128,
    i128,
    i128,
    Option<i128>,
    bool,
    i128,
    i128,
    Option<String>,
);
#[cfg(feature = "python")]
type PythonLatencyTraceRow = (i64, u64);

#[cfg(feature = "python")]
use pyo3::types::PyModuleMethods;

/// Receipt sequence breaks monotonic-clock ties. Exchange timestamps are not
/// part of this causal ordering key.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct LogicalTime {
    pub recv_monotonic_ns: u64,
    pub recv_seq: u64,
}

#[cfg(any(feature = "python", test))]
const LATENCY_SPLITMIX_GOLDEN: u64 = 0x9E37_79B9_7F4A_7C15;
#[cfg(any(feature = "python", test))]
const LATENCY_SPLITMIX_MIX1: u64 = 0xBF58_476D_1CE4_E5B9;
#[cfg(any(feature = "python", test))]
const LATENCY_SPLITMIX_MIX2: u64 = 0x94D0_49BB_1331_11EB;
#[cfg(any(feature = "python", test))]
const LATENCY_STRESS_SCALE: i64 = 1_000_000;

#[cfg(any(feature = "python", test))]
#[derive(Clone, Debug, Eq, PartialEq)]
struct ScenarioLatencySampler {
    mode: u8,
    fixed_new_us: i64,
    fixed_cancel_us: i64,
    samples_us: Vec<i64>,
    stress_multiplier_ppm: i64,
    state: u64,
}

#[cfg(any(feature = "python", test))]
impl ScenarioLatencySampler {
    fn new(
        mode: u8,
        fixed_new_us: i64,
        fixed_cancel_us: i64,
        samples_us: Vec<i64>,
        stress_multiplier_ppm: i64,
        seed: u64,
    ) -> Result<Self, &'static str> {
        if mode > 2 {
            return Err("latency mode must be fixed, empirical, or stress_tail");
        }
        if fixed_new_us < 0 || fixed_cancel_us < 0 {
            return Err("fixed latencies must be >= 0");
        }
        if samples_us.iter().any(|sample| *sample < 0) {
            return Err("latency samples must be >= 0");
        }
        if mode != 0 && samples_us.is_empty() {
            return Err("empirical/stress_tail latency modes require samples");
        }
        if stress_multiplier_ppm < LATENCY_STRESS_SCALE {
            return Err("stress_multiplier_ppm must be >= 1000000");
        }
        Ok(Self {
            mode,
            fixed_new_us,
            fixed_cancel_us,
            samples_us,
            stress_multiplier_ppm,
            state: seed,
        })
    }

    fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(LATENCY_SPLITMIX_GOLDEN);
        let mut value = self.state;
        value = (value ^ (value >> 30)).wrapping_mul(LATENCY_SPLITMIX_MIX1);
        value = (value ^ (value >> 27)).wrapping_mul(LATENCY_SPLITMIX_MIX2);
        value ^ (value >> 31)
    }

    fn draw(&mut self, component: u8) -> Result<i64, &'static str> {
        if component > 1 {
            return Err("latency component must be new_order or cancel");
        }
        match self.mode {
            0 => Ok(if component == 0 {
                self.fixed_new_us
            } else {
                self.fixed_cancel_us
            }),
            1 => {
                let index = (self.next_u64() % self.samples_us.len() as u64) as usize;
                Ok(self.samples_us[index])
            }
            2 => {
                let maximum = self.samples_us.iter().copied().max().unwrap_or(0);
                let product = (maximum as i128) * (self.stress_multiplier_ppm as i128);
                let value = product / i128::from(LATENCY_STRESS_SCALE);
                i64::try_from(value).map_err(|_| "latency stress draw overflow")
            }
            _ => unreachable!(),
        }
    }

    fn state(&self) -> u64 {
        self.state
    }
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

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct SchedulerKey {
    due: LogicalTime,
    insertion_sequence: u64,
    action_id: u64,
}

/// Deterministic integer-nanosecond scheduler used as a kernel parity boundary.
///
/// Actions due strictly before an observation can be drained separately from
/// actions due exactly at the observation. Exact ties retain insertion order.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct DeterministicScheduler {
    next_insertion_sequence: u64,
    pending: BTreeSet<SchedulerKey>,
    by_id: BTreeMap<u64, SchedulerKey>,
    seen_action_ids: BTreeSet<u64>,
}

impl DeterministicScheduler {
    pub fn schedule(&mut self, action_id: u64, due: LogicalTime) -> Result<(), &'static str> {
        if self.seen_action_ids.contains(&action_id) {
            return Err("duplicate_action_id");
        }
        let insertion_sequence = self.next_insertion_sequence;
        self.next_insertion_sequence = self
            .next_insertion_sequence
            .checked_add(1)
            .ok_or("scheduler_sequence_overflow")?;
        let key = SchedulerKey {
            due,
            insertion_sequence,
            action_id,
        };
        self.seen_action_ids.insert(action_id);
        self.pending.insert(key);
        self.by_id.insert(action_id, key);
        Ok(())
    }

    pub fn cancel(&mut self, action_id: u64) -> Result<(), &'static str> {
        let Some(key) = self.by_id.remove(&action_id) else {
            return Err("unknown_action");
        };
        self.pending.remove(&key);
        Ok(())
    }

    #[must_use]
    pub fn drain(&mut self, cutoff: LogicalTime, inclusive: bool) -> Vec<u64> {
        let mut drained = Vec::new();
        loop {
            let Some(key) = self.pending.first().copied() else {
                break;
            };
            if key.due > cutoff || (key.due == cutoff && !inclusive) {
                break;
            }
            self.pending.remove(&key);
            self.by_id.remove(&key.action_id);
            drained.push(key.action_id);
        }
        drained
    }

    #[must_use]
    pub fn pending_count(&self) -> usize {
        self.pending.len()
    }

    #[must_use]
    pub fn canonical_bytes(&self) -> Vec<u8> {
        let mut state = format!("next:{};seen:", self.next_insertion_sequence);
        for (index, action_id) in self.seen_action_ids.iter().enumerate() {
            if index > 0 {
                state.push(',');
            }
            state.push_str(&action_id.to_string());
        }
        state.push(';');
        for action in &self.pending {
            state.push_str(&format!(
                "action:{}:{}:{}:{};",
                action.action_id,
                action.due.recv_monotonic_ns,
                action.due.recv_seq,
                action.insertion_sequence
            ));
        }
        state.into_bytes()
    }

    #[must_use]
    pub fn state_sha256(&self) -> String {
        format!("{:x}", Sha256::digest(self.canonical_bytes()))
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ReservationState {
    Live,
    PendingCancel,
    Filled,
    Cancelled,
    EpochInvalidated,
}

impl ReservationState {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Live => "live",
            Self::PendingCancel => "pending_cancel",
            Self::Filled => "filled",
            Self::Cancelled => "cancelled",
            Self::EpochInvalidated => "epoch_invalidated",
        }
    }

    const fn reserves(self) -> bool {
        matches!(self, Self::Live | Self::PendingCancel)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ReservedOrder {
    is_bid: bool,
    remaining_lots: i64,
    state: ReservationState,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ReservationDecision {
    pub accepted: bool,
    pub reason: Option<&'static str>,
}

impl ReservationDecision {
    const fn accepted() -> Self {
        Self {
            accepted: true,
            reason: None,
        }
    }

    const fn rejected(reason: &'static str) -> Self {
        Self {
            accepted: false,
            reason: Some(reason),
        }
    }
}

/// Worst-case per-symbol live-plus-pending lot reservation ledger.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RiskReservationLedger {
    max_position_lots: i64,
    position_lots: i64,
    orders: BTreeMap<u64, ReservedOrder>,
    seen_order_ids: BTreeSet<u64>,
}

impl RiskReservationLedger {
    pub fn new(max_position_lots: i64) -> Result<Self, &'static str> {
        if max_position_lots <= 0 {
            return Err("max_position_lots must be positive");
        }
        Ok(Self {
            max_position_lots,
            position_lots: 0,
            orders: BTreeMap::new(),
            seen_order_ids: BTreeSet::new(),
        })
    }

    fn reservation_totals_i128(&self) -> (i128, i128) {
        self.orders
            .values()
            .filter(|order| order.state.reserves())
            .fold((0, 0), |(buy_lots, sell_lots), order| {
                if order.is_bid {
                    (buy_lots + i128::from(order.remaining_lots), sell_lots)
                } else {
                    (buy_lots, sell_lots + i128::from(order.remaining_lots))
                }
            })
    }

    fn within_limits(&self, buy_lots: i128, sell_lots: i128) -> bool {
        let position = i128::from(self.position_lots);
        let limit = i128::from(self.max_position_lots);
        position + buy_lots <= limit && position - sell_lots >= -limit
    }

    fn debug_assert_invariants(&self) {
        let (buy_lots, sell_lots) = self.reservation_totals_i128();
        debug_assert!(self.within_limits(buy_lots, sell_lots));
        debug_assert!(self.orders.values().all(|order| order.remaining_lots >= 0));
    }

    pub fn reserve(&mut self, order_id: u64, is_bid: bool, qty_lots: i64) -> ReservationDecision {
        if self.seen_order_ids.contains(&order_id) {
            return ReservationDecision::rejected("duplicate_order_id");
        }
        self.seen_order_ids.insert(order_id);
        if qty_lots <= 0 {
            return ReservationDecision::rejected("invalid_quantity");
        }
        let (mut buy_lots, mut sell_lots) = self.reservation_totals_i128();
        let reason = if is_bid {
            buy_lots += i128::from(qty_lots);
            "long_limit"
        } else {
            sell_lots += i128::from(qty_lots);
            "short_limit"
        };
        if !self.within_limits(buy_lots, sell_lots) {
            return ReservationDecision::rejected(reason);
        }
        self.orders.insert(
            order_id,
            ReservedOrder {
                is_bid,
                remaining_lots: qty_lots,
                state: ReservationState::Live,
            },
        );
        self.debug_assert_invariants();
        ReservationDecision::accepted()
    }

    pub fn request_cancel(&mut self, order_id: u64) -> ReservationDecision {
        let Some(order) = self.orders.get_mut(&order_id) else {
            return ReservationDecision::rejected("unknown_order");
        };
        if order.state != ReservationState::Live {
            return ReservationDecision::rejected("order_not_live");
        }
        order.state = ReservationState::PendingCancel;
        self.debug_assert_invariants();
        ReservationDecision::accepted()
    }

    pub fn cancel_ack(&mut self, order_id: u64) -> ReservationDecision {
        let Some(order) = self.orders.get_mut(&order_id) else {
            return ReservationDecision::rejected("unknown_order");
        };
        if order.state != ReservationState::PendingCancel {
            return ReservationDecision::rejected("cancel_not_pending");
        }
        order.state = ReservationState::Cancelled;
        self.debug_assert_invariants();
        ReservationDecision::accepted()
    }

    pub fn fill(&mut self, order_id: u64, qty_lots: i64) -> ReservationDecision {
        let Some(order) = self.orders.get(&order_id) else {
            return ReservationDecision::rejected("unknown_order");
        };
        if !order.state.reserves() {
            return ReservationDecision::rejected("order_not_fillable");
        }
        if qty_lots <= 0 {
            return ReservationDecision::rejected("invalid_fill_quantity");
        }
        if qty_lots > order.remaining_lots {
            return ReservationDecision::rejected("fill_exceeds_remaining");
        }
        let is_bid = order.is_bid;
        let next_position = if is_bid {
            self.position_lots.checked_add(qty_lots)
        } else {
            self.position_lots.checked_sub(qty_lots)
        };
        let Some(next_position) = next_position else {
            return ReservationDecision::rejected("position_overflow");
        };
        let order = self
            .orders
            .get_mut(&order_id)
            .expect("validated order must exist");
        order.remaining_lots -= qty_lots;
        if order.remaining_lots == 0 {
            order.state = ReservationState::Filled;
        }
        self.position_lots = next_position;
        self.debug_assert_invariants();
        ReservationDecision::accepted()
    }

    pub fn invalidate_epoch(&mut self) -> ReservationDecision {
        for order in self.orders.values_mut() {
            if order.state.reserves() {
                order.state = ReservationState::EpochInvalidated;
            }
        }
        self.debug_assert_invariants();
        ReservationDecision::accepted()
    }

    #[must_use]
    pub fn position_lots(&self) -> i64 {
        self.position_lots
    }

    #[must_use]
    pub fn reservation_totals(&self) -> (i128, i128) {
        self.reservation_totals_i128()
    }

    #[must_use]
    pub fn canonical_bytes(&self) -> Vec<u8> {
        let mut state = format!(
            "max:{};position:{};seen:",
            self.max_position_lots, self.position_lots
        );
        for (index, order_id) in self.seen_order_ids.iter().enumerate() {
            if index > 0 {
                state.push(',');
            }
            state.push_str(&order_id.to_string());
        }
        state.push(';');
        for (order_id, order) in &self.orders {
            state.push_str(&format!(
                "order:{order_id}:{}:{}:{};",
                if order.is_bid { "bid" } else { "ask" },
                order.remaining_lots,
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

#[derive(Clone, Debug, Eq, PartialEq)]
struct PortfolioReservedOrder {
    symbol_id: u32,
    is_bid: bool,
    remaining_notional_units: i64,
    state: ReservationState,
}

/// Conservative gross-notional reservation ledger across all symbols.
///
/// Inventory is supplied as externally marked fixed-point notional units. The
/// ledger deliberately does not net symbols, sides, or live versus pending
/// orders: every active order remains reserved until a cancel acknowledgement,
/// fill, or epoch invalidation is observed.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PortfolioNotionalReservationLedger {
    max_notional_units: i64,
    inventory_by_symbol: BTreeMap<u32, i64>,
    orders: BTreeMap<u64, PortfolioReservedOrder>,
    seen_order_ids: BTreeSet<u64>,
}

impl PortfolioNotionalReservationLedger {
    pub fn new(max_notional_units: i64) -> Result<Self, &'static str> {
        if max_notional_units <= 0 {
            return Err("max_notional_units must be positive");
        }
        Ok(Self {
            max_notional_units,
            inventory_by_symbol: BTreeMap::new(),
            orders: BTreeMap::new(),
            seen_order_ids: BTreeSet::new(),
        })
    }

    fn gross_inventory_units(&self) -> i128 {
        self.inventory_by_symbol
            .values()
            .map(|value| i128::from(*value).abs())
            .sum()
    }

    fn reserved_order_units(&self) -> i128 {
        self.orders
            .values()
            .filter(|order| order.state.reserves())
            .map(|order| i128::from(order.remaining_notional_units))
            .sum()
    }

    fn within_limit(&self, gross_inventory_units: i128, reserved_order_units: i128) -> bool {
        gross_inventory_units + reserved_order_units <= i128::from(self.max_notional_units)
    }

    fn debug_assert_invariants(&self) {
        debug_assert!(self.within_limit(self.gross_inventory_units(), self.reserved_order_units()));
        debug_assert!(self
            .orders
            .values()
            .all(|order| order.remaining_notional_units >= 0));
    }

    pub fn set_inventory(
        &mut self,
        symbol_id: u32,
        marked_notional_units: i64,
    ) -> ReservationDecision {
        let mut candidate = self.inventory_by_symbol.clone();
        if marked_notional_units == 0 {
            candidate.remove(&symbol_id);
        } else {
            candidate.insert(symbol_id, marked_notional_units);
        }
        let gross_inventory_units: i128 = candidate
            .values()
            .map(|value| i128::from(*value).abs())
            .sum();
        if !self.within_limit(gross_inventory_units, self.reserved_order_units()) {
            return ReservationDecision::rejected("portfolio_notional_limit");
        }
        self.inventory_by_symbol = candidate;
        self.debug_assert_invariants();
        ReservationDecision::accepted()
    }

    pub fn reserve(
        &mut self,
        order_id: u64,
        symbol_id: u32,
        is_bid: bool,
        notional_units: i64,
    ) -> ReservationDecision {
        if self.seen_order_ids.contains(&order_id) {
            return ReservationDecision::rejected("duplicate_order_id");
        }
        self.seen_order_ids.insert(order_id);
        if notional_units <= 0 {
            return ReservationDecision::rejected("invalid_notional");
        }
        if !self.within_limit(
            self.gross_inventory_units(),
            self.reserved_order_units() + i128::from(notional_units),
        ) {
            return ReservationDecision::rejected("portfolio_notional_limit");
        }
        self.orders.insert(
            order_id,
            PortfolioReservedOrder {
                symbol_id,
                is_bid,
                remaining_notional_units: notional_units,
                state: ReservationState::Live,
            },
        );
        self.debug_assert_invariants();
        ReservationDecision::accepted()
    }

    pub fn request_cancel(&mut self, order_id: u64) -> ReservationDecision {
        let Some(order) = self.orders.get_mut(&order_id) else {
            return ReservationDecision::rejected("unknown_order");
        };
        if order.state != ReservationState::Live {
            return ReservationDecision::rejected("order_not_live");
        }
        order.state = ReservationState::PendingCancel;
        self.debug_assert_invariants();
        ReservationDecision::accepted()
    }

    pub fn cancel_ack(&mut self, order_id: u64) -> ReservationDecision {
        let Some(order) = self.orders.get_mut(&order_id) else {
            return ReservationDecision::rejected("unknown_order");
        };
        if order.state != ReservationState::PendingCancel {
            return ReservationDecision::rejected("cancel_not_pending");
        }
        order.state = ReservationState::Cancelled;
        self.debug_assert_invariants();
        ReservationDecision::accepted()
    }

    pub fn fill(&mut self, order_id: u64, qty_notional_units: i64) -> ReservationDecision {
        let Some(order) = self.orders.get(&order_id) else {
            return ReservationDecision::rejected("unknown_order");
        };
        if !order.state.reserves() {
            return ReservationDecision::rejected("order_not_fillable");
        }
        if qty_notional_units <= 0 {
            return ReservationDecision::rejected("invalid_fill_quantity");
        }
        if qty_notional_units > order.remaining_notional_units {
            return ReservationDecision::rejected("fill_exceeds_remaining");
        }
        let symbol_id = order.symbol_id;
        let is_bid = order.is_bid;
        let previous_inventory = self
            .inventory_by_symbol
            .get(&symbol_id)
            .copied()
            .unwrap_or(0);
        let next_inventory = if is_bid {
            previous_inventory.checked_add(qty_notional_units)
        } else {
            previous_inventory.checked_sub(qty_notional_units)
        };
        let Some(next_inventory) = next_inventory else {
            return ReservationDecision::rejected("inventory_overflow");
        };
        let mut candidate = self.inventory_by_symbol.clone();
        if next_inventory == 0 {
            candidate.remove(&symbol_id);
        } else {
            candidate.insert(symbol_id, next_inventory);
        }
        let gross_inventory_units: i128 = candidate
            .values()
            .map(|value| i128::from(*value).abs())
            .sum();
        let reserved_order_units = self.reserved_order_units() - i128::from(qty_notional_units);
        if !self.within_limit(gross_inventory_units, reserved_order_units) {
            return ReservationDecision::rejected("portfolio_notional_limit");
        }
        let order = self
            .orders
            .get_mut(&order_id)
            .expect("validated order must exist");
        order.remaining_notional_units -= qty_notional_units;
        if order.remaining_notional_units == 0 {
            order.state = ReservationState::Filled;
        }
        self.inventory_by_symbol = candidate;
        self.debug_assert_invariants();
        ReservationDecision::accepted()
    }

    pub fn invalidate_epoch(&mut self) -> ReservationDecision {
        for order in self.orders.values_mut() {
            if order.state.reserves() {
                order.state = ReservationState::EpochInvalidated;
            }
        }
        self.debug_assert_invariants();
        ReservationDecision::accepted()
    }

    #[must_use]
    pub fn totals(&self) -> (i128, i128, i128) {
        let gross_inventory_units = self.gross_inventory_units();
        let reserved_order_units = self.reserved_order_units();
        (
            gross_inventory_units,
            reserved_order_units,
            gross_inventory_units + reserved_order_units,
        )
    }

    #[must_use]
    pub fn canonical_bytes(&self) -> Vec<u8> {
        let mut state = format!("max:{};", self.max_notional_units);
        for (symbol_id, inventory_units) in &self.inventory_by_symbol {
            state.push_str(&format!("inventory:{symbol_id}:{inventory_units};"));
        }
        state.push_str("seen:");
        for (index, order_id) in self.seen_order_ids.iter().enumerate() {
            if index > 0 {
                state.push(',');
            }
            state.push_str(&order_id.to_string());
        }
        state.push(';');
        for (order_id, order) in &self.orders {
            state.push_str(&format!(
                "order:{order_id}:{}:{}:{}:{};",
                order.symbol_id,
                if order.is_bid { "bid" } else { "ask" },
                order.remaining_notional_units,
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

const ACCOUNTING_CASH_SCALE: i128 = 1_000_000;

#[derive(Clone, Debug, Eq, PartialEq)]
struct AccountingPosition {
    lots: i128,
    cost_basis_cash_units: i128,
}

/// Fixed-point fill accounting and signed markout primitive.
///
/// Prices and quantities are integer ticks/lots. Cash is represented in
/// micro-units per tick-lot, and partial reversal cost is allocated with Rust's
/// integer division (toward zero), leaving the remainder with the open lot.
/// No floating point or venue-specific execution claim is made here.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct AccountingMarkoutLedger {
    positions: BTreeMap<u32, AccountingPosition>,
    marks: BTreeMap<u32, i64>,
    realized_pnl_cash_units: i128,
    total_fees_cash_units: i128,
    markout_cash_units: i128,
    markout_qty_lots: i128,
}

impl AccountingMarkoutLedger {
    fn cash_notional(price_tick: i64, qty_lots: i128) -> Option<i128> {
        i128::from(price_tick)
            .checked_mul(qty_lots)
            .and_then(|value| value.checked_mul(ACCOUNTING_CASH_SCALE))
    }

    pub fn mark(&mut self, symbol_id: u32, price_tick: i64) -> ReservationDecision {
        if price_tick <= 0 {
            return ReservationDecision::rejected("invalid_mark_price");
        }
        self.marks.insert(symbol_id, price_tick);
        ReservationDecision::accepted()
    }

    pub fn clear_mark(&mut self, symbol_id: u32) -> ReservationDecision {
        self.marks.remove(&symbol_id);
        ReservationDecision::accepted()
    }

    pub fn fill(
        &mut self,
        symbol_id: u32,
        is_bid: bool,
        price_tick: i64,
        qty_lots: i64,
        fee_cash_units: i64,
    ) -> ReservationDecision {
        if price_tick <= 0 {
            return ReservationDecision::rejected("invalid_fill_price");
        }
        if qty_lots <= 0 {
            return ReservationDecision::rejected("invalid_fill_quantity");
        }
        let qty = i128::from(qty_lots);
        if Self::cash_notional(price_tick, qty).is_none() {
            return ReservationDecision::rejected("notional_overflow");
        }
        let signed_qty = if is_bid { qty } else { -qty };
        let position = self
            .positions
            .entry(symbol_id)
            .or_insert(AccountingPosition {
                lots: 0,
                cost_basis_cash_units: 0,
            });
        if position.lots == 0 {
            position.lots = signed_qty;
            position.cost_basis_cash_units =
                Self::cash_notional(price_tick, qty).expect("validated notional");
        } else if position.lots.signum() == signed_qty.signum() {
            let Some(new_cost) = position
                .cost_basis_cash_units
                .checked_add(Self::cash_notional(price_tick, qty).expect("validated notional"))
            else {
                return ReservationDecision::rejected("cost_basis_overflow");
            };
            let Some(new_lots) = position.lots.checked_add(signed_qty) else {
                return ReservationDecision::rejected("position_overflow");
            };
            position.cost_basis_cash_units = new_cost;
            position.lots = new_lots;
        } else {
            let close_lots = position.lots.abs().min(qty);
            let allocated_cost = position.cost_basis_cash_units * close_lots / position.lots.abs();
            let fill_cash =
                Self::cash_notional(price_tick, close_lots).expect("validated notional");
            let realized_delta = if position.lots > 0 {
                fill_cash - allocated_cost
            } else {
                allocated_cost - fill_cash
            };
            self.realized_pnl_cash_units = self
                .realized_pnl_cash_units
                .checked_add(realized_delta)
                .expect("realized PnL overflow");
            position.cost_basis_cash_units -= allocated_cost;
            let remaining_position = position.lots.abs() - close_lots;
            let remaining_incoming = qty - close_lots;
            if remaining_position > 0 {
                position.lots = if position.lots > 0 {
                    remaining_position
                } else {
                    -remaining_position
                };
            } else if remaining_incoming > 0 {
                position.lots = if is_bid {
                    remaining_incoming
                } else {
                    -remaining_incoming
                };
                position.cost_basis_cash_units =
                    Self::cash_notional(price_tick, remaining_incoming)
                        .expect("validated notional");
            } else {
                position.lots = 0;
                position.cost_basis_cash_units = 0;
            }
        }
        self.total_fees_cash_units = self
            .total_fees_cash_units
            .checked_add(i128::from(fee_cash_units))
            .expect("fee overflow");
        if self
            .positions
            .get(&symbol_id)
            .is_some_and(|position| position.lots == 0)
        {
            self.positions.remove(&symbol_id);
        }
        ReservationDecision::accepted()
    }

    pub fn markout(
        &mut self,
        is_bid: bool,
        fill_price_tick: i64,
        qty_lots: i64,
        mark_price_tick: i64,
    ) -> ReservationDecision {
        if fill_price_tick <= 0 || mark_price_tick <= 0 {
            return ReservationDecision::rejected("invalid_markout_price");
        }
        if qty_lots <= 0 {
            return ReservationDecision::rejected("invalid_markout_quantity");
        }
        let delta = i128::from(mark_price_tick - fill_price_tick);
        let signed_delta = if is_bid { delta } else { -delta };
        let Some(markout_delta) = signed_delta
            .checked_mul(i128::from(qty_lots))
            .and_then(|value| value.checked_mul(ACCOUNTING_CASH_SCALE))
        else {
            return ReservationDecision::rejected("markout_overflow");
        };
        self.markout_cash_units = self
            .markout_cash_units
            .checked_add(markout_delta)
            .expect("markout overflow");
        self.markout_qty_lots += i128::from(qty_lots);
        ReservationDecision::accepted()
    }

    #[must_use]
    pub fn unrealized_pnl_cash_units(&self) -> Option<i128> {
        let mut unrealized = 0_i128;
        for (symbol_id, position) in &self.positions {
            if position.lots == 0 {
                continue;
            }
            let mark = *self.marks.get(symbol_id)?;
            let mark_value = Self::cash_notional(mark, position.lots.abs())?;
            let delta = if position.lots > 0 {
                mark_value - position.cost_basis_cash_units
            } else {
                position.cost_basis_cash_units - mark_value
            };
            unrealized = unrealized.checked_add(delta)?;
        }
        Some(unrealized)
    }

    #[must_use]
    pub fn totals(&self) -> (i128, i128, i128, i128, Option<i128>, bool) {
        let position_lots = self.positions.values().map(|position| position.lots).sum();
        let gross_position_lots = self
            .positions
            .values()
            .map(|position| position.lots.abs())
            .sum();
        let unrealized = self.unrealized_pnl_cash_units();
        (
            position_lots,
            gross_position_lots,
            self.realized_pnl_cash_units,
            self.total_fees_cash_units,
            unrealized,
            unrealized.is_some(),
        )
    }

    #[must_use]
    pub fn canonical_bytes(&self) -> Vec<u8> {
        let mut state = format!(
            "realized:{};fees:{};markout:{}:{};",
            self.realized_pnl_cash_units,
            self.total_fees_cash_units,
            self.markout_cash_units,
            self.markout_qty_lots
        );
        for (symbol_id, price_tick) in &self.marks {
            state.push_str(&format!("mark:{symbol_id}:{price_tick};"));
        }
        for (symbol_id, position) in &self.positions {
            state.push_str(&format!(
                "position:{symbol_id}:{}:{};",
                position.lots, position.cost_basis_cash_units
            ));
        }
        state.into_bytes()
    }

    #[must_use]
    pub fn state_sha256(&self) -> String {
        format!("{:x}", Sha256::digest(self.canonical_bytes()))
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

/// Run generated scheduler operations for independent Python/Rust parity.
///
/// Operations are `(kind, action_id, monotonic_ns, recv_seq, inclusive)`,
/// where kind `0` schedules, kind `1` drains at the supplied logical time,
/// and kind `2` cancels a pending action.
#[cfg(feature = "python")]
#[pyo3::pyfunction]
fn run_scheduler_trace(
    operations: Vec<PythonSchedulerOperation>,
    checkpoint_interval: usize,
) -> pyo3::PyResult<Vec<PythonSchedulerTraceRow>> {
    if checkpoint_interval == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "checkpoint_interval must be positive",
        ));
    }
    let operation_count = operations.len();
    let mut scheduler = DeterministicScheduler::default();
    let mut rows = Vec::with_capacity(operation_count);
    for (index, (kind, action_id, monotonic_ns, recv_seq, inclusive)) in
        operations.into_iter().enumerate()
    {
        let time = LogicalTime {
            recv_monotonic_ns: monotonic_ns,
            recv_seq,
        };
        let (accepted, reason, drained) = match kind {
            0 => match scheduler.schedule(action_id, time) {
                Ok(()) => (true, None, Vec::new()),
                Err(reason) => (false, Some(reason.to_owned()), Vec::new()),
            },
            1 => (true, None, scheduler.drain(time, inclusive)),
            2 => match scheduler.cancel(action_id) {
                Ok(()) => (true, None, Vec::new()),
                Err(reason) => (false, Some(reason.to_owned()), Vec::new()),
            },
            _ => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unsupported scheduler operation kind: {kind}"
                )))
            }
        };
        let ordinal = index + 1;
        let checkpoint = if ordinal % checkpoint_interval == 0 || ordinal == operation_count {
            Some(scheduler.state_sha256())
        } else {
            None
        };
        rows.push((
            accepted,
            reason,
            drained,
            scheduler.pending_count(),
            checkpoint,
        ));
    }
    Ok(rows)
}

/// Run per-symbol worst-case lot reservation operations for differential proof.
///
/// Operations are `(kind, order_id, is_bid, qty_lots)`, where kinds are
/// reserve, request-cancel, cancel-ack, fill and epoch-invalidate respectively.
#[cfg(feature = "python")]
#[pyo3::pyfunction]
fn run_risk_trace(
    operations: Vec<PythonRiskOperation>,
    max_position_lots: i64,
    checkpoint_interval: usize,
) -> pyo3::PyResult<Vec<PythonRiskTraceRow>> {
    if checkpoint_interval == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "checkpoint_interval must be positive",
        ));
    }
    let operation_count = operations.len();
    let mut ledger = RiskReservationLedger::new(max_position_lots)
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let mut rows = Vec::with_capacity(operation_count);
    for (index, (kind, order_id, is_bid, qty_lots)) in operations.into_iter().enumerate() {
        let decision = match kind {
            0 => ledger.reserve(order_id, is_bid, qty_lots),
            1 => ledger.request_cancel(order_id),
            2 => ledger.cancel_ack(order_id),
            3 => ledger.fill(order_id, qty_lots),
            4 => ledger.invalidate_epoch(),
            _ => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unsupported risk operation kind: {kind}"
                )))
            }
        };
        let (reserved_buy_lots, reserved_sell_lots) = ledger.reservation_totals();
        let ordinal = index + 1;
        let checkpoint = if ordinal % checkpoint_interval == 0 || ordinal == operation_count {
            Some(ledger.state_sha256())
        } else {
            None
        };
        rows.push((
            decision.accepted,
            decision.reason.map(str::to_owned),
            ledger.position_lots(),
            reserved_buy_lots,
            reserved_sell_lots,
            checkpoint,
        ));
    }
    Ok(rows)
}

/// Run conservative gross-notional reservation operations for differential proof.
///
/// Operations are `(kind, order_id, symbol_id, is_bid, notional_units)`, where
/// kinds are reserve, request-cancel, cancel-ack, fill, epoch-invalidate, and
/// set-inventory respectively. Inventory values are externally marked fixed-
/// point notional units; the ledger never infers marks from prices.
#[cfg(feature = "python")]
#[pyo3::pyfunction]
fn run_portfolio_notional_trace(
    operations: Vec<PythonPortfolioOperation>,
    max_notional_units: i64,
    checkpoint_interval: usize,
) -> pyo3::PyResult<Vec<PythonPortfolioTraceRow>> {
    if checkpoint_interval == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "checkpoint_interval must be positive",
        ));
    }
    let operation_count = operations.len();
    let mut ledger = PortfolioNotionalReservationLedger::new(max_notional_units)
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let mut rows = Vec::with_capacity(operation_count);
    for (index, (kind, order_id, symbol_id, is_bid, notional_units)) in
        operations.into_iter().enumerate()
    {
        let decision = match kind {
            0 => ledger.reserve(order_id, symbol_id, is_bid, notional_units),
            1 => ledger.request_cancel(order_id),
            2 => ledger.cancel_ack(order_id),
            3 => ledger.fill(order_id, notional_units),
            4 => ledger.invalidate_epoch(),
            5 => ledger.set_inventory(symbol_id, notional_units),
            _ => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unsupported portfolio operation kind: {kind}"
                )))
            }
        };
        let (gross_inventory_units, reserved_order_units, total_reserved_units) = ledger.totals();
        let ordinal = index + 1;
        let checkpoint = if ordinal % checkpoint_interval == 0 || ordinal == operation_count {
            Some(ledger.state_sha256())
        } else {
            None
        };
        rows.push((
            decision.accepted,
            decision.reason.map(str::to_owned),
            gross_inventory_units,
            reserved_order_units,
            total_reserved_units,
            checkpoint,
        ));
    }
    Ok(rows)
}

/// Run fixed-point fill accounting and signed markout operations.
///
/// Operations are `(kind, symbol_id, is_bid, price_or_fill_tick, qty_lots,
/// fee_or_mark_tick)`: kind 0 is fill (the final field is a signed fee in cash
/// units), kind 1 is mark (price in the fourth field), kind 2 clears a mark,
/// and kind 3 is markout (the final field is the mark tick).
#[cfg(feature = "python")]
#[pyo3::pyfunction]
fn run_accounting_trace(
    operations: Vec<PythonAccountingOperation>,
    checkpoint_interval: usize,
) -> pyo3::PyResult<Vec<PythonAccountingTraceRow>> {
    if checkpoint_interval == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "checkpoint_interval must be positive",
        ));
    }
    let operation_count = operations.len();
    let mut ledger = AccountingMarkoutLedger::default();
    let mut rows = Vec::with_capacity(operation_count);
    for (index, (kind, symbol_id, is_bid, price_tick, qty_lots, fee_or_mark_tick)) in
        operations.into_iter().enumerate()
    {
        let decision = match kind {
            0 => ledger.fill(symbol_id, is_bid, price_tick, qty_lots, fee_or_mark_tick),
            1 => ledger.mark(symbol_id, price_tick),
            2 => ledger.clear_mark(symbol_id),
            3 => ledger.markout(is_bid, price_tick, qty_lots, fee_or_mark_tick),
            _ => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unsupported accounting operation kind: {kind}"
                )))
            }
        };
        let (
            position_lots,
            gross_position_lots,
            realized_pnl,
            fees,
            unrealized_pnl,
            valuation_complete,
        ) = ledger.totals();
        let ordinal = index + 1;
        let checkpoint = if ordinal % checkpoint_interval == 0 || ordinal == operation_count {
            Some(ledger.state_sha256())
        } else {
            None
        };
        rows.push((
            decision.accepted,
            decision.reason.map(str::to_owned),
            position_lots,
            gross_position_lots,
            realized_pnl,
            fees,
            unrealized_pnl,
            valuation_complete,
            ledger.markout_cash_units,
            ledger.markout_qty_lots,
            checkpoint,
        ));
    }
    Ok(rows)
}

/// Run the language-neutral SplitMix64 scenario latency sampler.
///
/// ``mode`` is 0 for fixed, 1 for empirical, and 2 for stress-tail.  Each
/// component is 0 for a new order and 1 for a cancel.  Rows contain the
/// integer-microsecond draw and the sampler state after that operation.
#[cfg(feature = "python")]
#[pyo3::pyfunction]
fn run_latency_trace(
    mode: u8,
    fixed_new_us: i64,
    fixed_cancel_us: i64,
    samples_us: Vec<i64>,
    stress_multiplier_ppm: i64,
    seed: u64,
    components: Vec<u8>,
) -> pyo3::PyResult<Vec<PythonLatencyTraceRow>> {
    let mut sampler = ScenarioLatencySampler::new(
        mode,
        fixed_new_us,
        fixed_cancel_us,
        samples_us,
        stress_multiplier_ppm,
        seed,
    )
    .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let mut rows = Vec::with_capacity(components.len());
    for component in components {
        let draw = sampler
            .draw(component)
            .map_err(pyo3::exceptions::PyValueError::new_err)?;
        rows.push((draw, sampler.state()));
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
    m.add_function(pyo3::wrap_pyfunction!(run_scheduler_trace, m)?)?;
    m.add_function(pyo3::wrap_pyfunction!(run_risk_trace, m)?)?;
    m.add_function(pyo3::wrap_pyfunction!(run_portfolio_notional_trace, m)?)?;
    m.add_function(pyo3::wrap_pyfunction!(run_accounting_trace, m)?)?;
    m.add_function(pyo3::wrap_pyfunction!(run_latency_trace, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;

    use super::{
        AccountingMarkoutLedger, BookState, DeterministicScheduler, LogicalTime, OrderState,
        PortfolioNotionalReservationLedger, RiskReservationLedger, ScenarioLatencySampler, Side,
        SyntheticExchange, ACCOUNTING_CASH_SCALE, LATENCY_STRESS_SCALE,
    };

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
    fn scenario_latency_sampler_is_reproducible_and_checkpointable() {
        let components = [0, 1, 0, 1, 1, 0];
        let mut first =
            ScenarioLatencySampler::new(1, 10, 20, vec![1, 5, 25], LATENCY_STRESS_SCALE, 17)
                .expect("valid sampler");
        let mut second = first.clone();
        let first_draws: Vec<_> = components
            .iter()
            .map(|component| first.draw(*component).expect("draw"))
            .collect();
        let second_draws: Vec<_> = components
            .iter()
            .map(|component| second.draw(*component).expect("draw"))
            .collect();
        assert_eq!(first_draws, second_draws);
        assert_eq!(first.state(), second.state());
        let checkpoint = first.state();
        let next = first.draw(0).expect("draw after checkpoint");
        let mut resumed = second;
        resumed.state = checkpoint;
        assert_eq!(resumed.draw(0).expect("resumed draw"), next);
    }

    #[test]
    fn scenario_latency_stress_tail_is_not_random() {
        let mut sampler =
            ScenarioLatencySampler::new(2, 0, 0, vec![1_000, 5_000], 3 * LATENCY_STRESS_SCALE, 17)
                .expect("valid sampler");
        assert_eq!(sampler.draw(0).expect("stress draw"), 15_000);
        assert_eq!(sampler.state(), 17);
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

    #[test]
    fn scheduler_separates_strict_and_inclusive_ties() {
        let mut scheduler = DeterministicScheduler::default();
        let due = LogicalTime {
            recv_monotonic_ns: 100,
            recv_seq: 5,
        };
        scheduler.schedule(1, due).expect("schedule one");
        scheduler.schedule(2, due).expect("schedule two");
        assert!(scheduler.drain(due, false).is_empty());
        assert_eq!(scheduler.drain(due, true), vec![1, 2]);
        assert_eq!(scheduler.schedule(1, due), Err("duplicate_action_id"));
    }

    #[test]
    fn pending_cancel_reservation_remains_fillable_until_ack() {
        let mut ledger = RiskReservationLedger::new(10).expect("valid limit");
        assert!(ledger.reserve(1, true, 7).accepted);
        assert!(ledger.request_cancel(1).accepted);
        assert_eq!(ledger.reservation_totals(), (7, 0));
        assert_eq!(ledger.reserve(2, true, 4).reason, Some("long_limit"));
        assert!(ledger.fill(1, 3).accepted);
        assert_eq!(ledger.position_lots(), 3);
        assert_eq!(ledger.reservation_totals(), (4, 0));
        assert!(ledger.cancel_ack(1).accepted);
        assert_eq!(ledger.reservation_totals(), (0, 0));
        assert!(ledger.reserve(3, true, 7).accepted);
    }

    #[test]
    fn reservation_totals_do_not_overflow_i64_at_extreme_valid_limits() {
        let limit = i64::MAX;
        let mut ledger = RiskReservationLedger::new(limit).expect("valid limit");
        assert!(ledger.reserve(1, false, limit).accepted);
        assert!(ledger.fill(1, limit).accepted);
        assert!(ledger.reserve(2, true, limit).accepted);
        assert!(ledger.reserve(3, true, limit).accepted);
        assert_eq!(ledger.reservation_totals(), (i128::from(limit) * 2, 0));
    }

    #[test]
    fn portfolio_notional_reservation_is_gross_across_symbols() {
        let mut ledger = PortfolioNotionalReservationLedger::new(100).expect("valid limit");
        assert!(ledger.set_inventory(1, 30).accepted);
        assert!(ledger.reserve(1, 2, true, 40).accepted);
        assert_eq!(ledger.totals(), (30, 40, 70));
        assert!(ledger.request_cancel(1).accepted);
        assert_eq!(
            ledger.reserve(2, 3, false, 31).reason,
            Some("portfolio_notional_limit")
        );
        assert!(ledger.fill(1, 20).accepted);
        assert_eq!(ledger.totals(), (50, 20, 70));
        assert!(ledger.cancel_ack(1).accepted);
        assert_eq!(ledger.totals(), (50, 0, 50));
        assert!(ledger.reserve(3, 3, false, 50).accepted);
        assert_eq!(ledger.totals(), (50, 50, 100));
    }

    #[test]
    fn portfolio_notional_invalid_transitions_are_atomic() {
        let mut ledger = PortfolioNotionalReservationLedger::new(10).expect("valid limit");
        assert!(ledger.set_inventory(7, -4).accepted);
        assert!(ledger.reserve(1, 7, true, 6).accepted);
        let before = ledger.state_sha256();
        assert_eq!(ledger.fill(1, 7).reason, Some("fill_exceeds_remaining"));
        assert_eq!(ledger.state_sha256(), before);
        assert_eq!(
            ledger.set_inventory(8, 5).reason,
            Some("portfolio_notional_limit")
        );
        assert!(ledger.invalidate_epoch().accepted);
        assert_eq!(ledger.totals(), (4, 0, 4));
    }

    #[test]
    fn accounting_handles_reversals_fees_marks_and_markouts() {
        let mut accounting = AccountingMarkoutLedger::default();
        assert!(accounting.fill(1, true, 100, 3, 7).accepted);
        assert!(accounting.fill(1, false, 110, 1, -2).accepted);
        assert_eq!(accounting.totals().2, 10 * ACCOUNTING_CASH_SCALE);
        assert_eq!(accounting.totals().3, 5);
        assert!(accounting.totals().4.is_none());
        assert!(accounting.mark(1, 105).accepted);
        assert_eq!(accounting.totals().4, Some(10 * ACCOUNTING_CASH_SCALE));
        assert!(accounting.markout(true, 100, 2, 98).accepted);
        assert_eq!(accounting.markout_cash_units, -4 * ACCOUNTING_CASH_SCALE);
    }

    #[test]
    fn accounting_invalid_fill_is_atomic() {
        let mut accounting = AccountingMarkoutLedger::default();
        let before = accounting.state_sha256();
        assert_eq!(
            accounting.fill(1, true, 0, 1, 0).reason,
            Some("invalid_fill_price")
        );
        assert_eq!(accounting.state_sha256(), before);
    }
}
