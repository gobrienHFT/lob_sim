"""Native observation-only inference, independent audit stream and bounded state.

The feature dataset and runtime use the same authoritative observation adapter.
No strategy, mutable book, fill model, event ID counter or RNG is accessible here.
"""

from __future__ import annotations

import math
import csv
from copy import deepcopy
from collections import Counter
from collections.abc import Generator, Mapping
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from ..sim.checkpoint import decode, encode
from ..sim.observation import MarketObservation
from ..sim.sinks import EventSink, NullSink
from .dataset import FeatureDatasetObserver, _Context, instrument_identity
from .diagnostics import RegimeDiagnostics
from .features import CausalFeatureSampler, FeatureSample, FeatureStatus
from .filter import FilterResult
from .runtime import CausalRegimeEstimator
from .settings import HMMSettings
from .validation import canonical_json, finite, identity, integer, probabilities, require_keys, strict_json

TRACE_FIELDS = (
    "schema_version",
    "event_type",
    "symbol",
    "sample_ns",
    "available_at_ns",
    "receive_seq",
    "input_row",
    "wall_ns",
    "utc_day",
    "clock_basis",
    "epochs",
    "validity",
    "model_sha256",
    "feature_identity",
    "instrument_sha256",
    "input_sha256",
    "status",
    "features",
    "scaled_features",
    "posterior",
    "next_prior",
    "raw_map_state",
    "semantic_state",
    "active_state",
    "confidence",
    "entropy",
    "normalized_entropy",
    "candidate_state",
    "confirmation_streak",
    "state_age_samples",
    "state_switched",
    "state_switch_reason",
    "confident",
    "feature_reset_reason",
    "filter_reset_reason",
)
CHAIN_DOMAIN = b"lob_sim.hmm_regime_trace.v1"


def advance_trace_digest(previous: str, row: Mapping[str, Any]) -> str:
    return sha256(bytes.fromhex(previous) + canonical_json(dict(row)).encode("utf-8")).hexdigest()


