"""The manifest is hand-written: what we write must not destroy it."""

import tempfile
import unittest
from pathlib import Path

from rinth.errors import ManifestError
from rinth.manifest import Entry, Lock, Manifest


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def _fresh(self, text=None):
        if text is None:
            manifest = Manifest.create(self.root, "paper", "26.3", "plugins")
            manifest.save()
        else:
            (self.root / "rinth.toml").write_text(text)
        return Manifest.load(self.root)

    def test_round_trip(self):
        manifest = self._fresh()
        manifest.set_entry(Entry("luckperms", "*"))
        manifest.set_entry(Entry("vault", "1.7.3"))
        manifest.set_entry(Entry("essentials", "*", "beta"))
        manifest.save()

        reloaded = Manifest.load(self.root)
        self.assertEqual(reloaded.loader, "paper")
        self.assertEqual(reloaded.minecraft, "26.3")
        self.assertEqual(reloaded.target_dir, "plugins")
        self.assertEqual(reloaded.entries["vault"].version, "1.7.3")
        self.assertTrue(reloaded.entries["vault"].pinned)
        self.assertFalse(reloaded.entries["luckperms"].pinned)
        self.assertEqual(reloaded.entries["essentials"].channel, "beta")

    def test_keeps_comments_and_ordering(self):
        text = (
            '# my server\n\n[server]\nloader = "paper"\nminecraft = "26.3"\n\n'
            '[plugins]\n# permissions\nluckperms = "*"\n'
            'vault = "1.7.3"  # do not touch\n'
        )
        manifest = self._fresh(text)
        manifest.set_entry(Entry("newcomer", "*"))
        manifest.save()

        result = (self.root / "rinth.toml").read_text()
        self.assertIn("# my server", result)
        self.assertIn("# permissions", result)
        self.assertIn("# do not touch", result)
        self.assertLess(result.index("luckperms"), result.index("newcomer"))

    def test_updating_an_entry_does_not_duplicate_it(self):
        manifest = self._fresh()
        manifest.set_entry(Entry("vault", "1.7.3"))
        manifest.save()
        manifest = Manifest.load(self.root)
        manifest.set_entry(Entry("vault", "1.7.4"))
        manifest.save()

        result = (self.root / "rinth.toml").read_text()
        self.assertEqual(result.count("vault ="), 1)
        self.assertIn('vault = "1.7.4"', result)

    def test_dropping_an_entry(self):
        manifest = self._fresh()
        manifest.set_entry(Entry("vault", "1.7.3"))
        manifest.save()
        manifest = Manifest.load(self.root)
        manifest.drop_entry("vault")
        manifest.save()
        self.assertNotIn("vault", (self.root / "rinth.toml").read_text())

    def test_quotes_are_escaped(self):
        manifest = self._fresh()
        manifest.set_entry(Entry("odd", 'ver"sion'))
        manifest.save()
        self.assertEqual(Manifest.load(self.root).entries["odd"].version, 'ver"sion')

    def test_invalid_toml_gives_a_readable_error(self):
        with self.assertRaises(ManifestError) as caught:
            self._fresh("[server\nloader = paper")
        self.assertIn("syntax", str(caught.exception))

    def test_invalid_channel_is_rejected(self):
        with self.assertRaises(ManifestError) as caught:
            self._fresh('[plugins]\nx = { version = "*", channel = "nightly" }\n')
        self.assertIn("channel", str(caught.exception))

    def test_a_missing_manifest_suggests_init(self):
        with self.assertRaises(ManifestError) as caught:
            Manifest.load(self.root)
        self.assertIn("rinth init", caught.exception.hint)


class _StubResolution:
    """The minimum Lock.put needs."""

    slug = "luckperms"
    project_id = "Vebnzrzj"
    title = "LuckPerms"
    version = {"id": "abc", "date_published": "2026-01-01T00:00:00Z"}
    file = {"size": 10}
    version_id = "abc"
    version_number = "5.5.71"
    filename = "LuckPerms.jar"
    sha512 = "f" * 128
    url = "https://example.invalid/LuckPerms.jar"
    loader_used = "paper"
    pinned = False
    reason = "direct"


class TestLock(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "rinth.lock.json"

    def test_round_trip(self):
        lock = Lock.load(self.path)
        lock.put(_StubResolution())
        lock.save("paper", "26.3")

        reloaded = Lock.load(self.path)
        self.assertEqual(reloaded.get("luckperms")["version_number"], "5.5.71")
        self.assertEqual(reloaded.server["loader"], "paper")

    def test_a_corrupt_lock_does_not_block(self):
        self.path.write_text("{ this is not json")
        self.assertEqual(Lock.load(self.path).entries, {})

    def test_dependencies_of(self):
        lock = Lock.load(self.path)
        lock.entries = {
            "a": {"reason": "direct"},
            "b": {"reason": "dependency of a"},
            "c": {"reason": "dependency of b"},
        }
        self.assertEqual(lock.dependencies_of("a"), ["b"])
        self.assertEqual(lock.dependencies_of("b"), ["c"])


if __name__ == "__main__":
    unittest.main()
