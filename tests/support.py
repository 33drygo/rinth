"""Fake client serving the recorded fixtures. Tests never touch the network."""

from __future__ import annotations

import json
import pathlib
import random

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeClient:
    """Implements the surface of api.Client the rest of the code uses."""

    def __init__(self, projects=None, versions=None, shuffle=False):
        self.projects_by_key = projects if projects is not None else load("projects")
        self.versions_by_key = versions if versions is not None else load("versions")
        self.calls = []
        if shuffle:
            # /version has no guaranteed order: scramble it on purpose so the
            # tests have to rely on date_published.
            rng = random.Random(1234)
            for items in self.versions_by_key.values():
                rng.shuffle(items)

    def _key(self, ident):
        if ident in self.projects_by_key:
            return ident
        for slug, project in self.projects_by_key.items():
            if project.get("id") == ident:
                return slug
        raise KeyError(ident)

    def project(self, ident):
        from rinth.errors import NotFound

        try:
            return self.projects_by_key[self._key(ident)]
        except KeyError:
            raise NotFound(f"project '{ident}' does not exist on Modrinth") from None

    def projects(self, idents):
        found = []
        for ident in idents:
            try:
                found.append(self.project(ident))
            except Exception:
                pass
        return found

    def versions(self, ident, loaders=None, game_versions=None):
        from rinth.errors import NotFound

        try:
            items = self.versions_by_key[self._key(ident)]
        except KeyError:
            raise NotFound(f"project '{ident}' does not exist on Modrinth") from None
        self.calls.append((ident, tuple(loaders or ()), tuple(game_versions or ())))
        # Mimic the server-side filtering: loaders AND game_versions.
        result = items
        if loaders:
            result = [v for v in result if set(v["loaders"]) & set(loaders)]
        if game_versions:
            result = [v for v in result if set(v["game_versions"]) & set(game_versions)]
        return result

    def version(self, version_id):
        for items in self.versions_by_key.values():
            for version in items:
                if version["id"] == version_id:
                    return version
        from rinth.errors import NotFound

        raise NotFound("unknown version")

    def version_from_hash(self, digest, algorithm="sha1"):
        return None

    def game_versions(self):
        return [{"version": "26.3", "version_type": "release"}]

    def search(self, query, **kwargs):
        return load("search")
