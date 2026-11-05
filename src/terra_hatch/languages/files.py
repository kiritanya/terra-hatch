"""File helpers shared by all language sniffers."""

import tomllib

from ..log import warn

LICENSE_PATTERNS = ("license", "copyright", "copying", "licence")
DOC_PATTERNS = ("readme", "authors", "contributors", "news", "changelog")


def collect_top_files(src_dir):
    """List LICENSE*, README* and friends at the top of the repo."""
    licenses, docs = [], []
    for entry in sorted(src_dir.iterdir()):
        if not entry.is_file():
            continue
        lower = entry.name.lower()
        if lower.startswith(LICENSE_PATTERNS):
            licenses.append(entry.name)
        elif lower.startswith(DOC_PATTERNS):
            docs.append(entry.name)
    return licenses, docs


def read_toml(path):
    try:
        return tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (tomllib.TOMLDecodeError, OSError) as err:
        warn(f"Could not read {path.name} ({err}); the values become TODO.")
        return {}
