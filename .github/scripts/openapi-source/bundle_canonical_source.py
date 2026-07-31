"""Build one deterministic OpenAPI bundle from canonical direct-source files."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from source_contract import (
    SourceContractError,
    assert_no_method_path_conflicts,
    build_source_inventory,
    canonical_semantic_bytes,
    iter_source_documents,
    strip_operation_code_samples,
    validate_source_tree,
)

COMPONENT_SECTIONS = (
    "schemas",
    "parameters",
    "securitySchemes",
    "requestBodies",
    "responses",
    "headers",
    "examples",
    "links",
    "callbacks",
)


@dataclass(frozen=True)
class ComponentAssignment:
    source_file: str
    section: str
    original_name: str
    bundled_name: str


def _source_prefix(source_file: str, stem_counts: Counter[str]) -> str:
    path = Path(source_file)
    if stem_counts[path.stem] == 1:
        return path.stem
    without_suffix = path.with_suffix("").as_posix()
    return re.sub(r"[^A-Za-z0-9]+", "_", without_suffix).strip("_")


def _component_claims(
    documents: Mapping[str, Mapping[str, Any]],
) -> dict[tuple[str, str], list[tuple[str, Any]]]:
    claims: dict[tuple[str, str], list[tuple[str, Any]]] = defaultdict(list)
    for source_file, document in documents.items():
        components = document.get("components", {})
        if not isinstance(components, dict):
            raise SourceContractError(f"components must be an object: {source_file}")
        unsupported = sorted(set(components) - set(COMPONENT_SECTIONS))
        if unsupported:
            raise SourceContractError(
                f"unsupported component sections in {source_file}: {', '.join(unsupported)}"
            )
        for section in COMPONENT_SECTIONS:
            entries = components.get(section, {})
            if not isinstance(entries, dict):
                raise SourceContractError(
                    f"components/{section} must be an object: {source_file}"
                )
            for name, value in entries.items():
                claims[(section, name)].append((source_file, value))
    return claims


def _assign_components(
    documents: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[tuple[str, str, str], str], dict[tuple[str, str, str], str]]:
    stem_counts = Counter(Path(source).stem for source in documents)
    claims_by_component = _component_claims(documents)
    reserved_names = set(claims_by_component)
    assignments: dict[tuple[str, str, str], str] = {}
    owners: dict[tuple[str, str, str], str] = {}
    used: dict[tuple[str, str], Any] = {}

    for (section, name), claims in sorted(claims_by_component.items()):
        primary_source, primary_value = claims[0]
        for index, (source_file, value) in enumerate(claims):
            if index == 0 or value == primary_value:
                candidate = name
                owner = primary_source
            else:
                candidate = f"{_source_prefix(source_file, stem_counts)}_{name}"
                owner = source_file
            used_key = (section, candidate)
            if candidate != name and used_key in reserved_names:
                suffix = hashlib.sha256(source_file.encode()).hexdigest()[:10]
                candidate = f"{candidate}_{suffix}"
                used_key = (section, candidate)
            if used_key in used and used[used_key] != value:
                suffix = hashlib.sha256(source_file.encode()).hexdigest()[:10]
                candidate = f"{candidate}_{suffix}"
                used_key = (section, candidate)
            if used_key in used and used[used_key] != value:
                raise SourceContractError(
                    f"component namespace collision: {section}/{candidate}"
                )
            used[used_key] = value
            claim_key = (source_file, section, name)
            assignments[claim_key] = candidate
            owners[claim_key] = owner
    return assignments, owners


def _resolve_ref_source(current_source: str, ref_path: str, source_root: Path) -> str:
    if not ref_path:
        return current_source
    decoded = unquote(ref_path)
    target = (source_root / current_source).parent.joinpath(decoded).resolve()
    try:
        return target.relative_to(source_root.resolve()).as_posix()
    except ValueError as exc:
        raise SourceContractError(
            f"local $ref escapes source root in {current_source}: {ref_path}"
        ) from exc


def _rewrite_refs(
    value: Any,
    *,
    current_source: str,
    source_root: Path,
    assignments: Mapping[tuple[str, str, str], str],
) -> Any:
    if isinstance(value, list):
        return [
            _rewrite_refs(
                item,
                current_source=current_source,
                source_root=source_root,
                assignments=assignments,
            )
            for item in value
        ]
    if not isinstance(value, dict):
        return value

    result: dict[str, Any] = {}
    for key, child in value.items():
        if key == "$ref" and isinstance(child, str):
            parsed = urlsplit(child)
            if parsed.scheme or parsed.netloc:
                raise SourceContractError(
                    f"external $ref cannot be bundled in {current_source}: {child}"
                )
            fragment = unquote(parsed.fragment)
            parts = fragment.split("/")
            if len(parts) >= 4 and parts[:2] == ["", "components"]:
                section, name = parts[2], parts[3]
                target_source = _resolve_ref_source(
                    current_source, parsed.path, source_root
                )
                bundled_name = assignments.get((target_source, section, name))
                if bundled_name is None:
                    raise SourceContractError(
                        f"unresolved component $ref in {current_source}: {child}"
                    )
                suffix = "/".join(parts[4:])
                rewritten = f"#/components/{section}/{bundled_name}"
                result[key] = f"{rewritten}/{suffix}" if suffix else rewritten
            elif parsed.path:
                raise SourceContractError(
                    f"cross-file $ref must target a component in {current_source}: {child}"
                )
            else:
                result[key] = child
        else:
            result[key] = _rewrite_refs(
                child,
                current_source=current_source,
                source_root=source_root,
                assignments=assignments,
            )
    return result


def _merge_unique_mapping(
    target: dict[str, Any], incoming: Mapping[str, Any], *, label: str, source_file: str
) -> None:
    for key, value in incoming.items():
        if key in target and target[key] != value:
            raise SourceContractError(
                f"conflicting {label} {key!r} while merging {source_file}"
            )
        target[key] = value


def _merge_paths(
    target: dict[str, Any], incoming: Mapping[str, Any], *, source_file: str
) -> None:
    for api_path, path_item in incoming.items():
        if api_path not in target:
            target[api_path] = path_item
            continue
        current = target[api_path]
        if not isinstance(current, dict) or not isinstance(path_item, dict):
            raise SourceContractError(
                f"conflicting path {api_path!r} while merging {source_file}"
            )
        _merge_unique_mapping(
            current,
            path_item,
            label=f"path item {api_path}",
            source_file=source_file,
        )


def build_bundle(source_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a deterministic complete bundle and a collision/parity report."""

    source_root = Path(source_root).resolve()
    validate_source_tree(source_root)
    inventory = build_source_inventory(source_root)
    assert_no_method_path_conflicts(inventory)

    documents = {
        source_file: strip_operation_code_samples(document)
        for source_file, document in iter_source_documents(source_root)
    }
    if not documents:
        raise SourceContractError("canonical source tree contains no documents")

    assignments, component_owners = _assign_components(documents)
    first_source = next(iter(documents))
    first = documents[first_source]
    bundle = {
        key: copy.deepcopy(value)
        for key, value in first.items()
        if key not in {"tags", "paths", "components", "webhooks"}
    }
    bundle.update({"tags": [], "paths": {}, "components": {}, "webhooks": {}})
    for section in COMPONENT_SECTIONS:
        bundle["components"][section] = {}

    collision_report: list[dict[str, str]] = []
    for source_file, document in documents.items():
        tags = document.get("tags", [])
        if not isinstance(tags, list):
            raise SourceContractError(f"tags must be an array: {source_file}")
        for tag in tags:
            if tag not in bundle["tags"]:
                bundle["tags"].append(copy.deepcopy(tag))

        paths = _rewrite_refs(
            document.get("paths", {}),
            current_source=source_file,
            source_root=source_root,
            assignments=assignments,
        )
        webhooks = _rewrite_refs(
            document.get("webhooks", {}),
            current_source=source_file,
            source_root=source_root,
            assignments=assignments,
        )
        if not isinstance(paths, dict) or not isinstance(webhooks, dict):
            raise SourceContractError(f"paths/webhooks must be objects: {source_file}")
        _merge_paths(bundle["paths"], paths, source_file=source_file)
        _merge_unique_mapping(
            bundle["webhooks"], webhooks, label="webhook", source_file=source_file
        )

        components = document.get("components", {})
        for section in COMPONENT_SECTIONS:
            entries = components.get(section, {})
            for original_name, value in entries.items():
                claim_key = (source_file, section, original_name)
                bundled_name = assignments[claim_key]
                rewritten_value = _rewrite_refs(
                    value,
                    current_source=component_owners[claim_key],
                    source_root=source_root,
                    assignments=assignments,
                )
                _merge_unique_mapping(
                    bundle["components"][section],
                    {bundled_name: rewritten_value},
                    label=f"component {section}",
                    source_file=source_file,
                )
                if bundled_name != original_name:
                    collision_report.append(
                        {
                            "source_file": source_file,
                            "section": section,
                            "original_name": original_name,
                            "bundled_name": bundled_name,
                        }
                    )

    bundle["tags"] = sorted(bundle["tags"], key=lambda tag: tag.get("name", ""))
    bundle["paths"] = dict(sorted(bundle["paths"].items()))
    for path, path_item in bundle["paths"].items():
        if isinstance(path_item, dict):
            bundle["paths"][path] = dict(sorted(path_item.items()))
    bundle["webhooks"] = dict(sorted(bundle["webhooks"].items()))
    bundle["components"] = {
        section: dict(sorted(entries.items()))
        for section, entries in sorted(bundle["components"].items())
    }

    operation_count = sum(
        1
        for identity in inventory.operations
        if not identity.path.startswith("webhook:")
    )
    webhook_count = len(inventory.operations) - operation_count
    report = {
        "source_file_count": len(documents),
        "operation_count": operation_count,
        "webhook_operation_count": webhook_count,
        "component_collision_count": len(collision_report),
        "component_collisions": sorted(
            collision_report,
            key=lambda item: (
                item["section"],
                item["original_name"],
                item["source_file"],
            ),
        ),
        "sdk_bundle_sha256": hashlib.sha256(
            canonical_semantic_bytes(bundle)
        ).hexdigest(),
    }
    return bundle, report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    bundle, report = build_bundle(args.source_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(bundle, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
