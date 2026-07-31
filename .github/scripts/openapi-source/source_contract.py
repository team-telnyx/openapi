"""Deterministic contracts for canonical OpenAPI source trees."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)


class SourceContractError(ValueError):
    """Canonical source violates a safety or identity invariant."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that refuses silent mapping-key overwrites."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise SourceContractError(f"unhashable mapping key: {key!r}") from exc
        if duplicate:
            raise SourceContractError(f"duplicate mapping key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)

# OpenAPI uses JSON's scalar model even when serialized as YAML. PyYAML's YAML
# 1.1 resolver otherwise turns values such as ``yes`` and dates into Python-only
# types and response-code keys into integers. Restrict implicit resolution to
# JSON-compatible scalar syntax.
for first_character, resolvers in tuple(
    _UniqueKeyLoader.yaml_implicit_resolvers.items()
):
    _UniqueKeyLoader.yaml_implicit_resolvers[first_character] = [
        (tag, expression)
        for tag, expression in resolvers
        if tag
        not in {
            "tag:yaml.org,2002:bool",
            "tag:yaml.org,2002:float",
            "tag:yaml.org,2002:int",
            "tag:yaml.org,2002:timestamp",
        }
    ]

_UniqueKeyLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf")
)
_UniqueKeyLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    re.compile(r"^-?(?:0|[1-9][0-9]*)$"),
    list("-0123456789"),
)
_UniqueKeyLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(
        r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)$"
        r"|^-?(?:0|[1-9][0-9]*)\.[0-9]+$"
    ),
    list("-0123456789"),
)


@dataclass(frozen=True, order=True)
class OperationIdentity:
    source_file: str
    path: str
    method: str
    operation_id: str | None


@dataclass(frozen=True)
class SourceInventory:
    operations: tuple[OperationIdentity, ...]
    duplicate_operation_ids: dict[str, tuple[OperationIdentity, ...]]
    method_path_conflicts: dict[tuple[str, str], tuple[OperationIdentity, ...]]


