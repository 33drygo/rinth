"""Client for the Modrinth v2 API.

Stdlib only. It takes care of the User-Agent (Modrinth requires one), the rate
limit (300 req/min, announced through the x-ratelimit-* headers) and a
short-lived on-disk cache so identical lookups are not repeated.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__
from .errors import ApiError, Gone, NotFound, RateLimited

API = "https://api.modrinth.com/v2"
USER_AGENT = f"rinth/{__version__} (+https://github.com/drygo/rinth)"
CACHE_TTL = 300  # seconds
TIMEOUT = 30

# When fewer requests than this remain in the window, wait for the reset.
RATE_FLOOR = 10


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "rinth"


class Client:
    """API client with a per-instance cache and rate-limit tracking."""

    def __init__(self, use_cache=True, ttl=CACHE_TTL, verbose=False):
        self.use_cache = use_cache
        self.ttl = ttl
        self.verbose = verbose
        self._remaining = None
        self._reset_at = 0.0

    # --- transport -----------------------------------------------------

    def _url(self, path, params=None):
        url = f"{API}{path}"
        if params:
            # The API expects arrays as JSON inside the query string.
            flat = {}
            for key, value in params.items():
                if value is None:
                    continue
                if isinstance(value, (list, tuple)):
                    flat[key] = json.dumps(list(value), separators=(",", ":"))
                elif isinstance(value, bool):
                    flat[key] = "true" if value else "false"
                else:
                    flat[key] = str(value)
            if flat:
                url += "?" + urllib.parse.urlencode(flat)
        return url

    def _cache_path(self, url):
        digest = hashlib.sha256(url.encode()).hexdigest()[:32]
        return cache_dir() / f"{digest}.json"

    def _cache_read(self, url):
        if not self.use_cache:
            return None
        path = self._cache_path(url)
        try:
            blob = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if time.time() - blob.get("t", 0) > self.ttl:
            return None
        return blob.get("body")

    def _cache_write(self, url, body):
        if not self.use_cache:
            return
        path = self._cache_path(url)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"t": time.time(), "body": body}))
            os.replace(tmp, path)
        except OSError:
            pass  # a cache that cannot write is no reason to fail

    def _respect_rate_limit(self):
        if self._remaining is not None and self._remaining < RATE_FLOOR:
            wait = max(0.0, self._reset_at - time.time())
            if wait > 0:
                if self.verbose:
                    print(f"rate limit: waiting {wait:.0f}s", flush=True)
                time.sleep(min(wait, 60))

    def _note_rate_limit(self, headers):
        try:
            self._remaining = int(headers.get("x-ratelimit-remaining"))
            self._reset_at = time.time() + int(headers.get("x-ratelimit-reset"))
        except (TypeError, ValueError):
            pass

    def get(self, path, params=None, cacheable=True):
        url = self._url(path, params)

        cached = self._cache_read(url) if cacheable else None
        if cached is not None:
            return cached

        body = self._fetch(url)
        if cacheable:
            self._cache_write(url, body)
        return body

    def _fetch(self, url, attempt=0):
        self._respect_rate_limit()
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                self._note_rate_limit(response.headers)
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode())
        except urllib.error.HTTPError as exc:
            self._note_rate_limit(exc.headers)
            if exc.code == 404:
                raise NotFound("not found on Modrinth") from exc
            if exc.code == 410:
                raise Gone("the project was withdrawn from Modrinth") from exc
            if exc.code == 429:
                if attempt >= 2:
                    raise RateLimited(
                        "Modrinth is rate-limiting requests",
                        hint="wait a minute and try again",
                    ) from exc
                delay = int(exc.headers.get("Retry-After") or 5)
                time.sleep(min(delay, 60))
                return self._fetch(url, attempt + 1)
            raise ApiError(f"the API answered HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"could not reach Modrinth: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise ApiError("the API returned something that is not JSON") from exc

    # --- endpoints -----------------------------------------------------

    def search(self, query, project_type=None, loader=None, mc=None, limit=20, offset=0):
        """Search projects. Outer facets are ANDed, inner ones ORed."""
        facets = []
        if project_type:
            facets.append([f"project_type:{project_type}"])
        if loader:
            facets.append([f"categories:{loader}"])
        if mc:
            facets.append([f"versions:{mc}"])
        params = {"query": query, "limit": limit, "offset": offset}
        if facets:
            params["facets"] = facets
        return self.get("/search", params)

    def project(self, ident):
        try:
            return self.get(f"/project/{urllib.parse.quote(ident, safe='')}")
        except NotFound as exc:
            raise NotFound(f"project '{ident}' does not exist on Modrinth") from exc

    def projects(self, idents):
        """Several projects in one request (used to name dependencies)."""
        if not idents:
            return []
        return self.get("/projects", {"ids": list(idents)})

    def versions(self, ident, loaders=None, game_versions=None):
        params = {}
        if loaders:
            params["loaders"] = list(loaders)
        if game_versions:
            params["game_versions"] = list(game_versions)
        try:
            return self.get(
                f"/project/{urllib.parse.quote(ident, safe='')}/version", params
            )
        except NotFound as exc:
            raise NotFound(f"project '{ident}' does not exist on Modrinth") from exc

    def version(self, version_id):
        return self.get(f"/version/{urllib.parse.quote(version_id, safe='')}")

    def version_from_hash(self, digest, algorithm="sha1"):
        """Identify a file already on disk. None if Modrinth does not know it."""
        try:
            return self.get(f"/version_file/{digest}", {"algorithm": algorithm})
        except NotFound:
            return None

    def game_versions(self):
        """Minecraft version list, for validating -m and for completions."""
        return self.get("/tag/game_version", cacheable=True)