class RegimeObserver(FeatureDatasetObserver):
    """One frozen symbol/model. History is emitted, not retained."""

    def __init__(self, settings: HMMSettings, sink: EventSink | None = None) -> None:
        self.settings = settings
        self._model_sha256 = settings.model.model_sha256
        self.sink = sink if sink is not None else NullSink()
        self.estimator = CausalRegimeEstimator(settings.model, settings.hysteresis)
        super().__init__(settings.model.features, "", self._receive_sample, symbols=(settings.symbol,))
        metadata = strict_json(settings.model.provenance_json)
        training = metadata.get("training", {})
        self._expected_instrument = training.get("instrument_sha256") if isinstance(training, dict) else None
        self._labels = tuple(f"STATE_{index}" for index in range(settings.model.parameters.state_count))
        self._latest: dict[str, Any] | None = None
        self._immediate_status = "WARMING_UP"
        self._immediate_reason = "initialization"
        self._trace_count = 0
        self._trace_sha256 = sha256(CHAIN_DOMAIN).hexdigest()
        self._state_counts = [0] * settings.model.parameters.state_count
        self._transition_counts = [[0] * settings.model.parameters.state_count for _ in self._state_counts]
        self._previous_active: int | None = None
        self._entropy_sum = 0.0
        self._uncertain_count = 0
        self._switch_count = 0
        self.diagnostics = RegimeDiagnostics(settings.model.parameters.state_count, self.spec.interval_ns)

    def bind_input(self, input_sha256: str) -> None:
        if len(input_sha256) != 64 or any(char not in "0123456789abcdef" for char in input_sha256):
            raise ValueError("regime observer input must have SHA-256 identity")
        if self.input_sha256 and self.input_sha256 != input_sha256:
            raise ValueError("regime observer input identity mismatch")
        self.input_sha256 = input_sha256

    def _write(self, row: dict[str, Any]) -> None:
        # Sink failure stops the run; no count/hash claims for an unaccepted row.
        row = {key: row.get(key) for key in TRACE_FIELDS}
        next_digest = advance_trace_digest(self._trace_sha256, row)
        # Only the exact built-in no-op may skip the discarded defensive copy.
        # Subclasses/custom sinks still receive isolated rows and can fail.
        if type(self.sink) is not NullSink:
            self.sink.write(deepcopy(row))
        self._trace_sha256 = next_digest
        self._trace_count += 1

    def _receive_sample(self, row: Mapping[str, Any]) -> None:
        sample = FeatureSample(
            row["symbol"],
            row["sample_ns"],
            row["receive_seq"],
            tuple(row["epochs"]),
            row["feature_identity"],
            cast(FeatureStatus, row["status"]),
            tuple(row["features"]) if row["features"] is not None else None,
            row["reset_reason"],
        )
        signal = self.estimator.update(sample)
        output = signal.as_dict()
        output.update(
            {
                key: row[key]
                for key in (
                    "available_at_ns",
                    "input_row",
                    "wall_ns",
                    "utc_day",
                    "clock_basis",
                    "validity",
                    "instrument_sha256",
                    "input_sha256",
                )
            }
        )
        raw = output["raw_map_state"]
        output["semantic_state"] = self._labels[raw] if raw is not None else None
        output["event_type"] = "sample"
        self._write(output)
        self.diagnostics.observe(output)
        self._latest = output
        self._immediate_status = sample.status
        self._immediate_reason = sample.status if sample.status != "VALID" else "valid_sample"
        if raw is not None:
            self._state_counts[raw] += 1
            self._entropy_sum += signal.entropy or 0.0
            self._uncertain_count += int(not output["confident"])
            self._switch_count += int(output["state_switched"])
            active = output["active_state"]
            if signal.filter_reset_reason is not None:
                self._previous_active = None
            if active is not None and self._previous_active is not None:
                self._transition_counts[self._previous_active][active] += 1
            self._previous_active = active
        else:
            self._previous_active = None

    def observe(self, observation: MarketObservation) -> None:
        if observation.symbol != self.settings.symbol:
            return
        instrument = instrument_identity(observation)
        if self._expected_instrument is not None and instrument != self._expected_instrument:
            raise ValueError("regime model instrument grid mismatch")
        context = self._contexts.get(observation.symbol)
        changed = context is not None and (
            context.instrument_sha256 != instrument or context.observation.epochs != observation.epochs
        )
        super().observe(observation)
        status = self._contexts[observation.symbol].sampler.status
        if (
            changed
            or status not in {"VALID", "WARMING_UP"}
            or (status == "WARMING_UP" and self._immediate_status == "VALID")
        ):
            reason = "epoch_or_instrument_changed" if changed else status
            if (
                changed
                or (self._latest is not None and self._latest["status"] == "VALID")
                or (status, reason) != (self._immediate_status, self._immediate_reason)
            ):
                self.estimator.invalidate(reason)
                self._latest = None
                self._previous_active = None
                self._write(
                    {
                        "schema_version": "lob_sim.hmm_regime_invalidation.v1",
                        "event_type": "invalidation",
                        "symbol": observation.symbol,
                        "available_at_ns": observation.logical_ns,
                        "receive_seq": observation.receive_seq,
                        "input_row": observation.input_row,
                        "epochs": list(observation.epochs),
                        "validity": observation.validity.as_dict(),
                        "model_sha256": self._model_sha256,
                        "feature_identity": self.spec.digest,
                        "instrument_sha256": instrument,
                        "input_sha256": self.input_sha256,
                        "status": status,
                        "filter_reset_reason": reason,
                    }
                )
                self.diagnostics.invalidate()
            self._immediate_status, self._immediate_reason = status, reason

    def snapshot(self, logical_ns: int) -> dict[str, Any]:
        """Diagnostic information available at this decision, never a future row."""
        integer(logical_ns, "regime observation time")
        latest = self._latest
        context = self._contexts.get(self.settings.symbol)
        stale = context is not None and (
            context.sampler._last_book_ns is None
            or logical_ns - context.sampler._last_book_ns > self.spec.stale_after_ns
        )
        if latest is not None and latest["available_at_ns"] <= logical_ns and latest["status"] == "VALID" and not stale:
            # Deep copy prevents a trace consumer from mutating runtime state.
            return dict(strict_json(canonical_json(latest)))
        return {
            "model_sha256": self._model_sha256,
            "status": "STALE"
            if stale and self._immediate_status == "VALID"
            else "WARMING_UP"
            if latest is not None and latest["available_at_ns"] > logical_ns
            else self._immediate_status,
            "posterior": None,
            "active_state": None,
            "raw_map_state": None,
            "confidence": None,
            "entropy": None,
            "normalized_entropy": None,
            "confident": False,
            "filter_reset_reason": self._immediate_reason,
        }

    def summary(self) -> dict[str, Any]:
        valid = sum(self._state_counts)
        return {
            "schema_version": "lob_sim.hmm_observation_summary.v1",
            "config": self.settings.as_dict(),
            "input_sha256": self.input_sha256,
            "trace_count": self._trace_count,
            "trace_sha256": self._trace_sha256,
            "sample_count": self.sample_count,
            "status_counts": dict(sorted(self.status_counts.items())),
            "raw_map_sample_counts": self._state_counts[:],
            "active_state_sample_transitions": [row[:] for row in self._transition_counts],
            "mean_valid_sample_entropy": self._entropy_sum / valid if valid else None,
            "uncertain_valid_samples": self._uncertain_count,
            "confirmed_switches": self._switch_count,
            "state_diagnostics": self.diagnostics.summary(self.sample_count),
            "retained_samples": int(self._latest is not None),
            "retained_window_bins": sum(context.sampler.retained_bins for context in self._contexts.values()),
            "memory_bounded_by_tape_duration": self.sink.memory_bounded,
            "strategy_intervention": self.settings.mode == "policy",
            "claim_ready": False,
            "claim_reason": "unregistered regime diagnostics or policy runs are not out-of-sample execution evidence",
        }

    def checkpoint(self) -> dict[str, Any]:
        contexts = []
        for symbol, context in sorted(self._contexts.items()):
            contexts.append(
                {
                    "symbol": symbol,
                    "sampler": context.sampler.checkpoint(),
                    # Last trade/change objects have already entered the bounded bin.
                    "observation": encode(replace(context.observation, trade=None, changes=())),
                    "instrument_sha256": context.instrument_sha256,
                    "generation": context.generation,
                    "previous_sequence_key": encode(context.previous_sequence_key),
                    "sequence_id": context.sequence_id,
                    "last_valid_ns": context.last_valid_ns,
                }
            )
        return {
            "schema_version": "lob_sim.hmm_observer_checkpoint.v2",
            "config_sha256": identity(self.settings.as_dict()),
            "input_sha256": self.input_sha256,
            "contexts": contexts,
            "estimator": self.estimator.checkpoint(),
            "latest": deepcopy(self._latest),
            "immediate_status": self._immediate_status,
            "immediate_reason": self._immediate_reason,
            "trace_count": self._trace_count,
            "trace_sha256": self._trace_sha256,
            "sample_count": self.sample_count,
            "status_counts": dict(self.status_counts),
            "state_counts": self._state_counts[:],
            "transition_counts": [row[:] for row in self._transition_counts],
            "previous_active": self._previous_active,
            "entropy_sum": self._entropy_sum,
            "uncertain_count": self._uncertain_count,
            "switch_count": self._switch_count,
            "diagnostics": self.diagnostics.checkpoint(),
        }

    def validated_copy(self, checkpoint: object) -> RegimeObserver:
        """Return a validated independent candidate; no callbacks during restore."""
        data = require_keys(checkpoint, set(self.checkpoint()), "observer checkpoint")
        if data["schema_version"] != "lob_sim.hmm_observer_checkpoint.v2" or data["config_sha256"] != identity(
            self.settings.as_dict()
        ):
            raise ValueError("regime observer checkpoint configuration mismatch")
        candidate = RegimeObserver(self.settings, self.sink)
        candidate.bind_input(data["input_sha256"])
        if self.input_sha256 and candidate.input_sha256 != self.input_sha256:
            raise ValueError("regime observer checkpoint input mismatch")
        candidate.estimator.restore(data["estimator"])
        contexts = data["contexts"]
        if not isinstance(contexts, list) or len(contexts) > 1:
            raise ValueError("observer checkpoint exceeds symbol bound")
        for raw in contexts:
            item = require_keys(
                raw,
                {
                    "symbol",
                    "sampler",
                    "observation",
                    "instrument_sha256",
                    "generation",
                    "previous_sequence_key",
                    "sequence_id",
                    "last_valid_ns",
                },
                "observer context",
            )
            if item["symbol"] != self.settings.symbol:
                raise ValueError("observer checkpoint symbol mismatch")
            sampler = CausalFeatureSampler(self.settings.symbol, self.spec)
            sampler.restore(item["sampler"])
            observation = decode(item["observation"])
            if not isinstance(observation, MarketObservation) or observation.symbol != self.settings.symbol:
                raise ValueError("observer checkpoint observation mismatch")
            for key in ("logical_ns", "receive_seq", "input_row", "wall_ns"):
                integer(getattr(observation, key), key)
            if (
                observation.logical_ns != sampler._last_observation_ns
                or observation.input_row != sampler._last_receive_seq
                or observation.epochs != sampler._validity.epochs
                or observation.bids != (sampler._book.bids if sampler._book else ())
                or observation.asks != (sampler._book.asks if sampler._book else ())
                or item["instrument_sha256"] != instrument_identity(observation)
                or (self._expected_instrument is not None and item["instrument_sha256"] != self._expected_instrument)
            ):
                raise ValueError("observer checkpoint causal anchors mismatch")
            generation = integer(item["generation"], "generation")
            last_valid = item["last_valid_ns"]
            if last_valid is not None:
                integer(last_valid, "last_valid_ns")
                if last_valid > observation.logical_ns:
                    raise ValueError("observer valid sample exceeds observation")
            candidate._contexts[item["symbol"]] = _Context(
                sampler,
                observation,
                item["instrument_sha256"],
                generation,
                decode(item["previous_sequence_key"]),
                item["sequence_id"],
                last_valid,
            )
        for key in ("trace_count", "sample_count", "uncertain_count", "switch_count"):
            setattr(candidate, "_" + key if key not in {"sample_count"} else key, integer(data[key], key))
        counts = require_keys(data["status_counts"], set(data["status_counts"]), "observer status counts")
        for status, count in counts.items():
            FeatureSample(
                self.settings.symbol,
                0,
                0,
                (0, 0, 0),
                self.spec.digest,
                cast(FeatureStatus, status),
                (0.0,) * 12 if status == "VALID" else None,
                None,
            )
            integer(count, "status count")
        candidate.status_counts = Counter(counts)
        k = self.settings.model.parameters.state_count
        states, transitions = data["state_counts"], data["transition_counts"]
        if (
            not isinstance(states, list)
            or len(states) != k
            or not isinstance(transitions, list)
            or len(transitions) != k
        ):
            raise ValueError("observer statistics dimension mismatch")
        for row in [states, *transitions]:
            if not isinstance(row, list) or len(row) != k:
                raise ValueError("observer transition dimension mismatch")
            for value in row:
                integer(value, "state count")
        valid = sum(states)
        if (
            sum(counts.values()) != candidate.sample_count
            or valid != counts.get("VALID", 0)
            or candidate._trace_count < candidate.sample_count
            or candidate._uncertain_count > valid
            or candidate._switch_count > valid
            or sum(sum(row) for row in transitions) > valid
        ):
            raise ValueError("inconsistent observer statistics")
        candidate._state_counts = states[:]
        candidate._transition_counts = [row[:] for row in transitions]
        entropy = finite(data["entropy_sum"], "entropy_sum")
        if entropy < 0 or entropy > valid * math.log(k) + 1e-8:
            raise ValueError("inconsistent observer entropy")
        candidate._entropy_sum = entropy
        previous = data["previous_active"]
        if previous is not None and integer(previous, "previous_active") >= k:
            raise ValueError("observer previous state out of range")
        candidate._previous_active = previous
        candidate.diagnostics = self.diagnostics.validated_copy(data["diagnostics"])
        diagnostic_state = candidate.diagnostics.checkpoint()
        if (
            diagnostic_state["valid_samples"] != valid
            or diagnostic_state["last_sample_ns"] != candidate.estimator._last_sample_ns
            or [diagnostic_state["cells"]["raw_map"][label]["samples"] for label in self._labels] != states
        ):
            raise ValueError("observer diagnostic sample totals/anchors mismatch")
        trace_sha = data["trace_sha256"]
        if (
            not isinstance(trace_sha, str)
            or len(trace_sha) != 64
            or any(c not in "0123456789abcdef" for c in trace_sha)
        ):
            raise ValueError("invalid observer trace digest")
        candidate._trace_sha256 = trace_sha
        status, reason = data["immediate_status"], data["immediate_reason"]
        FeatureSample(
            self.settings.symbol,
            0,
            0,
            (0, 0, 0),
            self.spec.digest,
            status,
            (0.0,) * 12 if status == "VALID" else None,
            None,
        )
        if not isinstance(reason, str) or not reason:
            raise ValueError("invalid observer status reason")
        candidate._immediate_status, candidate._immediate_reason = status, reason
        latest = data["latest"]
        if latest is not None:
            row = require_keys(latest, set(TRACE_FIELDS), "observer latest signal")
            if (
                row["event_type"] != "sample"
                or row["model_sha256"] != self.settings.model.model_sha256
                or row["feature_identity"] != self.spec.digest
                or row["input_sha256"] != candidate.input_sha256
                or row["symbol"] != self.settings.symbol
                or row["status"] != status
                or row["sample_ns"] != candidate.estimator._last_sample_ns
                or row["receive_seq"] != candidate.estimator._last_receive_seq
            ):
                raise ValueError("observer latest signal identity mismatch")
            integer(row["available_at_ns"], "available_at_ns")
            if row["available_at_ns"] < row["sample_ns"]:
                raise ValueError("observer latest signal is available before sampling")
            sample = FeatureSample(
                row["symbol"],
                row["sample_ns"],
                row["receive_seq"],
                tuple(row["epochs"]),
                self.spec.digest,
                row["status"],
                tuple(row["features"]) if row["features"] is not None else None,
                row["feature_reset_reason"],
            )
            if sample.values is not None:
                p = probabilities(row["posterior"], "latest posterior", k)
                if (
                    p != candidate.estimator.filter.posterior
                    or list(
                        self.settings.model.scaler.transform(
                            sample.values,
                            feature_names=self.settings.model.scaler.feature_names,
                            feature_identity=self.spec.digest,
                        )
                    )
                    != row["scaled_features"]
                ):
                    raise ValueError("observer latest signal/filter mismatch")
                next_prior = tuple(
                    math.fsum(
                        p[left] * transition[right]
                        for left, transition in enumerate(self.settings.model.parameters.transition)
                    )
                    for right in range(k)
                )
                result = FilterResult(p, next_prior, 0.0, candidate.estimator.filter.samples_seen)
                hysteresis = candidate.estimator.hysteresis
                confident = (
                    result.confidence >= self.settings.hysteresis.enter_probability
                    and result.normalized_entropy <= self.settings.hysteresis.maximum_normalized_entropy
                )
                expected = {
                    "next_prior": list(next_prior),
                    "raw_map_state": result.map_state,
                    "semantic_state": self._labels[result.map_state],
                    "confidence": result.confidence,
                    "entropy": result.entropy,
                    "normalized_entropy": result.normalized_entropy,
                    "active_state": hysteresis.active_state,
                    "candidate_state": hysteresis.candidate_state,
                    "confirmation_streak": hysteresis.confirmation_streak,
                    "state_age_samples": hysteresis.state_age_samples,
                    "confident": confident,
                }
                if any(row[key] != value or type(row[key]) is not type(value) for key, value in expected.items()):
                    raise ValueError("observer latest hysteresis mismatch")
            elif (
                row["posterior"] is not None
                or row["active_state"] is not None
                or candidate.estimator.filter.samples_seen
            ):
                raise ValueError("invalid observer retains confidence")
            candidate._latest = dict(strict_json(canonical_json(row)))
        elif candidate.estimator.filter.samples_seen:
            raise ValueError("observer filter has no current signal")
        opened = diagnostic_state["open"]
        valid_latest = latest is not None and latest["status"] == "VALID"
        if valid_latest != (opened["raw_map"] is not None):
            raise ValueError("observer diagnostic validity mismatch")
        if valid_latest:
            if opened["raw_map"]["label"] != f"STATE_{latest['raw_map_state']}" or opened["active"]["label"] != (
                f"STATE_{latest['active_state']}" if latest["active_state"] is not None else "UNCONFIRMED"
            ):
                raise ValueError("observer diagnostic current labels mismatch")
        return candidate

    def restore(self, checkpoint: object) -> None:
        candidate = self.validated_copy(checkpoint)
        self.__dict__.update(candidate.__dict__)
        # The inherited emitter must be bound to this instance, not the clone.
        self.emit = self._receive_sample


