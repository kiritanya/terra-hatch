"""The `%changelog` block for generated specs.

Terra policies ask for a hand-written changelog entry; the Fedora spec
tools emit `%autochangelog` instead. This module is the single source for
both the templates (`{{ changelog }}`) and the spec-tool rewrite, so the
two paths cannot drift apart.
"""


def entry(today, packager, version=None, release=1):
    """One changelog entry: the header line plus its first item."""
    header = f"* {today} {packager}"
    if version and version != "TODO":
        header += f" - {version}-{release}"
    return f"{header}\n- initial package"


def block(today, packager, version=None, release=1):
    """The whole section, without a trailing newline (templates add it)."""
    return "%changelog\n" + entry(today, packager, version, release)
