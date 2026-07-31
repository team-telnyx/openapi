import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from bundle_canonical_source import build_bundle
from source_contract import SourceContractError


def spec(title, path, operation_id, schema):
    return {
        "openapi": "3.1.0",
        "info": {"title": title, "version": "1.0.0"},
        "paths": {
            path: {
                "get": {
                    "operationId": operation_id,
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Resource"}
                                }
                            },
                        }
                    },
                    "x-codeSamples": [{"lang": "Python", "source": "ignored"}],
                }
            }
        },
        "components": {"schemas": {"Resource": schema}},
    }


class CanonicalBundleTest(unittest.TestCase):
    def test_rewrites_conflicting_component_refs_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.json").write_text(
                json.dumps(spec("A", "/a", "getA", {"type": "string"}))
            )
            (root / "b.json").write_text(
                json.dumps(spec("B", "/b", "getB", {"type": "integer"}))
            )

            bundle, report = build_bundle(root)

        self.assertEqual(bundle["components"]["schemas"]["Resource"]["type"], "string")
        self.assertEqual(
            bundle["components"]["schemas"]["b_Resource"]["type"], "integer"
        )
        self.assertEqual(
            bundle["paths"]["/a"]["get"]["responses"]["200"]["content"][
                "application/json"
            ]["schema"]["$ref"],
            "#/components/schemas/Resource",
        )
        self.assertEqual(
            bundle["paths"]["/b"]["get"]["responses"]["200"]["content"][
                "application/json"
            ]["schema"]["$ref"],
            "#/components/schemas/b_Resource",
        )
        self.assertNotIn("x-codeSamples", bundle["paths"]["/a"]["get"])
        self.assertEqual(report["component_collision_count"], 1)

    def test_identical_components_share_the_unqualified_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schema = {"type": "string"}
            (root / "a.json").write_text(json.dumps(spec("A", "/a", "getA", schema)))
            (root / "b.json").write_text(json.dumps(spec("B", "/b", "getB", schema)))

            bundle, report = build_bundle(root)

        self.assertEqual(list(bundle["components"]["schemas"]), ["Resource"])
        self.assertEqual(report["component_collision_count"], 0)

    def test_duplicate_file_stems_receive_stable_path_prefixes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "first").mkdir()
            (root / "second").mkdir()
            (root / "first" / "api.json").write_text(
                json.dumps(spec("A", "/a", "getA", {"type": "string"}))
            )
            (root / "second" / "api.json").write_text(
                json.dumps(spec("B", "/b", "getB", {"type": "integer"}))
            )

            bundle, _ = build_bundle(root)

        self.assertIn("second_api_Resource", bundle["components"]["schemas"])

    def test_cross_file_component_ref_resolves_to_target_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "models").mkdir()
            target = spec("Target", "/target", "getTarget", {"type": "string"})
            source = spec("Source", "/source", "getSource", {"type": "integer"})
            source["paths"]["/source"]["get"]["responses"]["200"]["content"][
                "application/json"
            ]["schema"]["$ref"] = "models/target.json#/components/schemas/Resource"
            (root / "models" / "target.json").write_text(json.dumps(target))
            (root / "source.json").write_text(json.dumps(source))

            bundle, _ = build_bundle(root)

        ref = bundle["paths"]["/source"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        self.assertEqual(ref, "#/components/schemas/Resource")

    def test_method_path_conflict_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.json").write_text(
                json.dumps(spec("A", "/same", "getA", {"type": "string"}))
            )
            (root / "b.json").write_text(
                json.dumps(spec("B", "/same", "getB", {"type": "integer"}))
            )

            with self.assertRaisesRegex(SourceContractError, "method/path conflicts"):
                build_bundle(root)

    def test_output_is_independent_of_temporary_root(self):
        documents = [
            ("z.json", spec("Z", "/z", "getZ", {"type": "integer"})),
            ("a.json", spec("A", "/a", "getA", {"type": "string"})),
        ]
        with (
            tempfile.TemporaryDirectory() as first,
            tempfile.TemporaryDirectory() as second,
        ):
            first_root, second_root = Path(first), Path(second)
            for name, document in documents:
                (first_root / name).write_text(json.dumps(document))
            for name, document in reversed(documents):
                (second_root / name).write_text(json.dumps(document))

            first_bundle, first_report = build_bundle(first_root)
            second_bundle, second_report = build_bundle(second_root)

        self.assertEqual(first_bundle, second_bundle)
        self.assertEqual(first_report, second_report)


if __name__ == "__main__":
    unittest.main()
