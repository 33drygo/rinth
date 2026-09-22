"""Downloading and placing files.

Always to a temporary file on the same filesystem as the destination, then a
sha512 check and os.replace: a half-written jar never lands in the server's
folder.
"""

from __future__ import annotations

import hashlib
import os
import urllib.error
import urllib.request
from pathlib import Path

from .api import TIMEOUT, USER_AGENT
from .errors import DownloadError

CHUNK = 64 * 1024


def file_hash(path, algorithm="sha512"):
    digest = hashlib.new(algorithm)
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                digest.update(chunk)
    except OSError as exc:
        raise DownloadError(f"could not read {path}: {exc}") from exc
    return digest.hexdigest()


def already_present(path, sha512):
    """True if the file exists and its contents are exactly what we expect."""
    if not Path(path).is_file():
        return False
    if not sha512:
        return True
    return file_hash(path, "sha512") == sha512.lower()


def download(url, dest, sha512=None, expected_size=None, on_progress=None):
    """Fetch `url` into `dest`, verifying the hash before moving it into place."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.rinth-part"

    digest = hashlib.sha512()
    written = 0
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(tmp, "wb") as handle:
            total = expected_size or int(response.headers.get("Content-Length") or 0)
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                if on_progress:
                    on_progress(written, total)
    except urllib.error.URLError as exc:
        _cleanup(tmp)
        raise DownloadError(f"failed to download {url}: {exc.reason}") from exc
    except OSError as exc:
        _cleanup(tmp)
        raise DownloadError(f"failed to write {tmp}: {exc}") from exc

    if sha512 and digest.hexdigest() != sha512.lower():
        _cleanup(tmp)
        raise DownloadError(
            f"sha512 mismatch for {dest.name} against what Modrinth publishes",
            hint="retry; if it persists, the remote file changed",
        )
    if expected_size and written != expected_size:
        _cleanup(tmp)
        raise DownloadError(
            f"{dest.name}: expected {expected_size} bytes but got {written}"
        )

    try:
        os.replace(tmp, dest)
    except OSError as exc:
        _cleanup(tmp)
        raise DownloadError(f"could not place {dest}: {exc}") from exc
    return dest


def remove_file(path):
    """Delete a file we know about. Silent if it is already gone."""
    try:
        Path(path).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DownloadError(f"could not delete {path}: {exc}") from exc


def _cleanup(path):
    try:
        Path(path).unlink()
    except OSError:
        pass
