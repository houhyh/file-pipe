from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import initialize, load
from .pipeline import Pipeline, STAGES
from .storage import clean_error


def parser():
    p = argparse.ArgumentParser(prog="office-synth", description="Evidence-grounded office workspace synthesis")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create runnable configuration without overwriting files")
    init.add_argument("--output", type=Path, required=True)
    for name in STAGES + ["run"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--config", required=True, type=Path)
        cmd.add_argument("--dry-run", action="store_true", help="Print stage graph and budgets; no writes or requests")
        cmd.add_argument("--limit", type=int)
        cmd.add_argument("--seed-id")
        cmd.add_argument("--workspace-id")
        cmd.add_argument("--resume", action="store_true", default=True)
        cmd.add_argument("--force", action="store_true", help="Recompute selected stage objects; downstream invalidated by fingerprints")
        if name in {"run", "collect"}:
            cmd.add_argument("--urls", type=Path, help="JSONL strings or objects containing url")
    report = sub.add_parser("report")
    report.add_argument("--run-dir", type=Path, required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    pipeline = None
    try:
        if args.command == "init":
            initialize(args.output)
            print(f"Configuration created: {args.output}")
            return 0
        if args.command == "report":
            from .storage import read_json
            print(json.dumps(read_json(args.run_dir / "report.json"), indent=2, ensure_ascii=False))
            return 0
        config = load(args.config)
        if args.limit is not None and args.limit < 1:
            raise ValueError("--limit must be positive")
        urls = None
        if getattr(args, "urls", None):
            urls = []
            for line in args.urls.read_text().splitlines():
                if line.strip():
                    value = json.loads(line)
                    urls.append(value if isinstance(value, str) else value["url"])
            if urls:
                config["run"]["fixture"] = False
        if args.dry_run:
            print(json.dumps({"command": args.command, "stages": STAGES, "stage_dependencies": list(zip(STAGES[:-1], STAGES[1:])),
                              "run_dir": config["_root"], "fixture": config["run"]["fixture"], "budget": config["budget"],
                              "target_seeds": config["sampling"]["target_seeds"], "instances_per_seed": config["sampling"]["instances_per_seed"],
                              "writes": False, "network": False}, indent=2))
            return 0
        pipeline = Pipeline(config, resume=args.resume, force=args.force, limit=args.limit,
                            seed_id=args.seed_id, workspace_id=args.workspace_id, urls=urls)
        with pipeline.store.lock():
            def invoke():
                getattr(pipeline, args.command.replace("-", "_"))()
                return []
            try:
                pipeline.store.execute("command", args.command, {"config": config, "seed_id": args.seed_id, "workspace_id": args.workspace_id}, invoke, resume=False)
            finally:
                report = pipeline.report()
        print(json.dumps({"run_dir": str(pipeline.root), "accepted_seeds": report["accepted_seeds"],
                          "workspaces": len(report["workspaces"]), "consumption": report["consumption"]}, ensure_ascii=False))
        if args.command in {"run", "validate", "repair", "export"} and any(w["status"] == "rejected" for w in report["workspaces"]):
            return 2
        return 0
    except KeyboardInterrupt:
        print("Interrupted; completed stages are persisted. Use --resume.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"{type(exc).__name__}: {clean_error(exc)}", file=sys.stderr)
        return 1
    finally:
        if pipeline:
            pipeline.store.close()


if __name__ == "__main__":
    sys.exit(main())
