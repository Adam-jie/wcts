"""Prepare local complete-repository snapshots; never execute target code."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from testfix.dataset_cases import prepare_dataset_case


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Specification contains duplicate JSON fields")
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError("Specification cannot contain non-finite JSON values")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True, help="Explicit testfix.dataset-case.v1 JSON specification")
    parser.add_argument("--source", type=Path, required=True, help="Existing local full upstream repository; read only")
    parser.add_argument("--output", type=Path, required=True, help="Fresh case directory; never overwrite an existing path")
    args = parser.parse_args(argv)
    try:
        with args.spec.open("rb") as stream:
            raw = stream.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("Specification exceeds its 4 MiB size limit")
        spec = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_nonfinite)
        result = prepare_dataset_case(spec, args.source, args.output)
    except (ValueError, OSError, TypeError) as exc:
        print(f"Dataset preparation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
