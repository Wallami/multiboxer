from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


def common_search_roots() -> list[Path]:
    if os.name == "nt":
        roots: list[Path] = []
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            candidate = Path(f"{letter}:/")
            if candidate.exists():
                roots.append(candidate)
        return roots

    return [Path.home()]


def find_everquest_exe(roots: Iterable[Path], max_hits: int = 1) -> list[Path]:
    matches: list[Path] = []
    needle = "everquest.exe"

    for root in roots:
        try:
            iterator = root.rglob("*")
        except OSError:
            continue

        for file_path in iterator:
            if file_path.is_file() and file_path.name.lower() == needle:
                matches.append(file_path)
                if len(matches) >= max_hits:
                    return matches

    return matches
