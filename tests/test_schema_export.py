from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scaffoldscope.errors import ConfigError
from scaffoldscope.schema_export import config_schema_text, export_config_schema


class SchemaExportTests(unittest.TestCase):
    def test_packaged_schema_is_valid_json_and_export_is_idempotent(self) -> None:
        schema = json.loads(config_schema_text())
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIn("variant", schema["$defs"])

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "experiment.schema.json"
            self.assertEqual(export_config_schema(output), output.resolve())
            self.assertEqual(export_config_schema(output), output.resolve())
            output.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "overwrite"):
                export_config_schema(output)

    def test_schema_expresses_runtime_exclusivity_constraints(self) -> None:
        schema = json.loads(config_schema_text())

        self.assertEqual(
            schema["$defs"]["agent"]["not"],
            {
                "required": ["system_prompt", "prompt_file"],
                "properties": {
                    "system_prompt": {"type": "string"},
                    "prompt_file": {"type": "string"},
                },
            },
        )
        self.assertEqual(
            schema["$defs"]["sandbox"]["allOf"],
            [
                {
                    "if": {
                        "required": ["backend"],
                        "properties": {"backend": {"const": "docker"}},
                    },
                    "then": {
                        "required": ["docker"],
                        "properties": {"docker": {"$ref": "#/$defs/docker"}},
                    },
                    "else": {"properties": {"docker": {"type": "null"}}},
                }
            ],
        )
        self.assertTrue(schema["$defs"]["tasks"]["properties"]["ids"]["uniqueItems"])
        self.assertEqual(
            schema["$defs"]["variant"]["properties"]["tools"]["type"],
            ["array", "null"],
        )
        self.assertEqual(
            schema["$defs"]["variant"]["properties"]["instructions"]["type"],
            ["string", "null"],
        )
        model_rule = schema["$defs"]["model"]["allOf"][0]
        self.assertEqual(
            model_rule["if"]["properties"]["provider"],
            {"const": "scripted"},
        )
        self.assertEqual(model_rule["then"]["properties"]["temperature"], {"const": 0})
        self.assertEqual(model_rule["then"]["properties"]["supports_seed"], {"const": False})
        self.assertEqual(
            {item["required"][0] for item in model_rule["then"]["not"]["anyOf"]},
            {
                "base_url",
                "api_key_env",
                "requires_api_key",
                "timeout_seconds",
                "retries",
                "json_mode",
            },
        )
        policy_rules = schema["$defs"]["variant"]["allOf"]
        self.assertEqual(
            [rule["if"]["properties"]["policy"]["const"] for rule in policy_rules],
            ["none", "reactive", "periodic", "selective"],
        )
        self.assertEqual(
            {item["required"][0] for item in policy_rules[0]["then"]["not"]["anyOf"]},
            {
                "trigger_ratio",
                "target_ratio",
                "every_turns",
                "keep_recent_bundles",
                "weights",
                "plugin_options",
            },
        )


if __name__ == "__main__":
    unittest.main()
