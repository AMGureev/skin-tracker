import json
from pathlib import Path
import tempfile
import unittest

from cstracker.config import _read_local_config


class LocalConfigurationTest(unittest.TestCase):
    def test_missing_config_does_not_block_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(_read_local_config(Path(directory) / "missing.json"), {})

    def test_loads_private_config_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.local.json"
            config = {"CSMARKET_API_KEY": "test-key", "LIS_SKINS_API_KEY": "test-key"}
            path.write_text(json.dumps(config), encoding="utf-8")
            self.assertEqual(_read_local_config(path), config)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), config)

    def test_invalid_config_does_not_block_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.local.json"
            for text in ("not json", "[]", "null", '"value"'):
                path.write_text(text, encoding="utf-8")
                self.assertEqual(_read_local_config(path), {})


if __name__ == "__main__":
    unittest.main()
