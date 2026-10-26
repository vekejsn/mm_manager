"""Configuration parser checks, invoked by CTest with the config probe binary."""
import pathlib
import subprocess
import sys
import tempfile
import unittest

PROBE = str(pathlib.Path(sys.argv.pop(1)).resolve())


class Configuration(unittest.TestCase):
    def run_config(self, text=None, *args, filename=".env"):
        with tempfile.TemporaryDirectory() as directory:
            if text is not None:
                pathlib.Path(directory, filename).write_text(text)
            return subprocess.run([PROBE, *args], cwd=directory, text=True,
                                  capture_output=True)

    def test_absent_file_preserves_cli_and_default_profiles(self):
        result = self.run_config(None, "-vv", "-n", "1234")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["-vv", "-n", "1234",
                         "06CNB02:default", "12AAV08:default", "UNKNOWN:default"])

    def test_quotes_comments_flags_and_cli_precedence(self):
        result = self.run_config('''# settings
export MM_MODEM_INIT="AT X # literal" # comment
MM_DEFAULT_TABLE_DIR='tables with spaces'
MM_NCC_NUMBER=1234
MM_SECONDARY_NCC_NUMBER=5678
MM_VERBOSE=3
MM_QUIET=false
MM_COMPLETE_DOWNLOAD=true
UNRELATED=value
''', "-vn9999", "-d", "override")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = result.stdout.splitlines()[:-3]
        self.assertEqual(args, ["-c", "-i", "AT X # literal", "-vn9999", "-d", "override"])

    def test_explicit_file_and_multiple_firmware_profiles(self):
        result = self.run_config("MM_TABLES_06CNB02=21, 0x16, 074\n"
                                 "MM_TABLES_12AAV08=31,32\n",
                                 "-E", "custom.env", filename="custom.env")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("06CNB02: 21 22 74 13\n", result.stdout)
        self.assertIn("12AAV08: 31 32 13\n", result.stdout)
        self.assertIn("UNKNOWN:default\n", result.stdout)

    def test_empty_profile_restores_default(self):
        result = self.run_config("MM_TABLES_06CNB02=21,22\nMM_TABLES_06CNB02=\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("06CNB02:default", result.stdout)

    def test_missing_explicit_file_fails(self):
        result = self.run_config(None, "-Emissing.env")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing.env", result.stderr)

    def test_secondary_number_requires_primary_unless_cli_overrides(self):
        result = self.run_config("MM_SECONDARY_NCC_NUMBER=5678\n")
        self.assertNotEqual(result.returncode, 0)
        result = self.run_config("MM_SECONDARY_NCC_NUMBER=5678\n", "-n", "1234")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("5678", result.stdout)

    def test_invalid_configuration_fails_without_echoing_values(self):
        invalid = ["MM_TABLES_06CNB02=0", "MM_TABLES_06CNB02=256",
                   "MM_TABLES_06CNB02=21,21", "MM_TABLES_06CNB02=13",
                   "MM_TABLES_06CNB02=21,", "MM_TABLES_06CNB02=21,,22",
                   "MM_TABLES_06CNB02=21 garbage", "MM_TABLES_BAD=21",
                   "MM_VERBOSE=10", "MM_USE_MODEM=maybe",
                   'MM_ACCESS_CODE="secret', "MM_UNKNOWN=secret"]
        for text in invalid:
            with self.subTest(text=text):
                result = self.run_config(text + "\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("line 1", result.stderr)
                self.assertNotIn("secret", result.stderr)


if __name__ == "__main__":
    unittest.main()
