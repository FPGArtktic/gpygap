# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from gpygap.resolver import Resolution, ResolveRequest, parse_pylock

PYLOCK = """
lock-version = "1.0"
created-by = "uv"
requires-python = ">=3.10"

[[packages]]
name = "Typing_Extensions"
version = "4.15.0"
marker = "python_full_version < '3.13'"
sdist = { url = "https://f.example/typing_extensions-4.15.0.tar.gz", size = 10, hashes = { sha256 = "aa" } }
wheels = [
    { url = "https://f.example/typing_extensions-4.15.0-py3-none-any.whl", hashes = { sha256 = "bb" } },
    { url = "https://f.example/..%2F..%2Fevil-1.0-py3-none-any.whl", size = 5, hashes = { sha256 = "cc" } },
]

[[packages]]
name = "local-thing"
version = "0.1"
directory = { path = "." }
"""


def test_parse_pylock() -> None:
    packages = parse_pylock(PYLOCK)
    assert [p.name for p in packages] == ["typing-extensions", "local-thing"]
    typing_extensions = packages[0]
    assert typing_extensions.marker == "python_full_version < '3.13'"
    assert [(f.kind, f.filename, f.sha256) for f in typing_extensions.files] == [
        ("sdist", "typing_extensions-4.15.0.tar.gz", "aa"),
        ("wheel", "typing_extensions-4.15.0-py3-none-any.whl", "bb"),
    ]
    assert packages[1].files == ()


def test_resolution_round_trips_through_json() -> None:
    request = ResolveRequest("typing-extensions", "3.12")
    resolution = Resolution(request, tuple(parse_pylock(PYLOCK)), 123.0, error="boom")
    assert Resolution.from_json(resolution.to_json()) == resolution
    assert resolution.stale
    assert not resolution.ok
