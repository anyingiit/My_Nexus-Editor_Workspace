import json
from pathlib import Path
import unittest

import yaml

from agents_v3.schema_gate import assert_schema, validate_instance


ROOT = Path(__file__).resolve().parents[1]


class SchemaGateTests(unittest.TestCase):
    def schema(self, name: str) -> dict:
        return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))

    def test_published_config_examples_match_published_schemas(self) -> None:
        pairs = [
            ("config/run_config.example.yaml", "run_config.schema.json"),
            ("config/evaluation_profile.example.yaml", "evaluation_profile.schema.json"),
            ("config/runtime_capability_report.example.yaml", "runtime_capability_report.schema.json"),
            ("sandbox/capability_manifest.example.json", "capability_manifest.schema.json"),
        ]
        for instance_name, schema_name in pairs:
            path = ROOT / instance_name
            instance = (
                json.loads(path.read_text(encoding="utf-8"))
                if path.suffix == ".json"
                else yaml.safe_load(path.read_text(encoding="utf-8"))
            )
            findings = validate_instance(instance, self.schema(schema_name))
            self.assertEqual(findings, (), f"{instance_name}: {findings}")

    def test_additional_property_is_rejected(self) -> None:
        instance = yaml.safe_load(
            (ROOT / "config" / "run_config.example.yaml").read_text(encoding="utf-8")
        )
        instance["unreviewed_bypass"] = True
        findings = validate_instance(instance, self.schema("run_config.schema.json"))
        self.assertIn("additionalProperties", {item.keyword for item in findings})

    def test_external_ref_is_fail_closed(self) -> None:
        findings = validate_instance({}, {"$ref": "https://example.invalid/schema"})
        self.assertIn("$ref", {item.keyword for item in findings})


if __name__ == "__main__":
    unittest.main()
