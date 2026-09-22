"""Downloads: the hash decides, and nothing is left half-written."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from rinth.errors import DownloadError
from rinth.install import already_present, download, file_hash, remove_file


class TestDownload(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.source = self.tmp / "source.jar"
        self.source.write_bytes(b"jar contents" * 100)
        self.sha512 = hashlib.sha512(self.source.read_bytes()).hexdigest()
        self.url = self.source.as_uri()

    def test_downloads_and_verifies(self):
        dest = self.tmp / "dest" / "plugin.jar"
        download(self.url, dest, sha512=self.sha512)
        self.assertTrue(dest.is_file())
        self.assertEqual(file_hash(dest), self.sha512)

    def test_wrong_hash_leaves_no_file(self):
        dest = self.tmp / "plugin.jar"
        with self.assertRaises(DownloadError) as caught:
            download(self.url, dest, sha512="0" * 128)
        self.assertIn("sha512", str(caught.exception))
        self.assertFalse(dest.exists())
        # The partial temporary must be gone too.
        self.assertEqual(list(self.tmp.glob(".*rinth-part")), [])

    def test_unexpected_size_fails(self):
        dest = self.tmp / "plugin.jar"
        with self.assertRaises(DownloadError):
            download(self.url, dest, sha512=self.sha512, expected_size=999999)
        self.assertFalse(dest.exists())

    def test_already_present_compares_contents(self):
        dest = self.tmp / "plugin.jar"
        download(self.url, dest, sha512=self.sha512)
        self.assertTrue(already_present(dest, self.sha512))
        dest.write_bytes(b"something else")
        self.assertFalse(already_present(dest, self.sha512))
        self.assertFalse(already_present(self.tmp / "missing.jar", self.sha512))

    def test_removing_what_is_not_there_does_not_fail(self):
        self.assertFalse(remove_file(self.tmp / "ghost.jar"))
        dest = self.tmp / "real.jar"
        dest.write_bytes(b"x")
        self.assertTrue(remove_file(dest))

    def test_overwrites_atomically(self):
        dest = self.tmp / "plugin.jar"
        dest.write_bytes(b"old version")
        download(self.url, dest, sha512=self.sha512)
        self.assertEqual(file_hash(dest), self.sha512)


if __name__ == "__main__":
    unittest.main()
