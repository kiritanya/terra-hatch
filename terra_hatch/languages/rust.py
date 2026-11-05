"""Rust: detected by a root `Cargo.toml`. Cargo workspaces are detected too."""

from .files import read_toml

ROW = {
    "template": "rust",
    "registry": "crates",
    "prefix": "",
}

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
