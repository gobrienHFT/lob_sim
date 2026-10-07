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


def dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> bool:
    if args.command not in {
        "capture-audit",
        "research-days",
        "research-days-verify",
        "capture-soak",
        "capture-soak-verify",
        "research-register",
    }:
        return False
    from lob_sim.research.capture_audit import audit_capture
    from lob_sim.research.day_bundle import build_day_bundle, verify_day_bundle

    try:
        if args.command == "research-register":
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
