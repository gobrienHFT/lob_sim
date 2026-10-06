"""Registered paired diagnostic study; independent tapes and fixed latency.

No optimizer can see the test partition. Every feature cadence, policy, fitter
restart and failure is retained. Short legacy clips remain diagnostics, not a
claim of holdout benefit, private fills or ten complete joint-valid UTC days.
"""

from __future__ import annotations

import csv
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..config import Config
from ..replay.inspection import file_sha256
from ..research.protocol import ResearchRegistry
from ..sim.checkpoint import checkpoint_code_identity
from ..sim.run_manifest import config_snapshot
from ..sim.runner import run_bounded_simulation
from .artifact import save_model
from .collection import extract_feature_collection, collection_split, read_collection_partition
from .dataset import publish_json
from .features import FeatureSpec
from .fit import FitConfig, fit_candidates
from .hysteresis import HysteresisConfig
from .policy import RegimePolicyConfig
from .risk import verify_risk_trace
from .settings import HMMSettings
from .study_periods import read_risk_periods, compare_risk_periods
from .study_outcomes import outcome_contract, read_execution_periods, compare_execution_periods
from .study_pnl import PNL_CONTRACT, read_pnl_periods, compare_pnl_periods
from .validation import canonical_json, identity, integer, strict_json

PRIMARY = FeatureSpec()
CADENCE = FeatureSpec(interval_ns=250_000_000, window_steps=40)


def core_audit_hashes(files: dict[str, Path]) -> dict[str, Any]:
    """Normalize ONLY explicitly namespaced HMM event diagnostics.

    Fill and markout audits must agree exactly, including scenario/evidence,
    latency and legacy regime labels. Economic differences cannot be scrubbed.
    CSV is streamed; the digest includes every event in its original order.
    """
    digest = sha256(b"lob_sim.study_core_events.v1")
    count = 0
    with files["event_trace"].open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            details = strict_json(row["details"])
            if not isinstance(details, dict):
                raise ValueError("event details must be an object")
            row["details"] = canonical_json(
                {k: v for k, v in details.items() if k != "hmm" and not k.startswith("hmm_")}
            )
            digest.update((canonical_json(row) + "\n").encode())
            count += 1
    return {
        "event_count": count,
        "core_event_sha256": digest.hexdigest(),
        "trades_file_sha256": file_sha256(files["trades"]),
        "markouts_file_sha256": file_sha256(files["markouts"]),
    }


def _metrics(summary: dict[str, Any]) -> dict[str, Any]:
    # Existing verified reducers retain full source/queue/state conditioning in
    # each run bundle. The study projection does not recompute matching or PnL.
    return {
        key: summary[key]
        for key in (
            "hmm_economics",
            "hmm_execution",
            "hmm_risk",
            "hmm_quotes",
            "hmm",
            "integrity",
            "evidence_quality",
        )
        if key in summary
    }


