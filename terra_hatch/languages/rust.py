"""Rust: detected by a root `Cargo.toml`. Cargo workspaces are detected too."""

import re
import shutil
import subprocess

from .files import read_toml
from ..log import warn

ROW = {
    "template": "rust",
    "registry": "crates",
    "prefix": "",
}

CARGO_TREE_FLAGS = ["--workspace", "--edges", "no-build,no-dev,no-proc-macro",
                    "--no-dedupe", "--target", "all", "--prefix", "none",
                    "--format", "{l}"]
CARGO_TREE_TIMEOUT = 180


def license_expression(src_dir):
    """Aggregate `License:` value for a Rust package, built the same way
    as the `%cargo_license_preamble_online` macro in anda-srpm-macros:
    cargo tree over the dependency graph, `X / Y` collapsed to `X OR Y`,
    multi-license entries parenthesized, all sorted and AND-joined.
    Returns None when cargo is missing or the fetch fails — no licence is
    ever made up."""
    cargo = shutil.which("cargo")
    if not cargo or not src_dir:
        return None
    try:
        result = subprocess.run([cargo, "tree", *CARGO_TREE_FLAGS],
                                cwd=src_dir, capture_output=True,
                                text=True, timeout=CARGO_TREE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as err:
        warn(f"cargo tree failed ({err}); License: stays the top-level value.")
        return None
    if result.returncode != 0:
        err_lines = (result.stderr or "").strip().splitlines()
        detail = err_lines[-1] if err_lines else "cargo tree failed"
        warn(f"cargo tree failed ({detail}); License: stays the top-level value.")
        return None
    entries = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        collapsed = re.sub(r"\s*/\s*", " OR ", line)
        entries.append(f"({collapsed})" if " " in collapsed else collapsed)
    return " AND ".join(sorted(set(entries))) or None

TIPS = [
    "Rust: check the %autosetup dir name and any Cargo workspace subcrates.",
    "Rust: dependencies auto-generate `LICENSE.dependencies`; main License: comes from Cargo.toml — check it.",
]


def sniff(src_dir, repo_name):
    cargo = src_dir / "Cargo.toml"
    if not cargo.is_file():
        return None
    data = read_toml(cargo)
    package = data.get("package") or {}
    workspace = data.get("workspace") or {}
    if not package.get("name") and "workspace" not in data:
        return None
    return {
        "lang": "rust",
        "name": package.get("name"),
        "license": package.get("license"),
        "description": package.get("description") or workspace.get("package", {}).get("description"),
        "version": package.get("version") or workspace.get("package", {}).get("version"),
        "url": package.get("homepage") or package.get("repository"),
        "workspace": "workspace" in data,
    }
