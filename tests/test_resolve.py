"""Version selection: the parts the API does not guarantee and we do."""

import unittest

from rinth.errors import ResolveError
from rinth.resolve import (
    channel_allows,
    derive_type,
    loader_chain,
    primary_file,
    resolve_all,
    resolve_version,
)

from .support import FakeClient, load


class TestVersionSelection(unittest.TestCase):
    def setUp(self):
        # Scrambled on purpose: /version has no guaranteed order.
        self.client = FakeClient(shuffle=True)

    def test_picks_the_newest_even_when_the_api_scrambles(self):
        result = resolve_version(self.client, "luckperms", "paper", "26.3")
        dates = [
            v["date_published"]
            for v in self.client.versions_by_key["luckperms"]
            if "paper" in v["loaders"] and "26.3" in v["game_versions"]
        ]
        self.assertEqual(result.version["date_published"], max(dates))

    def test_refilters_the_platform_client_side(self):
        # LuckPerms ships one build per platform; asking for paper must not
        # return the velocity one even though the server filter mixes them.
        result = resolve_version(self.client, "luckperms", "paper", "26.3")
        self.assertIn("paper", result.version["loaders"])

    def test_loader_fallback_warns(self):
        result = resolve_version(self.client, "discord-placeholders", "paper", "1.20.4")
        self.assertEqual(result.loader_used, "spigot")
        self.assertTrue(result.fell_back)
        self.assertTrue(any("nothing published for 'paper'" in w for w in result.warnings))

    def test_missing_mc_version_suggests_the_available_ones(self):
        with self.assertRaises(ResolveError) as caught:
            resolve_version(self.client, "discord-placeholders", "paper", "1.7.10")
        self.assertIn("1.20", caught.exception.hint)

    def test_override_by_version_number(self):
        number = self.client.versions_by_key["luckperms"][0]["version_number"]
        result = resolve_version(self.client, "luckperms", "paper", "26.3", override=number)
        self.assertEqual(result.version_number, number)
        self.assertTrue(result.pinned)

    def test_unknown_override_lists_alternatives(self):
        with self.assertRaises(ResolveError) as caught:
            resolve_version(self.client, "luckperms", "paper", "26.3", override="9.9.9")
        self.assertIn("recent versions", caught.exception.hint)

    def test_release_channel_drops_prereleases(self):
        self.assertTrue(channel_allows("release", "release"))
        self.assertFalse(channel_allows("beta", "release"))
        self.assertTrue(channel_allows("beta", "beta"))
        self.assertTrue(channel_allows("alpha", "alpha"))

    def test_compatibility_chains(self):
        self.assertEqual(loader_chain("paper"), ["paper", "spigot", "bukkit"])
        # Velocity has its own API: it does not inherit from bungeecord.
        self.assertEqual(loader_chain("velocity"), ["velocity"])
        self.assertEqual(loader_chain("quilt"), ["quilt", "fabric"])

    def test_primary_file(self):
        version = {"files": [{"filename": "a", "primary": False},
                             {"filename": "b", "primary": True}]}
        self.assertEqual(primary_file(version)["filename"], "b")
        self.assertIsNone(primary_file({"files": []}))


class TestDependencies(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()

    def test_resolves_transitive_dependencies(self):
        resolutions, optional, _, _ = resolve_all(
            self.client,
            [("excellentcrates-virtual-key-placeholder", None)],
            "paper",
            "1.21.11",
        )
        slugs = {r.slug for r in resolutions}
        self.assertIn("excellentcrates", slugs)          # direct dependency
        self.assertIn("nightcore", slugs)                # dependency of that one
        reasons = {r.slug: r.reason for r in resolutions}
        self.assertEqual(reasons["nightcore"], "dependency of excellentcrates")
        self.assertTrue(optional)                        # packetevents stays out

    def test_no_deps_keeps_only_what_was_asked(self):
        resolutions, _, _, _ = resolve_all(
            self.client,
            [("excellentcrates-virtual-key-placeholder", None)],
            "paper",
            "1.21.11",
            with_deps=False,
        )
        self.assertEqual([r.slug for r in resolutions],
                         ["excellentcrates-virtual-key-placeholder"])

    def test_a_cycle_does_not_hang(self):
        projects = load("projects")
        versions = load("versions")
        first, second = "excellentcrates", "nightcore"
        # nightcore now requires excellentcrates: A -> B -> A.
        for version in versions[second]:
            version["dependencies"] = [
                {"project_id": projects[first]["id"], "version_id": None,
                 "dependency_type": "required"}
            ]
        client = FakeClient(projects=projects, versions=versions)
        resolutions, _, _, _ = resolve_all(client, [(first, None)], "paper", "1.21.11")
        self.assertEqual(len({r.slug for r in resolutions}), len(resolutions))


class TestDerivedType(unittest.TestCase):
    def test_plugin_is_inferred_from_the_loaders(self):
        # /project returns project_type "mod" even for plugins.
        self.assertEqual(derive_type({"project_type": "mod", "loaders": ["paper"]}), "plugin")

    def test_search_carries_the_derived_type(self):
        hit = {"project_type": "mod", "all_project_types": ["mod", "plugin"]}
        self.assertEqual(derive_type(hit), "plugin")

    def test_a_mod_is_still_a_mod(self):
        self.assertEqual(derive_type({"project_type": "mod", "loaders": ["fabric"]}), "mod")


if __name__ == "__main__":
    unittest.main()
