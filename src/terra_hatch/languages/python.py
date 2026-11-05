"""Python: detected by a root `pyproject.toml` with a `[project]` name."""

import re

from .files import read_toml

ROW = {
    "template": "python",
    "registry": "pypi",
    "prefix": "python-",
}

TIPS = [
    "Python: confirm the module name in %pyproject_save_files and drop `BuildArch: noarch` if the wheel has compiled extensions.",
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
    if not pyproject.is_file():
        return None
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
