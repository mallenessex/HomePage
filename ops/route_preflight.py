#!/usr/bin/env python3
"""
Launcher preflight: verify critical HTTP routes exist with expected methods.
This catches stale/mismatched code before the server process is started.
"""
from __future__ import annotations

import sys
from typing import Iterable
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


REQUIRED_ROUTES: dict[str, set[str]] = {
    "/admin/settings": {"GET"},
    "/admin/settings/update": {"POST"},
    "/admin/settings/secure-mode/ca": {"GET"},
    "/admin/settings/secure-mode/hosts": {"GET"},
    "/admin/settings/secure-mode/profile": {"GET"},
    "/admin/join-requests/{request_id}/delete": {"POST"},
    "/admin/users/{user_id}/delete": {"POST"},
    "/admin/users/{user_id}/remove": {"POST"},
    "/chat/channels/{channel_id}/topic/update": {"POST"},
    "/channels/{channel_id}/topic/update": {"POST"},
    "/.well-known/server-id": {"GET"},
    "/.well-known/connect-profile": {"GET"},
    "/.well-known/handshake": {"POST"},
}


def _route_methods(route) -> set[str]:
    methods = getattr(route, "methods", None)
    if not methods:
        return set()
    return {str(m).upper() for m in methods}


def _main() -> int:
    try:
        from app.main import app
    except Exception as exc:
        print(f"[route-preflight] FAIL: unable to import app.main: {exc}")
        return 1

    route_index: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        methods = _route_methods(route)
        if path in route_index:
            route_index[path].update(methods)
        else:
            route_index[path] = set(methods)

    missing_paths: list[str] = []
    method_mismatches: list[tuple[str, Iterable[str], Iterable[str]]] = []
    for path, expected_methods in REQUIRED_ROUTES.items():
        found_methods = route_index.get(path)
        if found_methods is None:
            missing_paths.append(path)
            continue
        if not expected_methods.issubset(found_methods):
            method_mismatches.append((path, sorted(expected_methods), sorted(found_methods)))

    if missing_paths or method_mismatches:
        print("[route-preflight] FAIL: required routes are missing or invalid.")
        if missing_paths:
            print("[route-preflight] Missing paths:")
            for path in missing_paths:
                print(f"  - {path}")
        if method_mismatches:
            print("[route-preflight] Method mismatches:")
            for path, expected, found in method_mismatches:
                print(f"  - {path}: expected {','.join(expected)}; found {','.join(found)}")
        return 1

    print(f"[route-preflight] OK: validated {len(REQUIRED_ROUTES)} critical routes.")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