def _walk_refs(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref" and isinstance(child, str):
                yield child
            yield from _walk_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_refs(child)


def strip_operation_code_samples(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep copy without operation-level ``x-codeSamples`` fields."""

    normalized = copy.deepcopy(document)
    paths = normalized.get("paths")
    if not isinstance(paths, dict):
        return normalized

    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() in HTTP_METHODS and isinstance(operation, dict):
                operation.pop("x-codeSamples", None)

    return normalized


def canonical_semantic_bytes(document: Mapping[str, Any]) -> bytes:
    """Serialize parsed OpenAPI deterministically for semantic checksums."""

    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _source_files(root: Path) -> Iterator[Path]:
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        if path.is_symlink():
            raise SourceContractError(
                f"symbolic link is not allowed in source tree: {path}"
            )
        if path.is_file() and path.suffix.lower() in {".json", ".yaml", ".yml"}:
            yield path


def _update_framed(digest: Any, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, byteorder="big"))
    digest.update(value)


def raw_tree_sha256(root: Path) -> str:
    """Hash supported source paths and their exact bytes deterministically."""

    root = Path(root).resolve()
    digest = hashlib.sha256()
    for path in _source_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        _update_framed(digest, relative)
        _update_framed(digest, path.read_bytes())
    return digest.hexdigest()


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key, value in pairs:
        if key in mapping:
            raise SourceContractError(f"duplicate mapping key: {key!r}")
        mapping[key] = value
    return mapping


def _reject_json_constant(value: str) -> None:
    raise SourceContractError(f"invalid JSON constant: {value}")


def _load_document(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix.lower() == ".json":
            document = json.loads(
                text,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
        else:
            document = yaml.load(text, Loader=_UniqueKeyLoader)
    except SourceContractError as exc:
        raise SourceContractError(f"{exc} in {path}") from exc
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SourceContractError(f"invalid YAML/JSON in {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise SourceContractError(f"OpenAPI source must be an object: {path}")
    return _stringify_mapping_keys(document, path)


def _stringify_mapping_keys(value: Any, path: Path) -> Any:
    """Normalize YAML mapping keys to JSON object keys without hiding collisions."""

    if isinstance(value, list):
        return [_stringify_mapping_keys(item, path) for item in value]
    if not isinstance(value, dict):
        return value

    normalized: dict[str, Any] = {}
    for key, child in value.items():
        if not isinstance(key, (str, int, float, bool)) and key is not None:
            raise SourceContractError(
                f"mapping key is not JSON-compatible in {path}: {key!r}"
            )
        normalized_key = str(key).lower() if isinstance(key, bool) else str(key)
        if normalized_key in normalized:
            raise SourceContractError(
                f"duplicate mapping key after JSON normalization in {path}: "
                f"{normalized_key!r}"
            )
        normalized[normalized_key] = _stringify_mapping_keys(child, path)
    return normalized


def load_document(path: Path) -> dict[str, Any]:
    """Safely parse one JSON or YAML OpenAPI document."""

    return _load_document(Path(path))


def source_files(root: Path) -> tuple[Path, ...]:
    """Return supported source files in stable source-relative order."""

    root = Path(root).resolve()
    return tuple(_source_files(root))


def iter_source_documents(root: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield source-relative paths and safely parsed OpenAPI documents."""

    root = Path(root).resolve()
    for path in _source_files(root):
        yield path.relative_to(root).as_posix(), _load_document(path)


def build_source_inventory(root: Path) -> SourceInventory:
    """Inventory stable source-file/path/method operation identities."""

    root = Path(root).resolve()
    operations: list[OperationIdentity] = []
    by_operation_id: dict[str, list[OperationIdentity]] = defaultdict(list)
    by_method_path: dict[tuple[str, str], list[OperationIdentity]] = defaultdict(list)

    for source_path in _source_files(root):
        document = _load_document(source_path)
        paths = document.get("paths", {})
        if not isinstance(paths, dict):
            raise SourceContractError(f"paths must be an object: {source_path}")
        for api_path, path_item in paths.items():
            if not isinstance(api_path, str) or not isinstance(path_item, dict):
                raise SourceContractError(
                    f"invalid path item in {source_path}: {api_path!r}"
                )
            for method, operation in path_item.items():
                normalized_method = str(method).lower()
                if normalized_method not in HTTP_METHODS:
                    continue
                if not isinstance(operation, dict):
                    raise SourceContractError(
                        f"operation must be an object in {source_path}: "
                        f"{normalized_method.upper()} {api_path}"
                    )
                operation_id_value = operation.get("operationId")
                operation_id = (
                    operation_id_value
                    if isinstance(operation_id_value, str) and operation_id_value
                    else None
                )
                identity = OperationIdentity(
                    source_file=source_path.relative_to(root).as_posix(),
                    path=api_path,
                    method=normalized_method,
                    operation_id=operation_id,
                )
                operations.append(identity)
                by_method_path[(api_path, normalized_method)].append(identity)
                if operation_id is not None:
                    by_operation_id[operation_id].append(identity)

        webhooks = document.get("webhooks", {})
        if not isinstance(webhooks, dict):
            raise SourceContractError(f"webhooks must be an object: {source_path}")
        for webhook_name, path_item in webhooks.items():
            if not isinstance(webhook_name, str) or not isinstance(path_item, dict):
                raise SourceContractError(
                    f"invalid webhook item in {source_path}: {webhook_name!r}"
                )
            for method, operation in path_item.items():
                normalized_method = str(method).lower()
                if normalized_method not in HTTP_METHODS:
                    continue
                if not isinstance(operation, dict):
                    raise SourceContractError(
                        f"webhook operation must be an object in {source_path}: "
                        f"{normalized_method.upper()} {webhook_name}"
                    )
                operation_id_value = operation.get("operationId")
                operation_id = (
                    operation_id_value
                    if isinstance(operation_id_value, str) and operation_id_value
                    else None
                )
                identity = OperationIdentity(
                    source_file=source_path.relative_to(root).as_posix(),
                    path=f"webhook:{webhook_name}",
                    method=normalized_method,
                    operation_id=operation_id,
                )
                operations.append(identity)
                if operation_id is not None:
                    by_operation_id[operation_id].append(identity)

    sorted_operations = tuple(sorted(operations))
    duplicate_operation_ids = {
        operation_id: tuple(sorted(matches))
        for operation_id, matches in sorted(by_operation_id.items())
        if len(matches) > 1
    }
    method_path_conflicts = {
        identity: tuple(sorted(matches))
        for identity, matches in sorted(by_method_path.items())
        if len({match.source_file for match in matches}) > 1
    }
    return SourceInventory(
        operations=sorted_operations,
        duplicate_operation_ids=duplicate_operation_ids,
        method_path_conflicts=method_path_conflicts,
    )


def assert_no_method_path_conflicts(inventory: SourceInventory) -> None:
    """Fail when multiple canonical source files claim the same HTTP operation."""

    if not inventory.method_path_conflicts:
        return
    details = []
    for (api_path, method), matches in inventory.method_path_conflicts.items():
        sources = ", ".join(match.source_file for match in matches)
        details.append(f"{method.upper()} {api_path} ({sources})")
    raise SourceContractError("cross-file method/path conflicts: " + "; ".join(details))


def validate_source_tree(root: Path) -> None:
    """Reject local references that can escape the canonical source root."""

    root = Path(root).resolve()
    for source_path in _source_files(root):
        document = _load_document(source_path)
        for ref in _walk_refs(document):
            parsed = urlsplit(ref)
            if parsed.scheme or parsed.netloc:
                raise SourceContractError(
                    f"external $ref escapes immutable source epoch in {source_path}: {ref}"
                )
            if not parsed.path:
                continue
            decoded_path = unquote(parsed.path)
            if "\\" in decoded_path:
                raise SourceContractError(
                    f"local $ref uses unsafe path separators in {source_path}: {ref}"
                )
            candidate = (source_path.parent / decoded_path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise SourceContractError(
                    f"local $ref escapes source root in {source_path}: {ref}"
                ) from exc
            if not candidate.is_file():
                raise SourceContractError(
                    f"local $ref target does not exist in {source_path}: {ref}"
                )


def semantic_tree_sha256(root: Path, *, strip_samples: bool) -> str:
    """Hash parsed source semantics while retaining each relative source path."""

    root = Path(root).resolve()
    digest = hashlib.sha256()
    for path in _source_files(root):
        document = _load_document(path)
        if strip_samples:
            document = strip_operation_code_samples(document)
        _update_framed(digest, path.relative_to(root).as_posix().encode("utf-8"))
        _update_framed(digest, canonical_semantic_bytes(document))
    return digest.hexdigest()


def normalized_semantic_sha256(document: Mapping[str, Any]) -> str:
    """Hash OpenAPI semantics after removing CI-owned code samples."""

    normalized = strip_operation_code_samples(document)
    return hashlib.sha256(canonical_semantic_bytes(normalized)).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    checksum = subparsers.add_parser("checksum")
    checksum.add_argument("root", type=Path)
    checksum.add_argument("--strip-samples", action="store_true")
    args = parser.parse_args()
    if args.command == "checksum":
        print(semantic_tree_sha256(args.root, strip_samples=args.strip_samples))
        return 0
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
