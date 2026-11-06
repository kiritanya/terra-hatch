"""Python: detected by a root `pyproject.toml` with a `[project]` name."""

import re
import shutil
import subprocess

from .files import read_toml

ROW = {
    "template": "python",
    "registry": "pypi",
    "prefix": "python-",
    # when this tool is on PATH, ask it for the spec first
    "tool": "pyp2spec",
}

TIPS = [
    "Python: confirm the module name in %pyproject_save_files and drop `BuildArch: noarch` if the wheel has compiled extensions.",
    "Python: a plain `python-<name>` spec only fits pure-Python packages; anything compiled needs the right BuildRequires.",
]

# build-system `requires` entries we can map to Terra RPM names
BUILD_TOOLS = {
    "setuptools": "python3-setuptools",
    "wheel": "python3-wheel",
    "pip": "python3-pip",
    "installer": "python3-installer",
    "hatchling": "python3-hatchling",
    "hatch-vcs": "python3-hatch-vcs",
    "setuptools-scm": "python3-setuptools-scm",
    "poetry-core": "python3-poetry-core",
    "flit-core": "python3-flit-core",
    "maturin": "maturin",
}


def sniff(src_dir, repo_name):
    pyproject = src_dir / "pyproject.toml"
    if pyproject.is_file():
        data = read_toml(pyproject)
        project = data.get("project") or {}
        if not project.get("name"):
            return None
        urls = project.get("urls") or {}
        license_field = project.get("license")
        if isinstance(license_field, dict):
            license_field = license_field.get("text")
        return {
            "lang": "python",
            "name": project["name"],
            "license": license_field,
            "description": project.get("description"),
            "url": urls.get("homepage") or urls.get("repository"),
            "build_requires": map_build_requires(
                (data.get("build-system") or {}).get("requires") or []),
        }
    # legacy projects still use a root setup.py
    setup_py = src_dir / "setup.py"
    if not setup_py.is_file():
        return None
    try:
        text = setup_py.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    # find the last `...setup(` call in the file and only take literals
    # from its argument block, so `name=` from unrelated code (threads,
    # dialogs, ...) never wins. No code runs here.
    start = text.rfind("setup(")
    block = text[start:start + 4000] if start >= 0 else ""
    name = _setup_literal(block, "name")
    if not name:
        return None
    license_field = _setup_literal(text, "license")
    return {
        "lang": "python",
        "name": name,
        "license": license_field,
        "description": None,
        "url": None,
        # legacy setup.py is still buildable through the setuptools backend,
        # but the %pyproject_* macros need a human check.
        "build_requires": {"mapped": [], "unknown": []},
        "legacy_setup_py": True,
        # use the dedicated legacy template, not the pyproject one
        "template": "python-setup",
    }


def _setup_literal(text, keyword):
    """Take the first plain string value of `keyword=...`. Nothing else:
    no code runs here."""
    match = re.search(rf'\b{keyword}\s*=\s*["\']([^"\']+)["\']', text)
    return match.group(1) if match else None


def map_build_requires(requires):
    """Turn build-system `requires` into RPM names and record what we
    could not map."""
    mapped, unknown = [], []
    for element in requires:
        base = re.split(r"[<>=!~;\[\s]", str(element), maxsplit=1)[0].strip().lower()
        rpm_name = BUILD_TOOLS.get(base)
        if rpm_name:
            if rpm_name not in mapped:
                mapped.append(rpm_name)
        else:
            unknown.append(str(element))
    return {"mapped": mapped, "unknown": unknown}


def extras(source, base):
    """Names for the python spec:
    - `python-<name>` on the outside to match the RPM name
    - `_`-joined module name on the inside
    """
    mapped = source.get("build_requires", {}).get("mapped", [])
    unknown = source.get("build_requires", {}).get("unknown", [])
    comment = ""
    if unknown:
        comment = "# TODO: unmapped build-system requires: " + " ".join(unknown)
    return {
        "build_requires": " ".join(["python3-devel", *mapped]),
        "py_unknown_comment": comment,
        "module": source["name"].replace("-", "_").replace(".", "_"),
    }


SPEC_SECTION = (r"%(?:prep|build|install|check|files|changelog|description|"
                r"package|generate_buildrequires|clean|postun|post|preun|pre|"
                r"trigger|conf|verifyscript|transfiletrigger)\b")
GENERATE_BUILDREQUIRES = re.compile(
    rf"^%generate_buildrequires[ \t]*\n(.*?)(?=^{SPEC_SECTION}|\Z)",
    re.M | re.S)

BUILDREQUIRES_TODO = ("# TODO: BuildRequires — run %pyproject_buildrequires on a "
                      "Fedora host and list the python3dist(...) results here")


def resolve_buildrequires(text, src_dir):
    """Terra specs do not use %generate_buildrequires. Run the section's
    commands (pyp2spec writes `%pyproject_buildrequires ...`) against the
    sources instead, turn the printed `python3dist(...)` lines into
    BuildRequires and drop the section. Returns (text, requirements or
    None); None means the macros/rpm were unavailable, so a TODO is left."""
    match = GENERATE_BUILDREQUIRES.search(text)
    if not match:
        return text, None
    requirements = _run_requirements(match.group(1), src_dir)
    replacement = "" if requirements else BUILDREQUIRES_TODO + "\n"
    text = text[:match.start()] + replacement + text[match.end():]
    text = re.sub(r"\n{3,}", "\n\n", text)
    if requirements:
        text = _insert_buildrequires(text, requirements)
    return text, requirements


def _run_requirements(body, src_dir):
    """Expand the section's commands with rpm, run them in the sources and
    keep the python3dist lines they print. None on any problem or when the
    pyproject macros are not installed (Fedora ships a stub that only
    echoes the package name)."""
    rpm = shutil.which("rpm")
    if not rpm or not src_dir:
        return None
    script_lines = []
    for line in body.splitlines():
        command = line.strip()
        if not command or command.startswith("#"):
            continue
        try:
            expanded = subprocess.run([rpm, "--eval", command],
                                      capture_output=True, text=True,
                                      timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
        output = expanded.stdout.strip()
        if expanded.returncode != 0 or not output \
                or "%pyproject_buildrequires" in output:
            return None
        script_lines.append(output)
    script = "\n".join(script_lines)
    if not script:
        return None
    try:
        result = subprocess.run(["bash", "-euo", "pipefail", "-c", script],
                                cwd=src_dir, capture_output=True, text=True,
                                timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    requirements = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if "dist(" in line or line.startswith("python3-"):
            requirements.append(line)
    return sorted(set(requirements)) or None


def _insert_buildrequires(text, requirements):
    """Append the resolved requirements to the BuildRequires block (or
    after Source when the spec has none yet), skipping duplicates."""
    existing = set(re.findall(r"^BuildRequires:\s*(.+)$", text, re.M))
    lines = [f"BuildRequires:  {req}" for req in requirements
             if req not in existing]
    if not lines:
        return text
    block = "\n".join(lines)
    if re.search(r"^BuildRequires:", text, re.M):
        return re.sub(r"^(BuildRequires:.*(?:\nBuildRequires:.*)*)$",
                      lambda m: m.group(1) + "\n" + block,
                      text, count=1, flags=re.M)
    return re.sub(r"^(Source:.*)$", lambda m: m.group(1) + "\n" + block,
                  text, count=1, flags=re.M)
