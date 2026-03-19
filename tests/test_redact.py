from __future__ import annotations

import unittest

from scaffoldscope.redact import redact, redact_text


class RedactionTests(unittest.TestCase):
    def test_common_tokens_and_sensitive_mapping_keys_are_redacted(self) -> None:
        value = {
            "Authorization": "Bearer arbitrary-token-value",
            "nested": {"api-key": "custom-secret"},
            "message": "use Bearer abcdefghijklmnop for auth",
        }

        redacted = redact(value)

        self.assertEqual(redacted["Authorization"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["api-key"], "[REDACTED]")
        self.assertNotIn("abcdefghijklmnop", redacted["message"])

    def test_non_secret_text_is_unchanged(self) -> None:
        self.assertEqual(redact_text("ordinary benchmark output"), "ordinary benchmark output")

    def test_tokens_inside_json_escaped_observations_are_redacted(self) -> None:
        serialized = r'{"content":"ok\nBearer abcdefghijklmnop\nsk-abcdefghijklmnop\n"}'

        redacted = redact_text(serialized)

        self.assertNotIn("abcdefghijklmnop", redacted)
        self.assertEqual(redacted.count("[REDACTED]"), 2)

    def test_operator_identifiers_do_not_destroy_typed_metrics(self) -> None:
        value = {
            "lexical_constraint_availability": {"api-key": True},
            "constraint_checks": {"secret": False},
            "constraint_details": {"password": {"actual": "Bearer abcdefghijklmnop"}},
        }

        redacted = redact(value)

        self.assertIs(redacted["lexical_constraint_availability"]["api-key"], True)
        self.assertIs(redacted["constraint_checks"]["secret"], False)
        self.assertEqual(
            redacted["constraint_details"]["password"]["actual"],
            "Bearer [REDACTED]",
        )


if __name__ == "__main__":
    unittest.main()
