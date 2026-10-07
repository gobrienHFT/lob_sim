"""Small CLI adapter for evidence operations; no market or research logic."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

from lob_sim.config import load_config


def register_commands(sub: argparse._SubParsersAction) -> None:
    audit = sub.add_parser("capture-audit", help="Audit finalized schema-v3 receipts and causal validity intervals")
    audit.add_argument("--file", required=True)
    audit.add_argument("--out", required=True)
    audit.add_argument("--symbol", action="append", help="Explicit universe; default BTCUSDT+ETHUSDT")
    days = sub.add_parser("research-days", help="Audit paired-symbol captures and apply fixed full-UTC-day admission")
    days.add_argument("--file", required=True, action="append")
    days.add_argument("--out", required=True)
    verify = sub.add_parser("research-days-verify", help="Reopen source bytes and independently re-read day artifacts")
    verify.add_argument("--file", required=True, action="append")
    verify.add_argument("--bundle", required=True)
    soak = sub.add_parser(
        "capture-soak", help="Guarded public BTCUSDT+ETHUSDT capture; default 25h, restart in a new directory"
    )
    soak.add_argument("--out", required=True)
    soak.add_argument("--seconds", type=int, default=90_000)
    soak.add_argument("--sample-seconds", type=int, default=5)
    soak.add_argument("--minimum-disk-free-mib", type=int, default=2048)
    soak.add_argument("--maximum-rss-mib", type=int, default=1024)
    soak.add_argument("--restart-from")
    soak_verify = sub.add_parser("capture-soak-verify", help="Independently re-read finalized soak telemetry")
    soak_verify.add_argument("--bundle", required=True)
    registry = sub.add_parser(
        "research-register", help="Freeze the complete protocol only after >=10 independently admitted UTC days"
    )
    registry.add_argument("--days", required=True)
    registry.add_argument("--file", required=True, action="append")
    registry.add_argument("--out", required=True)
    for command, help_text in (
        ("research-prepare", "Prepare admitted causal features in physical day/role files"),
        ("research-prepare-verify", "Independently re-read prepared feature admission"),
        ("research-fit", "Fit calibration/validation models; never open test feature rows"),
        ("research-fit-verify", "Independently verify saved models, ledgers and forward likelihoods"),
        ("research-views", "Prepare model-frozen held-out source/day replay views"),
        ("research-views-verify", "Re-read every normalized observation against its raw source"),
        ("research-study", "Replay the complete registered held-out scenario/latency/cadence grid"),
        ("research-study-verify", "Independently reopen the complete serialized research graph"),
    ):
        child = sub.add_parser(command, help=help_text)
        child.add_argument("--registry", required=True)
        child.add_argument("--registry-sha256", required=True)
        child.add_argument("--days", required=True)
        child.add_argument("--file", required=True, action="append")
        if command != "research-prepare":
            child.add_argument("--prepared", required=True)
        if command in {"research-prepare", "research-fit", "research-views", "research-study"}:
            child.add_argument("--out", required=True)
        if command in {
            "research-fit-verify",
            "research-views",
            "research-views-verify",
            "research-study",
            "research-study-verify",
        }:
            child.add_argument("--models", required=True)
        if command in {"research-views-verify", "research-study", "research-study-verify"}:
            child.add_argument("--views", required=True)
        if command == "research-study-verify":
            child.add_argument("--study", required=True)


def dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> bool:
    if args.command not in {
        "capture-audit",
        "research-days",
        "research-days-verify",
        "capture-soak",
        "capture-soak-verify",
        "research-register",
        "research-prepare",
        "research-prepare-verify",
        "research-fit",
        "research-fit-verify",
        "research-views",
        "research-views-verify",
        "research-study",
        "research-study-verify",
    }:
        return False
    from lob_sim.research.capture_audit import audit_capture
    from lob_sim.research.day_bundle import build_day_bundle, verify_day_bundle

    try:
        if args.command in {"research-views", "research-views-verify", "research-study", "research-study-verify"}:
            from lob_sim.research.registered_protocol import load_protocol
            from lob_sim.research.study_views import prepare_study_views, verify_views
            from lob_sim.research.fit_bundle import verify_registered_models, load_model_set
            from lob_sim.research.feature_reader import admitted_parents
            from lob_sim.research.study import run_registered_study
            from lob_sim.research.study_reader import verify_registered_study

            inputs = tuple(Path(p) for p in args.file)
            common = {"registry_sha256": args.registry_sha256}
            if args.command == "research-views":
                result = prepare_study_views(
                    inputs,
                    Path(args.days),
                    Path(args.registry),
                    Path(args.prepared),
                    Path(args.models),
                    Path(args.out),
                    load_config(args.env or ".env"),
                    **common,
                )
            elif args.command == "research-views-verify":
                protocol = load_protocol(Path(args.registry), **common)
                verify_registered_models(
                    Path(args.models), Path(args.prepared), protocol, Path(args.days), inputs, **common
                )
                models = load_model_set(Path(args.models), protocol, **common)
                result = verify_views(
                    Path(args.views),
                    protocol,
                    models,
                    admitted_parents(Path(args.days), protocol, inputs, **common),
                    inputs,
                    **common,
                )
            else:
                folder = Path(args.out) if args.command == "research-study" else Path(args.study)
                result = (
                    run_registered_study(
                        inputs,
                        Path(args.days),
                        Path(args.registry),
                        Path(args.prepared),
                        Path(args.models),
                        Path(args.views),
                        folder,
                        load_config(args.env or ".env"),
                        **common,
                    )
                    if args.command == "research-study"
                    else verify_registered_study(
                        folder,
                        inputs,
                        Path(args.days),
                        Path(args.registry),
                        Path(args.prepared),
                        Path(args.models),
                        Path(args.views),
                        load_config(args.env or ".env"),
                        **common,
                    )
                )
        elif args.command in {"research-prepare", "research-prepare-verify", "research-fit", "research-fit-verify"}:
            from lob_sim.research.registered_protocol import load_protocol
            from lob_sim.research.prepare_features import prepare_features
            from lob_sim.research.feature_reader import verify_prepared
            from lob_sim.research.fitting import fit_registered_models
            from lob_sim.research.fit_bundle import verify_registered_models

            inputs = tuple(Path(p) for p in args.file)
            if args.command == "research-prepare":
                result = prepare_features(
                    inputs,
                    Path(args.days),
                    Path(args.registry),
                    Path(args.out),
                    load_config(args.env or ".env"),
                    registry_sha256=args.registry_sha256,
                )
            elif args.command == "research-fit":
                result = fit_registered_models(
                    Path(args.prepared),
                    Path(args.registry),
                    Path(args.days),
                    inputs,
                    Path(args.out),
                    load_config(args.env or ".env"),
                    registry_sha256=args.registry_sha256,
                )
            else:
                protocol = load_protocol(Path(args.registry), registry_sha256=args.registry_sha256)
                result = (
                    verify_prepared(
                        Path(args.prepared), protocol, Path(args.days), inputs, registry_sha256=args.registry_sha256
                    )
                    if args.command == "research-prepare-verify"
                    else verify_registered_models(
                        Path(args.models),
                        Path(args.prepared),
                        protocol,
                        Path(args.days),
                        inputs,
                        registry_sha256=args.registry_sha256,
                    )
                )
        elif args.command == "research-register":
            from lob_sim.research.registered_protocol import register_protocol

            result = register_protocol(
                Path(args.days), tuple(Path(p) for p in args.file), load_config(args.env or ".env"), Path(args.out)
            ).snapshot()
        elif args.command == "capture-soak-verify":
            from lob_sim.research.soak_reader import verify_soak

            result = verify_soak(Path(args.bundle))
        elif args.command == "capture-soak":
            import asyncio
            from lob_sim.research.capture_telemetry import SoakSettings
            from lob_sim.research.soak import run_soak

            settings = SoakSettings(
                args.seconds, args.sample_seconds, args.minimum_disk_free_mib * 1024**2, args.maximum_rss_mib * 1024**2
            )
            result = asyncio.run(
                run_soak(
                    load_config(args.env or ".env"),
                    Path(args.out),
                    settings,
                    restart_from=Path(args.restart_from) if args.restart_from else None,
                )
            )
        elif args.command == "research-days-verify":
            result = verify_day_bundle(Path(args.bundle), tuple(Path(p) for p in args.file))
        else:
            cfg = load_config(args.env or ".env")
            cfg = replace(
                cfg,
                symbols=tuple(sorted(args.symbol))
                if args.command == "capture-audit" and args.symbol
                else ("BTCUSDT", "ETHUSDT"),
            )
            result = (
                audit_capture(args.file, args.out, cfg)
                if args.command == "capture-audit"
                else build_day_bundle(tuple(Path(p) for p in args.file), Path(args.out), cfg)
            )
        print(json.dumps(result, sort_keys=True, indent=2))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        parser.error("Evidence operation failed: " + type(exc).__name__ + "; raw and partial evidence is preserved")
    return True
