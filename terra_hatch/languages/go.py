"""Go: detected by a root `go.mod` with a module line."""

import re

ROW = {
    "template": "go",
    "registry": None,          # no registry-based version tracking
    "prefix": "",              # spec-Name prefix
    # when this tool is on PATH, ask it for the spec first
    "tool": "go2rpm",
}

TIPS = [
    "Go: verify %golicenses/%godocs file names and the %gobuild subpath.",
]


def sniff(src_dir, repo_name):
    go_mod = src_dir / "go.mod"
    if not go_mod.is_file():
        return None
    module = None
    for line in go_mod.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("module "):
            module = line.strip().split(None, 1)[1].split()[0]
            break
    if not module:
        return None
    return {
        "lang": "go",
        "goipath": module,
        "name": module.rsplit("/", 1)[-1],
        "main_dir": find_go_main(src_dir),
    }


def find_go_main(src_dir):
    """Where is the code for the binary? Returns the shallowest directory
    with a Go `package main` file ('' = repo root, None = not found)."""
    candidates = []
    for directory in [src_dir, *src_dir.rglob("*")]:
        if not directory.is_dir():
            continue
        for go_file in directory.glob("*.go"):
            try:
                text = go_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if re.search(r"^package\s+main\b", text, re.MULTILINE):
                candidates.append(directory)
                break
    if not candidates:
        return None
    if src_dir in candidates:
        return ""
    best = min(candidates, key=lambda d: len(d.relative_to(src_dir).parts))
    return best.relative_to(src_dir).as_posix()


def extras(source, base):
    return {"gobuild_path": _gobuild_path(source)}


def _gobuild_path(source):
    main_dir = source.get("main_dir")
    return "." if main_dir in ("", None) else f"./{main_dir}"
