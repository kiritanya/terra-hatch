"""Find the upstream project's own license for `SourceLicense:`.

Two sources, in order of trust:

1. language metadata (Cargo.toml `license`, including workspace members)
2. LICENSE/COPYING files at the repo root, matched against the common
   license texts

The file pass is a guess: the text cannot tell `GPL-3.0-only` from
`GPL-3.0-or-later`, and a repo can carry a license that covers only part of
the tree. Callers get the origin back so they can add a review reminder.
"""

import re

from .languages.files import LICENSE_PATTERNS, read_toml

# phrases that identify a license text; checked in order, more specific
# families first. Each entry needs every pattern to match.
#
# The title rules only see the top of the file: the full GPLv3 text mentions
# the Affero and Lesser licenses later on, so matching the whole body would
# mistake GPLv3 for AGPL/LGPL.
HEAD_RULES = (
    ("AGPL-3.0-only", (r"GNU AFFERO GENERAL PUBLIC LICENSE", r"Version 3")),
    ("LGPL-3.0-only", (r"GNU LESSER GENERAL PUBLIC LICENSE", r"Version 3")),
    ("LGPL-2.1-only", (r"GNU LESSER GENERAL PUBLIC LICENSE", r"Version 2\.1")),
    ("GPL-3.0-only", (r"GNU GENERAL PUBLIC LICENSE", r"Version 3")),
    ("GPL-2.0-only", (r"GNU GENERAL PUBLIC LICENSE", r"Version 2")),
    ("Apache-2.0", (r"Apache License", r"Version 2\.0")),
    ("MPL-2.0", (r"Mozilla Public License Version 2\.0",)),
    ("EUPL-1.2", (r"EUROPEAN UNION PUBLIC LICENCE", r"v\. 1\.2")),
    ("BSL-1.0", (r"Boost Software License", r"Version 1\.0")),
    ("CC0-1.0", (r"CC0 1\.0 Universal",)),
)

# rules whose distinctive sentence is in the body of the file
BODY_RULES = (
    ("Unlicense", (r"free and unencumbered software released into the public domain",)),
    ("Zlib", (r"This software is provided .as-is.",)),
    ("BSD-3-Clause", (r"Redistribution and use in source and binary forms",
                      r"Neither the name")),
    ("BSD-2-Clause", (r"Redistribution and use in source and binary forms",)),
    ("MIT", (r"Permission is hereby granted, free of charge",)),
)


def spdx_from_text(text):
    """Pull an SPDX id out of a license file, or None when the text is not
    one of the well-known licenses."""
    header = re.search(r"SPDX-License-Identifier:\s*([A-Za-z0-9.+\-() ]+)", text)
    if header:
        return header.group(1).strip()

    head = " ".join("\n".join(text.lstrip().splitlines()[:40]).split())
    folded = " ".join(text.split())

    for spdx, patterns in HEAD_RULES:
        if all(re.search(pattern, head, re.I) for pattern in patterns):
            return spdx

    # The body can hold more than one license (a project's own text plus
    # vendored code below it). The first one is the project's own, so pick
    # the earliest match.
    candidates = []
    isc = re.search(r"Permission to use, copy, modify, and(?:/or)? distribute",
                    folded, re.I)
    if isc:
        isc_id = "ISC" if re.search(
            r"provided that the above copyright notice", folded, re.I) else "0BSD"
        candidates.append((isc.start(), 0, isc_id))
    for order, (spdx, patterns) in enumerate(BODY_RULES, start=1):
        spans = [re.search(pattern, folded, re.I) for pattern in patterns]
        if all(spans):
            candidates.append((spans[0].start(), order, spdx))
    if candidates:
        return min(candidates)[2]
    return None


def _read_license(path):
    try:
        return spdx_from_text(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def file_license(src_dir):
    """License ids found in the repo-root LICENSE/COPYING files. Multiple
    different files usually mean a dual-licensed project, so join with OR."""
    found = []
    for entry in sorted(src_dir.iterdir()):
        if not entry.is_file():
            continue
        if not entry.name.lower().startswith(LICENSE_PATTERNS):
            continue
        spdx = _read_license(entry)
        if spdx and spdx not in found:
            found.append(spdx)
    return " OR ".join(found) if found else None


def cargo_license(src_dir):
    """`license` values from Cargo.toml, including workspace members.
    Distinct values are AND-joined because the tarball ships all of them."""
    cargo = src_dir / "Cargo.toml"
    if not cargo.is_file():
        return None
    data = read_toml(cargo)
    package = data.get("package") or {}
    workspace = data.get("workspace") or {}
    workspace_package = workspace.get("package") or {}
    values = []

    def add(value):
        if isinstance(value, str) and value.strip() and value.strip() not in values:
            values.append(value.strip())

    add(package.get("license"))
    add(workspace_package.get("license"))
    for license_file in (package.get("license-file"),
                         workspace_package.get("license-file")):
        if isinstance(license_file, str) and (src_dir / license_file).is_file():
            add(_read_license(src_dir / license_file))

    for member in workspace.get("members") or []:
        if not isinstance(member, str):
            continue
        if any(char in member for char in "*?["):
            paths = list(src_dir.glob(member))
        else:
            paths = [src_dir / member]
        for path in paths:
            member_cargo = path / "Cargo.toml"
            if member_cargo.is_file():
                add((read_toml(member_cargo).get("package") or {}).get("license"))

    if not values:
        return None
    # keep `A OR B` from swallowing the AND when it is combined with another
    # member's license
    return " AND ".join(f"({value})" if " " in value else value
                        for value in values)


def source_license(src_dir, info):
    """Return (value, origin) for the upstream project's own license."""
    if info.get("lang") == "rust":
        value = cargo_license(src_dir)
        if value:
            return value, "cargo"

    metadata = info.get("license")
    if isinstance(metadata, str) and metadata.strip():
        return metadata.strip(), "metadata"

    value = file_license(src_dir)
    if value:
        return value, "license-file"
    return None, None
