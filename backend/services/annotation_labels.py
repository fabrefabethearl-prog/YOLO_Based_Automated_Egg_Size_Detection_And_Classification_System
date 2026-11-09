"""Reference labels parsed from uploaded image filenames."""

from __future__ import annotations

import re

FILENAME_LABELS = {
    "small": "Small",
    "medium": "Medium",
    "large": "Large",
    "extra large": "Extra Large",
    "jumbo": "Jumbo",
}


def filename_reference_label(filename: str) -> str | None:
    """Return a class only when a filename contains exactly one supported token."""
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    tokens = re.split(r"[\s_-]+", stem.casefold())
    matches = []
    index = 0
    while index < len(tokens):
        if tokens[index : index + 2] == ["extra", "large"]:
            matches.append(FILENAME_LABELS["extra large"])
            index += 2
        elif tokens[index] in FILENAME_LABELS and tokens[index] != "extra large":
            matches.append(FILENAME_LABELS[tokens[index]])
            index += 1
        else:
            index += 1
    return matches[0] if len(matches) == 1 else None