def iter_trace_rows(path: Path) -> Generator[dict[str, Any], None, None]:
    """Strict bounded row decoding shared by state and cross-stream audits."""
    integer_fields = {
        "sample_ns",
        "available_at_ns",
        "receive_seq",
        "input_row",
        "wall_ns",
        "raw_map_state",
        "active_state",
        "candidate_state",
        "confirmation_streak",
        "state_age_samples",
    }
    float_fields = {"confidence", "entropy", "normalized_entropy"}
    bool_fields = {"state_switched", "confident"}
    json_fields = {"epochs", "validity", "features", "scaled_features", "posterior", "next_prior"}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(TRACE_FIELDS):
            raise ValueError("regime trace field contract mismatch")
        for raw in reader:
            if set(raw) != set(TRACE_FIELDS) or any(value is None for value in raw.values()):
                raise ValueError("malformed regime trace row")
            row: dict[str, Any] = {}
            for key, value in raw.items():
                if value == "":
                    row[key] = None
                elif key in integer_fields:
                    row[key] = integer(int(value), key)
                elif key in float_fields:
                    row[key] = finite(float(value), key)
                elif key in bool_fields:
                    if value not in {"True", "False"}:
                        raise ValueError("invalid regime trace boolean")
                    row[key] = value == "True"
                elif key in json_fields:
                    row[key] = strict_json(value)
                else:
                    row[key] = value
            yield row


