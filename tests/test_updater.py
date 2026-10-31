import json
import unittest
from unittest import mock
import urllib.error

from ubuntu_ferret_scanner import updater


class ParseVersion(unittest.TestCase):
    def test_compares_numerically(self):
        self.assertGreater(updater.parse_version("0.10.0"), updater.parse_version("0.9.1"))
        self.assertEqual(updater.parse_version("v1.2.0"), (1, 2, 0))


class Check(unittest.TestCase):
    def test_private_repo_counts_as_no_update(self):
        error = urllib.error.HTTPError(updater.API, 404, "Not Found", {}, None)
        with mock.patch.object(updater, "_get", side_effect=error):
            self.assertIsNone(updater.check())

    def test_rejects_assets_not_from_this_repo(self):
        release = {"assets": [{"name": updater.INFO_ASSET,
                               "browser_download_url": "https://example.com/info.json"}]}
        with mock.patch.object(updater, "_get", return_value=json.dumps(release).encode()):
            self.assertIsNone(updater.check())

    def test_finds_a_newer_release(self):
        base = f"https://github.com/{updater.REPO}/releases/download/v9.0.0/"
        release = {"assets": [
            {"name": updater.INFO_ASSET, "browser_download_url": base + updater.INFO_ASSET},
            {"name": "app.whl", "browser_download_url": base + "app.whl"},
        ]}
        info = {"version": "9.0.0", "wheel": "app.whl", "sha256": "ABC"}
        replies = [json.dumps(release).encode(), json.dumps(info).encode()]
        with mock.patch.object(updater, "_get", side_effect=replies):
            update = updater.check()
        self.assertEqual((update.version, update.sha256), ("9.0.0", "abc"))


if __name__ == "__main__":
    unittest.main()
