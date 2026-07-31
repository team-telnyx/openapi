"""Build the public normalized Telnyx OpenAPI mirror."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from bundle_canonical_source import build_bundle
from source_contract import HTTP_METHODS, canonical_semantic_bytes


def strip_hidden_operations(bundle: dict[str, Any]) -> tuple[dict[str, Any], int]:
    public = copy.deepcopy(bundle)
    removed = 0
    paths = public.get("paths") or {}
    for api_path in list(paths):
        path_item = paths[api_path]
        if not isinstance(path_item, dict):
            continue
        for method in list(path_item):
            operation = path_item[method]
            if (
                str(method).lower() in HTTP_METHODS
                and isinstance(operation, dict)
                and operation.get("x-hidden") is True
            ):
                del path_item[method]
                removed += 1
        if not any(str(key).lower() in HTTP_METHODS for key in path_item):
            del paths[api_path]
    return public, removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--yaml-output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    bundle, bundle_report = build_bundle(args.source_root)
    public, removed = strip_hidden_operations(bundle)
    json_bytes = json.dumps(public, indent=2, ensure_ascii=True).encode() + b"\n"
    yaml_text = yaml.safe_dump(public, sort_keys=False, allow_unicode=True)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.yaml_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_bytes(json_bytes)
    args.yaml_output.write_text(yaml_text, encoding="utf-8")
    report = {
        "schema_version": 1,
        "source_file_count": bundle_report["source_file_count"],
        "hidden_operation_count": removed,
        "public_semantic_sha256": hashlib.sha256(
            canonical_semantic_bytes(public)
        ).hexdigest(),
        "public_json_sha256": hashlib.sha256(json_bytes).hexdigest(),
    }
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
