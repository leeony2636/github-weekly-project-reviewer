import unittest

from reviewer.security import (
    SensitiveContentError,
    assert_safe_for_external_transfer,
    find_sensitive_content,
)


class SecurityTests(unittest.TestCase):
    def test_allows_normal_source_code(self) -> None:
        files = [
            {
                "filename": "src/example.py",
                "patch": (
                    "@@ -1 +1 @@\n"
                    "+timeout_seconds = 30"
                ),
            }
        ]

        self.assertEqual(
            find_sensitive_content(files),
            [],
        )

    def test_allows_environment_variable_lookup(
        self,
    ) -> None:
        files = [
            {
                "filename": "src/config.py",
                "patch": (
                    '@@ -1 +1 @@\n'
                    '+api_key = os.environ["API_KEY"]'
                ),
            }
        ]

        self.assertEqual(
            find_sensitive_content(files),
            [],
        )

    def test_blocks_private_key(self) -> None:
        files = [
            {
                "filename": "secret.txt",
                "patch": (
                    "+-----BEGIN PRIVATE KEY-----"
                ),
            }
        ]

        locations = find_sensitive_content(files)

        self.assertEqual(len(locations), 1)
        self.assertEqual(
            locations[0].kind,
            "private_key",
        )

    def test_blocks_email_address(self) -> None:
        files = [
            {
                "filename": "src/example.py",
                "patch": "+contact = 'user@example.com'",
            }
        ]

        locations = find_sensitive_content(files)

        self.assertEqual(len(locations), 1)
        self.assertEqual(
            locations[0].kind,
            "email_address",
        )

    def test_blocks_environment_file(self) -> None:
        files = [
            {
                "filename": ".env.production",
                "patch": "+DEBUG=false",
            }
        ]

        locations = find_sensitive_content(files)

        self.assertTrue(
            any(
                item.kind == "environment_file"
                for item in locations
            )
        )

    def test_allows_empty_env_example(self) -> None:
        files = [
            {
                "filename": ".env.example",
                "patch": (
                    "+OPENAI_API_KEY=\n"
                    "+GEMINI_API_KEY="
                ),
            }
        ]

        self.assertEqual(
            find_sensitive_content(files),
            [],
        )

    def test_exception_does_not_expose_secret(
        self,
    ) -> None:
        secret_value = "ghp_abcdefghijklmnopqrstuvwxyz1234"

        files = [
            {
                "filename": "src/example.py",
                "patch": f"+token = '{secret_value}'",
            }
        ]

        with self.assertRaises(
            SensitiveContentError
        ) as context:
            assert_safe_for_external_transfer(files)

        self.assertNotIn(
            secret_value,
            str(context.exception),
        )


if __name__ == "__main__":
    unittest.main()