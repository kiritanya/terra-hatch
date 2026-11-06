"""Language support for terra-hatch, one module per language.

To add a language:
1. create a module here with a ``ROW``, a ``sniff()`` function, and
   optionally ``extras()`` and ``TIPS``
2. register it in the tables below
3. drop a ``.spec.j2`` template into ``templates/``
"""

from . import generic, go, python, rust
from .files import collect_top_files

# sniff order: first detector to return a result wins
SNIFF_ORDER = ("go", "rust", "python")

# language table — one row per language
LANGUAGES = {
    "go": go.ROW,
    "rust": rust.ROW,
    "python": python.ROW,
    "generic": generic.ROW,
}

SNIFFERS = {
    "go": go.sniff,
    "rust": rust.sniff,
    "python": python.sniff,
    "generic": generic.sniff,
}

# extra template values per language; languages without extras are absent
EXTRAS = {
    "go": go.extras,
    "python": python.extras,
}

# per-language license aggregation hooks: fn(sources dir) -> SPDX expression
LICENSE_HOOKS = {
    "rust": rust.license_expression,
}

# per-language review reminder text; languages without tips are absent
LANG_TIPS = {
    "go": go.TIPS,
    "rust": rust.TIPS,
    "python": python.TIPS,
    "generic": generic.TIPS,
}

# registry APIs for auto-update: registry name → (url pattern, check field)
REGISTRY_APIS = {
    "crates": ("https://crates.io/api/v1/crates/{name}", "crate"),
    "pypi": ("https://pypi.org/pypi/{name}/json", "info"),
}


def sniff_repository(src_dir, repo_name):
    """Find the language and basic facts of the upstream sources."""
    licenses, docs = collect_top_files(src_dir)
    for lang in SNIFF_ORDER:
        info = SNIFFERS[lang](src_dir, repo_name)
        if info:
            break
    else:
        info = SNIFFERS["generic"](src_dir, repo_name)
    info.setdefault("license", None)
    info.setdefault("description", None)
    info.setdefault("url", None)
    info["top_licenses"] = licenses
    info["top_docs"] = docs
    return info
