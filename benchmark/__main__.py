"""Opt-in commands; importing the benchmark does not execute a study."""

import argparse
import json
import sys
from pathlib import Path

from .analysis import analyze
from .archive import convert_iau_archive
from .design import plan
from .execution import execute
from .pipeline import collect, evaluate, predict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Historical, leakage-controlled imagery benchmark")
    sub = parser.add_subparsers(dest="command", required=True)
    planning = sub.add_parser("plan", help="Freeze inputs and sampling; no propagation/network")
    planning.add_argument("--config", required=True, type=Path)
    planning.add_argument("--tle-archive", required=True, type=Path)
    planning.add_argument("--out", required=True, type=Path)
    importing = sub.add_parser(
        "import-iau", help="Validate saved, timestamped IAU archive responses"
    )
    importing.add_argument("--input", required=True, type=Path)
    importing.add_argument("--out", required=True, type=Path)
    running = sub.add_parser("run", help="Execute all study stages with local workers")
    running.add_argument("--run", required=True, type=Path)
    running.add_argument("--predict-workers", type=int, default=8)
    running.add_argument("--collect-workers", type=int, default=4)
    running.add_argument("--allow-network", action="store_true")
    for name in ("predict", "collect", "evaluate", "analyze"):
        command = sub.add_parser(name)
        command.add_argument("--run", required=True, type=Path)
        if name in {"predict", "collect"}:
            command.add_argument("--limit", type=int, help="Process at most N outstanding cases")
            command.add_argument("--retry-errors", action="store_true")
        if name == "collect":
            command.add_argument(
                "--allow-network",
                action="store_true",
                help="Required for live catalog/asset requests",
            )
    args = parser.parse_args(argv)
    try:
        if getattr(args, "limit", None) is not None and args.limit <= 0:
            raise ValueError("--limit must be positive.")
        if args.command == "import-iau":
            result = convert_iau_archive(args.input, args.out)
        elif args.command == "run":
            if not args.allow_network or min(args.predict_workers, args.collect_workers) < 1:
                raise ValueError("run requires --allow-network and positive worker counts.")
            result = execute(args.run, args.predict_workers, args.collect_workers)
        elif args.command == "plan":
            manifest = plan(args.config, args.tle_archive, args.out)
            result = {
                "study_id": manifest["study_id"],
                "n_sites": len(manifest["sites"]),
                "n_cases": len(manifest["sites"]) * len(manifest["snapshots"]),
                "missing_tle_snapshots": sum(s["element"] is None for s in manifest["snapshots"]),
            }
        elif args.command == "predict":
            result = predict(args.run, args.limit, args.retry_errors)
        elif args.command == "collect":
            if not args.allow_network:
                raise ValueError("collect requires --allow-network.")
            result = collect(args.run, args.limit, args.retry_errors)
        elif args.command == "evaluate":
            data = evaluate(args.run)
            result = {
                "n_predictions": len(data["predictions"]),
                "n_observations": len(data["observations"]),
            }
        else:
            report = analyze(args.run)
            result = {
                "n_groups": len(report["groups"]),
                "n_validated_candidates": sum(
                    c["status"] != "withheld" for c in report["uncertainty_candidates"]
                ),
            }
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 2
    print(json.dumps({"status": "ok", **result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