def run_regime_study(
    inputs: tuple[Path, ...],
    directory: str | Path,
    cfg: Config,
    *,
    symbol: str,
    fit_config: FitConfig = FitConfig(),
    bootstrap_replicates: int = 2000,
) -> dict[str, Any]:
    integer(bootstrap_replicates, "bootstrap replicates", minimum=1)
    if cfg.hmm is not None or cfg.sim_latency_mode != "fixed":
        raise ValueError("study needs HMM disabled and fixed latency: same seed is NOT matched empirical action draws")
    if fit_config.state_counts != (2, 3, 4, 5):
        raise ValueError("registered study must retain K=2..5 selection diagnostics")
    if not cfg.mm_enabled or cfg.mm_strategy_profile != "research_mm" or cfg.symbols != (symbol,):
        raise ValueError("study requires enabled single-symbol research_mm reference; choose config explicitly")
    hashes = tuple(file_sha256(path) for path in inputs)
    if not hashes or len(set(hashes)) != len(hashes):
        raise ValueError("study inputs must be nonempty and content-distinct")
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    source_identity = checkpoint_code_identity()
    horizons = (
        tuple(
            sorted(
                set(cfg.sim_markout_horizons_ms)
                | {int((Decimal(str(cfg.sim_adverse_markout_seconds)) * Decimal("1000")).to_integral_value())}
            )
        )
        if cfg.sim_adverse_markout_seconds > 0
        else ()
    )
    registry = ResearchRegistry()
    variants = (
        ("baseline", None, None),
        ("observe", PRIMARY, None),
        ("policy", PRIMARY, RegimePolicyConfig()),
        ("hard_active", PRIMARY, RegimePolicyConfig(risk_aggregation="hard_active")),
        ("cadence_250ms", CADENCE, RegimePolicyConfig()),
    )
    registered = {}
    for name, spec, policy in variants:
        hysteresis = HysteresisConfig(
            confirmation_samples=3 if spec is None else 3_000_000_000 // spec.interval_ns,
            minimum_state_age_samples=3 if spec is None else 3_000_000_000 // spec.interval_ns,
        )
        registered[name] = registry.register(
            name,
            {
                "base_config": config_snapshot(cfg),
                "source_sha256": list(hashes),
                "code_identity": source_identity,
                "features": spec.as_dict() if spec else None,
                "fit_config": fit_config.as_dict() if spec else None,
                "policy": policy.as_dict() if policy else None,
                "hysteresis": hysteresis.as_dict() if spec else None,
                "pairing": "fixed latency;identical tape/seed/fees/queue assumptions;fresh engine and zero inventory per source",
                "bootstrap": {
                    "period_seconds": 60,
                    "primary_block_minutes": 30,
                    "sensitivity_minutes": [5, 60],
                    "confidence": 0.95,
                    "replicates": bootstrap_replicates,
                    "seed": cfg.sim_seed,
                    "execution_outcomes": outcome_contract(horizons),
                    "marked_pnl": dict(PNL_CONTRACT),
                },
            },
        )
    publish_json(root / "registry.json", registry.freeze())  # Before extraction or untouched test access.
    models, fit_reports, collections = {}, {}, {}
    failures: list[dict[str, Any]] = []
    split = None
    for label, spec in (("primary", PRIMARY), ("cadence_250ms", CADENCE)):
        folder = root / label
        try:
            collection = extract_feature_collection(inputs, folder, cfg, symbol=symbol, spec=spec)
            candidate_split = collection_split(folder)
            if split is not None and candidate_split != split:
                raise ValueError("cadence changed the registered UTC-day universe; paired study unavailable")
            if any(len(entry["wall_spans"]) != 1 for entry in collection["sources"]):
                raise ValueError("strategy study needs independent single-UTC-day inputs; no hidden cross-day resets")
            train = read_collection_partition(folder, candidate_split, "calibration", symbol=symbol)
            validation = read_collection_partition(folder, candidate_split, "validation", symbol=symbol)
            fitted = fit_candidates(train, validation, fit_config)
            publish_json(folder / "fit_report.json", fitted.report())
            fit_reports[label] = fitted.report()
            collections[label] = collection
            if split is None:
                split = candidate_split
            if fitted.model is None:
                raise ValueError("no valid fitted candidate; all attempts retained in fit_report.json")
            save_model(folder / "model.json", fitted.model)
            models[label] = fitted.model
        except (ValueError, OSError, RuntimeError) as exc:
            failures.append({"stage": "fit", "variant": label, "error_type": type(exc).__name__, "error": str(exc)})
    results: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    if split is not None and "primary" in collections:
        primary_collection = collections["primary"]
        # Explicit workflow guard also verifies untouched feature rows before
        # scoring. They are not fed back to fitting, scaler or risk signatures.
        for label in models:
            read_collection_partition(root / label, split, "test", symbol=symbol, registry=registry)
        for index, (path, entry) in enumerate(zip(inputs, primary_collection["sources"])):
            span = entry["wall_spans"][0]
            if span["utc_day"] not in split.test_days:
                continue
            successful, period_tables, outcome_tables, pnl_tables = {}, {}, {}, {}
            for name, spec, policy in variants:
                label = "cadence_250ms" if name == "cadence_250ms" else "primary"
                record: dict[str, Any] = {
                    "variant": name,
                    "variant_id": registered[name],
                    "source_sha256": hashes[index],
                    "utc_day": span["utc_day"],
                }
                if spec is not None and label not in models:
                    record.update(status="unavailable", error="registered fitted model unavailable")
                    results.append(record)
                    continue
                try:
                    if file_sha256(path) != hashes[index] or checkpoint_code_identity() != source_identity:
                        raise ValueError("registered source/package changed during study")
                    runtime = (
                        None
                        if spec is None
                        else HMMSettings(
                            models[label],
                            symbol,
                            HysteresisConfig(
                                confirmation_samples=3_000_000_000 // spec.interval_ns,
                                minimum_state_age_samples=3_000_000_000 // spec.interval_ns,
                            ),
                            "observe" if policy is None else "policy",
                            policy,
                        )
                    )
                    configuration = replace(
                        cfg,
                        hmm=runtime,
                        mm_strategy_profile="hmm_regime_mm" if policy else "research_mm",
                        record_dir=root / "runs" / f"{index:06d}" / name,
                    )
                    files, summary = run_bounded_simulation(configuration, path)
                    if file_sha256(path) != hashes[index] or checkpoint_code_identity() != source_identity:
                        raise ValueError("registered source/package changed during run")
                    record.update(
                        status="completed",
                        core_audits=core_audit_hashes(files),
                        run_dir=files["summary"].parent.relative_to(root).as_posix(),
                        metrics=_metrics(summary),
                        artifact_sha256={key: file_sha256(value) for key, value in files.items()},
                    )
                    if runtime is not None:
                        if span["wall_offset_ns"] is None or span["clock_basis"] is None:
                            verify_risk_trace(files["regime_risk"], summary["hmm_risk"])
                            record["statistics_unavailable_reason"] = (
                                "source has changing wall/logical anchor or mixed clock basis; no fixed-UTC projection asserted"
                            )
                        else:
                            period_tables[name] = read_risk_periods(files["regime_risk"], summary["hmm_risk"], span)
                            if summary["hmm_execution"]["horizons_ms"] != list(horizons):
                                raise ValueError("runtime markout horizons differ from the frozen outcome contract")
                            outcome_tables[name] = read_execution_periods(
                                files["regime_execution"],
                                files["trades"],
                                execution_summary=summary["hmm_execution"],
                                economic_summary=summary["hmm_economics"],
                                risk_periods=period_tables[name],
                                span=span,
                            )
                            outcome_path = files["summary"].parent / "clock_outcomes.json"
                            outcome_document = {
                                "schema_version": "lob_sim.hmm_clock_outcome_periods.v1",
                                "source_sha256": hashes[index],
                                "variant_id": registered[name],
                                "contract": outcome_contract(horizons),
                                "wall_span": span,
                                "parents": summary["hmm_economics"]["parents"],
                                "model_sha256": summary["hmm_execution"]["model_sha256"],
                                "grid": summary["hmm_economics"]["grid"],
                                "periods": list(outcome_tables[name]),
                                "claim_ready": False,
                            }
                            outcome_document["report_sha256"] = identity(outcome_document)
                            publish_json(outcome_path, outcome_document)
                            record["artifact_sha256"]["clock_outcomes"] = file_sha256(outcome_path)
                            record["clock_outcomes_path"] = outcome_path.relative_to(root).as_posix()
                            pnl_tables[name] = read_pnl_periods(
                                files["regime_risk"],
                                files["trades"],
                                files["regime_execution"],
                                risk_summary=summary["hmm_risk"],
                                execution_summary=summary["hmm_execution"],
                                economic_summary=summary["hmm_economics"],
                                risk_periods=period_tables[name],
                                span=span,
                            )
                            # Separate native consumers must agree on every
                            # available [start,end) fee total, including rebates
                            # and excluded-risk minutes. No double subtraction.
                            for pnl, outcome in zip(pnl_tables[name], outcome_tables[name], strict=True):
                                if pnl["fees_delta_quote_rational"] is not None and Fraction(
                                    pnl["fees_delta_quote_rational"]
                                ) != Fraction(outcome["additive"]["fees_quote"]):
                                    raise ValueError("PnL endpoint fees differ from native execution clock totals")
                            pnl_path = files["summary"].parent / "clock_pnl.json"
                            pnl_document = {
                                "schema_version": "lob_sim.hmm_clock_pnl_periods.v1",
                                "source_sha256": hashes[index],
                                "variant_id": registered[name],
                                "contract": dict(PNL_CONTRACT),
                                "wall_span": span,
                                "parents": summary["hmm_economics"]["parents"],
                                "model_sha256": summary["hmm_execution"]["model_sha256"],
                                "grid": summary["hmm_economics"]["grid"],
                                "periods": list(pnl_tables[name]),
                                "claim_ready": False,
                            }
                            pnl_document["report_sha256"] = identity(pnl_document)
                            publish_json(pnl_path, pnl_document)
                            record["artifact_sha256"]["clock_pnl"] = file_sha256(pnl_path)
                            record["clock_pnl_path"] = pnl_path.relative_to(root).as_posix()
                    successful[name] = record
                except (ValueError, OSError, RuntimeError) as exc:
                    record.update(status="failed", error_type=type(exc).__name__, error=str(exc))
                    failures.append(
                        {
                            "stage": "run",
                            "variant": name,
                            "source_sha256": hashes[index],
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
                results.append(record)
            baseline, observed = successful.get("baseline"), successful.get("observe")
            if baseline is None or observed is None or baseline["core_audits"] != observed["core_audits"]:
                failures.append(
                    {
                        "stage": "pairing",
                        "source_sha256": hashes[index],
                        "error": "baseline/observe core events, fills or markouts differ; baseline period proxy forbidden",
                    }
                )
                continue
            # Observation mode is a measured baseline proxy ONLY after exact
            # core parity. No model affects baseline actions or execution.
            baseline["metrics"] = observed["metrics"]
            baseline["measurement_source"] = "observe sidecars after exact event/fill/markout parity"
            for name in ("policy", "hard_active", "cadence_250ms"):
                if name not in successful:
                    continue
                if name not in period_tables or "observe" not in period_tables:
                    comparisons.append(
                        {
                            "variant": name,
                            "baseline": "baseline",
                            "source_sha256": hashes[index],
                            "utc_day": span["utc_day"],
                            "observation_core_parity": True,
                            "risk_clock_comparison": None,
                            "execution_clock_comparison": None,
                            "pnl_clock_comparison": None,
                            "unavailable_reason": "source wall/logical clock mapping is not fixed; source risk/economics audits remain verified",
                        }
                    )
                    continue
                comparisons.append(
                    {
                        "variant": name,
                        "baseline": "baseline",
                        "source_sha256": hashes[index],
                        "utc_day": span["utc_day"],
                        "observation_core_parity": True,
                        "risk_clock_comparison": compare_risk_periods(
                            period_tables[name],
                            period_tables["observe"],
                            hashes[index],
                            replicates=bootstrap_replicates,
                            seed=cfg.sim_seed,
                        ),
                        "execution_clock_comparison": compare_execution_periods(
                            outcome_tables[name],
                            outcome_tables["observe"],
                            hashes[index],
                            horizons=horizons,
                            replicates=bootstrap_replicates,
                            seed=cfg.sim_seed,
                        ),
                        "pnl_clock_comparison": compare_pnl_periods(
                            pnl_tables[name],
                            pnl_tables["observe"],
                            hashes[index],
                            replicates=bootstrap_replicates,
                            seed=cfg.sim_seed,
                        ),
                    }
                )
    report: dict[str, Any] = {
        "schema_version": "lob_sim.hmm_regime_study.v3",
        "status": "completed" if results and not failures else "incomplete",
        "claim_ready": False,
        "claim_reason": "short/legacy UTC snippets are not ten certified full joint-valid days; no holdout or policy-benefit claim",
        "source_sha256": list(hashes),
        "code_identity": source_identity,
        "registry_sha256": registry.snapshot()["registry_sha256"],
        "registered_variants": registered,
        "split": split.as_dict() if split is not None else None,
        "fit_reports": fit_reports,
        "results": results,
        "comparisons": comparisons,
        "failures": failures,
        "scope": "frozen primary models;independent source starts/zero inventory;single fixed-latency scenario;not walk-forward or profitability",
        "statistics": "paired common complete UTC-minute risk,execution and causal marked-PnL sufficient statistics;ratio-of-sums outcomes and mean equity deltas;30-minute block primary,5/60 sensitivity;short/gapped strata or undefined ratio replicas yield null CI",
        "limitations": [
            "not private FIFO or fill truth",
            "no funding",
            "global PnL and observed drawdown are descriptive scenario outputs",
            "PnL clock bootstrap estimates mean eligible-minute equity changes,not total-path PnL or full-path drawdown intervals",
            "causal equity left limits exclude all same-time observations/fills;unknown endpoints and invalid-risk intervals remain null,not gap-bridged",
            "fill count per minute is activity,not quote-denominated fill probability;native source-conditioned audit summaries remain separate",
            "no drop-feature ablations: cadence and risk aggregation are the registered ablations",
            "artifact hashes identify bytes, not a trusted author or liveness certification",
        ],
    }
    report["report_sha256"] = identity(report)
    publish_json(root / "study_report.json", report)
    return report


def format_study_report(report: dict[str, Any]) -> str:
    lines = [
        "Registered HMM policy study (diagnostic, not a holdout claim)",
        f"Status: {report['status']}; registry: {report['registry_sha256']}",
        report["claim_reason"],
    ]
    for result in report["results"]:
        economic = result.get("metrics", {}).get("hmm_economics", {})
        lines.append(
            f"{result['utc_day']} {result['variant']}: {result['status']}; "
            f"fills={economic.get('fill_count', 'n/a')}; net marked PnL={economic.get('net_marked_pnl_quote_rational', 'n/a')}"
        )
    for comparison in report["comparisons"]:
        pnl = comparison.get("pnl_clock_comparison")
        if pnl is None:
            reason = comparison.get("unavailable_reason", "marked-PnL analysis was not included in this report version")
            lines.append(f"{comparison['variant']} vs baseline: clock PnL unavailable; {reason}")
            continue
        statistic = pnl["metrics"]["net_marked_delta_quote_rational"]["30"]
        lines.append(
            f"{comparison['utc_day']} {comparison['variant']} vs baseline: "
            f"mean eligible-minute net PnL delta={statistic['estimate']} quote/minute; "
            f"95% 30-minute-block interval={statistic['interval']}; "
            f"eligible={statistic['eligible_period_count']}/{statistic['period_count']} minutes"
            + (f"; {statistic['unavailable_reason']}" if statistic.get("unavailable_reason") else "")
        )
    for failure in report["failures"]:
        lines.append(f"FAILED {failure['stage']}: {failure['error']}")
    lines.append(
        "No best-PnL selection: inspect every fit attempt, variant, common-period denominator and missing interval."
    )
    return "\n".join(lines)
