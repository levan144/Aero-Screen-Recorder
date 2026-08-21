from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import __version__


LATEST_RELEASE_API = (
    "https://api.github.com/repos/levan144/Aero-Screen-Recorder/releases/latest"
)


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    version: str
    page_url: str
    name: str


def version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)*", value)
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


def is_newer_version(candidate: str, current: str = __version__) -> bool:
    candidate_parts = version_tuple(candidate)
    current_parts = version_tuple(current)
    length = max(len(candidate_parts), len(current_parts))
    return candidate_parts + (0,) * (length - len(candidate_parts)) > current_parts + (
        0,
    ) * (length - len(current_parts))


def check_latest_release(timeout: float = 8.0) -> UpdateInfo | None:
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"AeroRecorder/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # GitHub returns 404 for /releases/latest when the repository exists
        # but has no published releases. That is a valid "nothing to update"
        # state, not a connectivity failure.
        if exc.code == 404:
            return None
        raise RuntimeError(f"Could not check GitHub releases: {exc}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not check GitHub releases: {exc}") from exc
    tag = str(payload.get("tag_name", ""))
    if not tag or not is_newer_version(tag):
        return None
    return UpdateInfo(
        version=tag,
        page_url=str(payload.get("html_url", "")),
        name=str(payload.get("name") or tag),
    )
