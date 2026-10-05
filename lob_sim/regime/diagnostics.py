"""Bounded descriptive reduction of causal regime audit rows.

Sample-grid occupancy is not joint-valid wall-clock coverage. An episode is
complete only when both of its state-change boundaries were observed; gaps,
resets, the initial edge and the still-open tail are censored, never interpolated.
No fitting, execution outcomes or strategy inputs are accessed here.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .features import FEATURE_NAMES
from .validation import finite, integer, require_keys, vector

BASES = ("raw_map", "active")
MEASURES = (*FEATURE_NAMES, "confidence", "normalized_entropy")


def _moment() -> dict[str, Any]:
    return {"count": 0, "mean": 0.0, "m2": 0.0, "minimum": None, "maximum": None}


def _cell() -> dict[str, Any]:
    return {
        "samples": 0,
        "episodes": 0,
        "complete_episodes": 0,
        "complete_duration_ns": 0,
        "censored_episodes": 0,
        "censored_duration_ns": 0,
        "moments": {name: _moment() for name in MEASURES},
    }


def _update(moment: dict[str, Any], value: float) -> None:
    count = moment["count"] + 1
    delta = value - moment["mean"]
    mean = finite(moment["mean"] + delta / count, "running mean")
    m2 = finite(moment["m2"] + delta * (value - mean), "running squared deviations")
    moment.update(
        count=count,
        mean=mean,
        m2=max(0.0, m2),  # Welford: guard negative roundoff, not missing data.
        minimum=value if moment["minimum"] is None else min(value, moment["minimum"]),
        maximum=value if moment["maximum"] is None else max(value, moment["maximum"]),
    )


class RegimeDiagnostics:
    """Fixed K-by-feature counters, two current episodes, no retained row history."""

    def __init__(self, state_count: int, interval_ns: int) -> None:
        integer(state_count, "diagnostic state count", minimum=2)
        if state_count > 5:
            raise ValueError("diagnostics supports K=2..5")
        integer(interval_ns, "diagnostic sampling interval", minimum=1)
        self.state_count, self.interval_ns = state_count, interval_ns
        self.labels = tuple(f"STATE_{i}" for i in range(state_count)) + ("UNCONFIRMED",)
        self._cells = {basis: {label: _cell() for label in self.labels} for basis in BASES}
        self._transitions = {
            basis: {label: {other: 0 for other in self.labels} for label in self.labels} for basis in BASES
        }
        self._open: dict[str, dict[str, Any] | None] = dict.fromkeys(BASES)
        self._last_sample_ns: int | None = None
        self._valid_samples = 0

    def _close(self, basis: str, *, right_censored: bool) -> None:
        episode = self._open[basis]
        if episode is None:
            return
        cell = self._cells[basis][episode["label"]]
        kind = "censored" if right_censored or episode["left_censored"] else "complete"
        cell[kind + "_episodes"] += 1
        cell[kind + "_duration_ns"] += episode["samples"] * self.interval_ns
        self._open[basis] = None

    def invalidate(self) -> None:
        for basis in BASES:
            self._close(basis, right_censored=True)

    def observe(self, row: Mapping[str, Any]) -> None:
        if row["event_type"] == "invalidation":
            self.invalidate()
            return
        if row["event_type"] != "sample":
            raise ValueError("diagnostic row kind invalid")
        sample_ns = integer(row["sample_ns"], "diagnostic sample time")
        if sample_ns % self.interval_ns or (self._last_sample_ns is not None and sample_ns <= self._last_sample_ns):
            raise ValueError("diagnostic sample is off grid or not increasing")
        available = integer(row["available_at_ns"], "diagnostic availability")
        if available < sample_ns:
            raise ValueError("diagnostic sample not causally available")
        if row["status"] != "VALID":
            self.invalidate()
            self._last_sample_ns = sample_ns
            return
        values = vector(row["features"], "diagnostic features", len(FEATURE_NAMES))
        confidence = finite(row["confidence"], "diagnostic confidence")
        entropy = finite(row["normalized_entropy"], "diagnostic entropy")
        if not 0 <= confidence <= 1 or not 0 <= entropy <= 1:
            raise ValueError("diagnostic confidence/entropy outside [0,1]")
        raw = integer(row["raw_map_state"], "diagnostic MAP state")
        active = row["active_state"]
        if raw >= self.state_count or (
            active is not None and integer(active, "diagnostic active state") >= self.state_count
        ):
            raise ValueError("diagnostic state out of range")
        if row.get("filter_reset_reason") is not None or (
            self._last_sample_ns is not None and sample_ns != self._last_sample_ns + self.interval_ns
        ):
            self.invalidate()
        labels = {"raw_map": f"STATE_{raw}", "active": f"STATE_{active}" if active is not None else "UNCONFIRMED"}
        for basis, label in labels.items():
            episode = self._open[basis]
            left_censored = episode is None
            if episode is not None:
                self._transitions[basis][episode["label"]][label] += 1
                if episode["label"] != label:
                    self._close(basis, right_censored=False)
                    episode = None
            if episode is None:
                episode = {"label": label, "samples": 0, "start_sample_ns": sample_ns, "left_censored": left_censored}
                self._open[basis] = episode
                self._cells[basis][label]["episodes"] += 1
            episode["samples"] += 1
            cell = self._cells[basis][label]
            cell["samples"] += 1
            for name, value in zip(MEASURES, (*values, confidence, entropy)):
                _update(cell["moments"][name], value)
        self._valid_samples += 1
        self._last_sample_ns = sample_ns

    def summary(self, total_samples: int) -> dict[str, Any]:
        integer(total_samples, "total diagnostic samples")
        if total_samples < self._valid_samples:
            raise ValueError("diagnostic valid samples exceed total")
        cells = deepcopy(self._cells)
        for basis, groups in cells.items():
            for label, cell in groups.items():
                episode = self._open[basis]
                # Report the open right-censored tail without closing mutable state.
                if episode is not None and episode["label"] == label:
                    cell["censored_episodes"] += 1
                    cell["censored_duration_ns"] += episode["samples"] * self.interval_ns
                samples, complete = cell["samples"], cell["complete_episodes"]
                cell["fraction_of_valid_samples"] = samples / self._valid_samples if self._valid_samples else None
                cell["fraction_of_all_samples"] = samples / total_samples if total_samples else None
                cell["sample_grid_duration_ns"] = samples * self.interval_ns
                cell["mean_complete_episode_seconds"] = (
                    cell["complete_duration_ns"] / complete / 1e9 if complete else None
                )
                cell["mean_observed_episode_seconds"] = (
                    samples * self.interval_ns / cell["episodes"] / 1e9 if cell["episodes"] else None
                )
                cell["open_tail_samples"] = (
                    episode["samples"] if episode is not None and episode["label"] == label else 0
                )
                cell["measures"] = {
                    name: {
                        "mean": moment["mean"] if samples else None,
                        "population_variance": moment["m2"] / samples if samples else None,
                        "minimum": moment["minimum"],
                        "maximum": moment["maximum"],
                    }
                    for name, moment in cell.pop("moments").items()
                }
        return {
            "schema_version": "lob_sim.hmm_state_diagnostics.v1",
            "sampling_interval_ns": self.interval_ns,
            "valid_samples": self._valid_samples,
            "total_samples": total_samples,
            "sample_grid_valid_fraction": self._valid_samples / total_samples if total_samples else None,
            "occupancy_basis": "sample_grid;not_joint_valid_wall_clock_coverage",
            "episode_basis": "consecutive_grid_labels;complete_only_between_observed_state_changes;edges_and_resets_censored",
            "duration_basis": "sample_count*interval;quantized_observed_span_not_true_latent_duration",
            "conditioned": cells,
            "sample_transitions": deepcopy(self._transitions),
            "retained_episodes": sum(episode is not None for episode in self._open.values()),
            "claim_ready": False,
        }

    def checkpoint(self) -> dict[str, Any]:
        return deepcopy(
            {
                "schema_version": "lob_sim.hmm_state_diagnostics_checkpoint.v1",
                "state_count": self.state_count,
                "interval_ns": self.interval_ns,
                "cells": self._cells,
                "transitions": self._transitions,
                "open": self._open,
                "last_sample_ns": self._last_sample_ns,
                "valid_samples": self._valid_samples,
            }
        )

    def validated_copy(self, checkpoint: object) -> RegimeDiagnostics:
        data = require_keys(checkpoint, set(self.checkpoint()), "diagnostic checkpoint")
        for key in ("schema_version", "state_count", "interval_ns"):
            if data[key] != self.checkpoint()[key] or type(data[key]) is not type(self.checkpoint()[key]):
                raise ValueError("diagnostic checkpoint configuration mismatch")
        valid = integer(data["valid_samples"], "diagnostic valid samples")
        last = data["last_sample_ns"]
        if last is not None and integer(last, "diagnostic sample watermark") % self.interval_ns:
            raise ValueError("diagnostic checkpoint is off grid")
        if valid and last is None:
            raise ValueError("diagnostic checkpoint lacks sample watermark")
        cells = require_keys(data["cells"], set(BASES), "diagnostic bases")
        transitions = require_keys(data["transitions"], set(BASES), "diagnostic transitions")
        opened = require_keys(data["open"], set(BASES), "diagnostic open episodes")
        for basis in BASES:
            groups = require_keys(cells[basis], set(self.labels), "diagnostic labels")
            matrix = require_keys(transitions[basis], set(self.labels), "diagnostic transition labels")
            episode = opened[basis]
            if episode is not None:
                require_keys(
                    episode, {"label", "samples", "start_sample_ns", "left_censored"}, "diagnostic open episode"
                )
                n = integer(episode["samples"], "open episode samples", minimum=1)
                start = integer(episode["start_sample_ns"], "episode start")
                if (
                    episode["label"] not in self.labels
                    or type(episode["left_censored"]) is not bool
                    or last is None
                    or start + (n - 1) * self.interval_ns != last
                ):
                    raise ValueError("diagnostic episode anchors inconsistent")
            for label, cell in groups.items():
                require_keys(cell, set(_cell()), "diagnostic state cell")
                samples = integer(cell["samples"], "diagnostic state samples")
                for key in (
                    "episodes",
                    "complete_episodes",
                    "censored_episodes",
                    "complete_duration_ns",
                    "censored_duration_ns",
                ):
                    integer(cell[key], key)
                tail = episode if episode is not None and episode["label"] == label else None
                if (
                    cell["episodes"] != cell["complete_episodes"] + cell["censored_episodes"] + int(tail is not None)
                    or cell["episodes"] > samples
                    or cell["complete_duration_ns"]
                    + cell["censored_duration_ns"]
                    + (tail["samples"] * self.interval_ns if tail else 0)
                    != samples * self.interval_ns
                ):
                    raise ValueError("diagnostic episode totals inconsistent")
                for kind in ("complete", "censored"):
                    duration, count = cell[kind + "_duration_ns"], cell[kind + "_episodes"]
                    if (
                        duration % self.interval_ns
                        or duration < count * self.interval_ns
                        or ((duration == 0) != (count == 0))
                    ):
                        raise ValueError("diagnostic closed episode durations inconsistent")
                moments = require_keys(cell["moments"], set(MEASURES), "diagnostic measures")
                for name, moment in moments.items():
                    require_keys(moment, set(_moment()), "diagnostic moment")
                    if integer(moment["count"], "moment sample count") != samples:
                        raise ValueError("diagnostic moment count mismatch")
                    mean, m2 = finite(moment["mean"], "moment mean"), finite(moment["m2"], "moment m2")
                    if m2 < 0:
                        raise ValueError("diagnostic negative squared deviations")
                    if not samples:
                        if moment != _moment():
                            raise ValueError("empty diagnostic moment retains data")
                    else:
                        low, high = (
                            finite(moment["minimum"], "moment minimum"),
                            finite(moment["maximum"], "moment maximum"),
                        )
                        if (
                            low > high
                            or mean < low - abs(low) * 1e-12
                            or mean > high + abs(high) * 1e-12
                            or (samples == 1 and m2 != 0)
                        ):
                            raise ValueError("diagnostic moment bounds inconsistent")
                        if name in {"confidence", "normalized_entropy"} and not 0 <= low <= high <= 1:
                            raise ValueError("diagnostic probability bounds inconsistent")
                row = require_keys(matrix[label], set(self.labels), "diagnostic transition destinations")
                for count in row.values():
                    integer(count, "diagnostic transition count")
                if row[label] != samples - cell["episodes"]:
                    raise ValueError("diagnostic self transitions disagree with episode spans")
                # Every complete episode has an observed outbound switch.
                if sum(row[other] for other in self.labels if other != label) < cell["complete_episodes"]:
                    raise ValueError("diagnostic complete episode lacks transition")
                if sum(row.values()) > samples:
                    raise ValueError("diagnostic transitions exceed samples")
            if sum(cell["samples"] for cell in groups.values()) != valid:
                raise ValueError("diagnostic sample total mismatch")
            if basis == "raw_map" and (
                groups["UNCONFIRMED"]["samples"] or (episode is not None and episode["label"] == "UNCONFIRMED")
            ):
                raise ValueError("valid raw MAP cannot be unconfirmed")
        if (opened["raw_map"] is None) != (opened["active"] is None):
            raise ValueError("diagnostic episode validity bases disagree")
        candidate = RegimeDiagnostics(self.state_count, self.interval_ns)
        candidate._cells, candidate._transitions, candidate._open = (
            deepcopy(dict(cells)),
            deepcopy(dict(transitions)),
            deepcopy(dict(opened)),
        )
        candidate._last_sample_ns, candidate._valid_samples = last, valid
        return candidate


def format_state_report(summary: Mapping[str, Any]) -> str:
    """Human-readable market diagnostics, after serialized-trace verification."""
    diagnostics = summary["state_diagnostics"]
    lines = [
        "Causal regime market-state diagnostics (descriptive only)",
        f"Model: {summary['config']['model_sha256']}",
        f"Sampling: {diagnostics['sampling_interval_ns'] / 1e9:g}s; valid samples: {diagnostics['valid_samples']}/{diagnostics['total_samples']}",
        "Occupancy is a sample fraction, NOT joint-valid wall-clock coverage.",
        "Durations are quantized observed label spans, NOT true latent dwell times.",
        "Complete episodes require observed state-change boundaries at both ends; edges, gaps and resets are censored.",
        "Market features are not execution quality, state PnL, alpha or a holdout result.",
    ]

    def number(value: Any) -> str:
        return "n/a" if value is None else f"{value:.6g}"

    for basis, groups in diagnostics["conditioned"].items():
        lines.append(f"\n{'Raw MAP' if basis == 'raw_map' else 'Active hysteretic'} labels:")
        for label, cell in groups.items():
            fraction = cell["fraction_of_valid_samples"]
            percent = "n/a" if fraction is None else f"{fraction:.2%}"
            lines.append(
                f"  {label}: samples={cell['samples']} ({percent} of valid); "
                f"complete/censored episodes={cell['complete_episodes']}/{cell['censored_episodes']}; "
                f"mean complete duration={number(cell['mean_complete_episode_seconds'])}s"
            )
            for name, moment in cell["measures"].items():
                lines.append(
                    f"    {name}: mean={number(moment['mean'])}; variance={number(moment['population_variance'])}"
                )
        lines.append("  Sample transition counts (including self; no crossing invalid boundaries):")
        for label, row in diagnostics["sample_transitions"][basis].items():
            lines.append("    " + label + ": " + ", ".join(f"{other}={count}" for other, count in row.items()))
    return "\n".join(lines)


def inspect_run(run_dir: str | Path) -> str:
    """Read a completed bounded bundle and verify relevant model/regime identities.

    No report is printed before the full regime trace is reduced and compared.
    This checks the regime audit, not the underlying market-data coverage or an
    economic claim. Content hashes are identities, not author authentication.
    """
    from .artifact import load_model
    from .observation import verify_trace
    from .validation import strict_json

    directory = Path(run_dir)
    if (directory / "_INCOMPLETE.json").exists() or any(directory.glob("*.partial")):
        raise ValueError("regime report requires finalized audit files")
    summary_path = directory / "summary.json"
    limit = 32 * 1024 * 1024
    with summary_path.open("rb") as handle:
        payload = handle.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("regime summary exceeds 32 MiB diagnostic limit")
    summary = strict_json(payload.decode("utf-8"))
    if not isinstance(summary, Mapping) or not isinstance(summary.get("hmm"), Mapping):
        raise ValueError("run has no HMM observation summary")
    hmm = summary["hmm"]
    model = load_model(directory / "hmm_model.json")
    if (
        model.model_sha256 != hmm["config"]["model_sha256"]
        or model.features.interval_ns != hmm["state_diagnostics"]["sampling_interval_ns"]
    ):
        raise ValueError("regime report model/clock identity mismatch")
    if model.parameters.state_count != len(hmm["raw_map_sample_counts"]):
        raise ValueError("regime report model dimensions mismatch")
    verify_trace(directory / "regime_trace.csv", hmm)
    report = "Verified serialized regime audit.\n" + format_state_report(hmm)
    execution = summary.get("hmm_execution")
    if isinstance(execution, Mapping) and "quote_lifecycles" in execution:
        from .execution import format_execution_report, verify_execution_trace
        from .quotes import format_quote_report, verify_quote_trace

        if (
            execution.get("model_sha256") != model.model_sha256
            or execution.get("symbol") != hmm["config"]["symbol"]
            or execution["quote_lifecycles"].get("symbol") != execution.get("symbol")
        ):
            raise ValueError("quote report model/symbol identity mismatch")
        verify_execution_trace(directory / "regime_execution.csv", execution)
        labels = tuple(f"STATE_{i}" for i in range(model.parameters.state_count)) + ("UNCONFIRMED", "UNAVAILABLE")
        verify_quote_trace(
            directory / "regime_quotes.csv",
            execution["quote_lifecycles"],
            labels,
            execution_path=directory / "regime_execution.csv",
        )
        report += "\n\n" + format_quote_report(execution["quote_lifecycles"])
        report += "\n\n" + format_execution_report(execution)
    risk = summary.get("hmm_risk")
    if risk is not None:
        from .risk import format_risk_report, verify_risk_trace

        if (
            not isinstance(risk, Mapping)
            or risk.get("model_sha256") != model.model_sha256
            or risk.get("symbol") != hmm["config"]["symbol"]
            or risk.get("state_count") != model.parameters.state_count
        ):
            raise ValueError("risk report model/symbol identity mismatch")
        verify_risk_trace(
            directory / "regime_risk.csv", risk, regime_path=directory / "regime_trace.csv", regime_summary=hmm
        )
        report += "\n\n" + format_risk_report(risk)
    return report