def verify_trace(path: Path, summary: Mapping[str, Any]) -> None:
    """Stream serialized rows back through the same canonical audit hash."""
    count, digest = 0, sha256(CHAIN_DOMAIN).hexdigest()
    sample_count = 0
    statuses: Counter[str] = Counter()
    diagnostics = RegimeDiagnostics(
        len(summary["raw_map_sample_counts"]), summary["state_diagnostics"]["sampling_interval_ns"]
    )
    for row in iter_trace_rows(path):
        digest = advance_trace_digest(digest, row)
        diagnostics.observe(row)
        if row["event_type"] == "sample":
            sample_count += 1
            statuses[row["status"]] += 1
        count += 1
    if count != summary["trace_count"] or digest != summary["trace_sha256"]:
        raise ValueError("serialized regime audit count/hash mismatch")
    if diagnostics.summary(summary["sample_count"]) != summary["state_diagnostics"]:
        raise ValueError("serialized regime state diagnostics mismatch")
    reduced = diagnostics.summary(sample_count)
    if (
        sample_count != summary["sample_count"]
        or dict(statuses) != summary["status_counts"]
        or [reduced["conditioned"]["raw_map"][f"STATE_{i}"]["samples"] for i in range(diagnostics.state_count)]
        != summary["raw_map_sample_counts"]
        or [
            [
                reduced["sample_transitions"]["active"][f"STATE_{i}"][f"STATE_{j}"]
                for j in range(diagnostics.state_count)
            ]
            for i in range(diagnostics.state_count)
        ]
        != summary["active_state_sample_transitions"]
    ):
        raise ValueError("serialized regime summary counters mismatch")
