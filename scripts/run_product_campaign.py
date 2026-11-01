"""Run a frozen, repeated read-only local HTTP campaign. No model calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT))

from testfix.product_campaign import run_campaign
from testfix.product_http import LocalHTTPAdapter, source_digest


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON fields")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--adapter", choices=["http", "http-load"], default="http")
    parser.add_argument("--scope-only", action="store_true", help="Exit by the declared smoke scope; default requires full product acceptance")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/product-campaigns")
    args = parser.parse_args(argv)
    try:
        raw = args.profile.read_bytes()
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Profile exceeds 2 MiB")
        profile = json.loads(raw, object_pairs_hook=_unique)
        if set(profile) != {"source_files", "contract"}:
            raise ValueError("Profile needs source_files and contract")
        files = profile["source_files"]
        if not isinstance(files, list) or not 1 <= len(files) <= 100:
            raise ValueError("Select 1-100 public source files")
        for name in files:
            if (not isinstance(name, str) or "\\" in name or ":" in name
                    or any(part in {"", ".", "..", ".git"} or part.startswith(".env") for part in name.split("/"))
                    or Path(name).suffix.lower() in {".key", ".pem"}):
                raise ValueError("Use relative public source paths; credential files cannot be archived")
        if sum((ROOT / name).stat().st_size for name in files) > 8 * 1024 * 1024:
            raise ValueError("Selected source snapshot exceeds 8 MiB")
        probe = lambda: source_digest(ROOT, files)
        contract = dict(profile["contract"])
        if "candidate_hash" in contract:
            raise ValueError("The host, rather than the profile, binds the source digest")
        contract["candidate_hash"] = probe()
        if args.adapter == "http-load":
            from testfix.product_http_load import LocalHTTPLoadAdapter
            adapter = LocalHTTPLoadAdapter(args.base_url)
        else:
            adapter = LocalHTTPAdapter(args.base_url)
        report = run_campaign(contract, adapter, output_dir=args.output, candidate_probe=probe)
        evidence = Path(report["evidence_dir"])
        # Preserve only explicitly selected local source files. Never imply they
        # identify the running server, image, complete environment or database.
        snapshot_verified = False
        if probe() == contract["candidate_hash"]:
            snapshot = evidence / "source-snapshot"
            snapshot.mkdir(exist_ok=False)
            for name in files:
                origin = (ROOT / name).resolve()
                destination = snapshot / origin.relative_to(ROOT)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, destination)
            snapshot_verified = source_digest(snapshot, files) == contract["candidate_hash"]
        context = {"base_url": args.base_url, "adapter": args.adapter, "source_files": files,
                   "exit_gate": "tested_scope_passed" if args.scope_only else "product_accepted",
                   "source_snapshot_verified": snapshot_verified,
                   "binding": "selected_disk_files_only; running process/image/database identity not established",
                   "model_calls": 0, "hardware_actions": 0}
        (evidence / "adapter-context.json").write_text(json.dumps(context, indent=2), encoding="utf-8")
        print(json.dumps({key: report[key] for key in ("run_id", "tested_scope_passed", "product_accepted", "counts", "coverage", "duration_s", "acceptance_blockers", "evidence_dir")}, ensure_ascii=False, indent=2))
        return 0 if report["tested_scope_passed" if args.scope_only else "product_accepted"] else 1
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(f"Campaign could not start: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
