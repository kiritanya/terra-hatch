#!/usr/bin/env python3
"""terra-hatch CLI: write a starter Terra package (anda.hcl, <name>.spec,
update.rhai).

Language support is data-driven and lives in ``languages.py``. This module
holds the forge helpers and the main flow.
"""

import argparse
import http.client
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

from . import HTTP_UA, NAME, __version__
from .languages import EXTRAS, LANGUAGES, LANG_TIPS, REGISTRY_APIS, sniff_repository
from .log import die, log, warn

# suggested folder per detected language (used only when present in the
# discovered list); every other language is suggested without a default
DEFAULT_CATEGORY = {"go": "langs", "rust": "langs", "python": "langs"}

# forge table
FORGE_HOSTS = {"github": "github.com", "codeberg": "codeberg.org"}

# version-check calls per forge. We fill `{owner}`/`{repo}` later.
# gitlab is special: it needs a numeric project id (see forge_version()).
FORGE_VERSION_CALLS = {
    "github": "gh(\"{owner}/{repo}\")",
    "codeberg": "codeberg(\"{owner}/{repo}\")",
}
FORGE_VERSION_TAG_CALLS = {
    "github": "gh_tag(\"{owner}/{repo}\")",
    "codeberg": "codeberg_tag(\"{owner}/{repo}\")",
}
FORGE_COMMIT_CALLS = {
    "github": "gh_commit(\"{owner}/{repo}\")",
    "codeberg": "codeberg_commit(\"{owner}/{repo}\")",
    "gitlab": "gitlab_commit(\"{host}\", \"{gitlab_id}\", \"main\")",
}

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def fetch_json(url):
    """Get a JSON URL. Returns None on any error (offline, 404, ...)."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": HTTP_UA})
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except (urllib.error.URLError, urllib.error.HTTPError, http.client.HTTPException,
            ValueError, OSError):
        return None



# ---------------------------------------------------------------------------
# forge / registry helpers
# ---------------------------------------------------------------------------

def detect_forge(netloc):
    """Which forge is this URL on? Returns None for unknown names."""
    forge = None
    for name, host in FORGE_HOSTS.items():
        if netloc.lower() == host:
            forge = name
    if forge is None and "gitlab" in netloc.lower():
        forge = "gitlab"
    return forge


def fetch_latest(forge, host, owner, repo):
    """Get the latest release (or tag) from the forge.
    Returns (tag, rhai version call, gitlab project id). The version call is
    a full Rhai expression, like `gh("owner/repo")`."""
    if forge == "gitlab":
        project_path = urllib.parse.quote(f"{owner}/{repo}", safe="")
        project = fetch_json(f"https://{host}/api/v4/projects/{project_path}")
        if isinstance(project, dict) and project.get("id"):
            project_id = str(project["id"])
            releases = fetch_json(
                f"https://{host}/api/v4/projects/{project_id}/releases?per_page=1")
            if isinstance(releases, list) and releases and releases[0].get("tag_name"):
                return releases[0]["tag_name"], f"gitlab({project_id})", project_id
            return None, f"gitlab_tag({project_id})", project_id
        return None, None, None

    slug = f'"{owner}/{repo}"'
    if forge == "github":
        release = fetch_json(f"https://api.github.com/repos/{owner}/{repo}/releases/latest")
        if isinstance(release, dict) and release.get("tag_name"):
            return release["tag_name"], FORGE_VERSION_CALLS[forge].format(owner=owner, repo=repo), None
        tags = fetch_json(f"https://api.github.com/repos/{owner}/{repo}/tags")
    elif forge == "codeberg":
        release = fetch_json(f"https://codeberg.org/api/v1/repos/{owner}/{repo}/releases?limit=1")
        if isinstance(release, list) and release and release[0].get("tag_name"):
            return release[0]["tag_name"], FORGE_VERSION_CALLS[forge].format(owner=owner, repo=repo), None
        release = None
        tags = fetch_json(f"https://codeberg.org/api/v1/repos/{owner}/{repo}/tags")
    else:
        return None, None, None

    if isinstance(tags, list) and tags:
        return tags[0].get("name"), FORGE_VERSION_TAG_CALLS[forge].format(owner=owner, repo=repo), None
    return None, None, None


def forge_commit_call(host, forge, owner, repo, gitlab_id):
    template = FORGE_COMMIT_CALLS.get(forge)
    if not template:
        return None
    return template.format(host=host, owner=owner, repo=repo, gitlab_id=gitlab_id)


def registry_links_to(info, top, match):
    """Does the registry entry link back to the upstream repo we are
    packaging? Protects against name collisions on PyPI/crates."""
    entry = info.get(top) or {}
    urls = [entry.get("repository"), entry.get("homepage"), entry.get("home_page"),
            entry.get("docs_url"), entry.get("bugtrack_url")]
    project_urls = entry.get("project_urls") or {}
    if isinstance(project_urls, dict):
        urls.extend(project_urls.values())
    return any(isinstance(u, str) and match in u for u in urls)


def registry_entry_exists(lang, name, match=None):
    """Check the registry, with the upstream collision check. Returns the
    info dict, or None."""
    registry = LANGUAGES[lang].get("registry")
    if not registry or not name:
        return None
    url, truthy_field = REGISTRY_APIS[registry]
    info = fetch_json(url.format(name=urllib.parse.quote(name)))
    if not (isinstance(info, dict) and info.get(truthy_field)):
        return None
    if match and not registry_links_to(info, truthy_field, match):
        warn(f'The {registry} entry "{name}" does not link back to {match}; '
             "not using it for tracking (it may be a project with the same "
             "name). Pass --pypi-name/--crate-name if the registry uses "
             "another name.")
        return None
    return info


def registry_update_call(lang, name, match=None):
    """Check crates.io / PyPI for the project. Terra guidelines prefer
    registry checks over GitHub API calls, so we try these first."""
    registry = LANGUAGES[lang].get("registry")
    if registry_entry_exists(lang, name, match):
        return f'{registry}("{name}")'
    return None


def insert_packager(text, packager):
    """Terra spec files carry a Packager tag; the Fedora tools do not add
    one. Put it right after the License line."""
    if "Packager:" in text:
        return text
    return re.sub(r"^(License: .*)$",
                  lambda m: m.group(1) + "\nPackager:       " + packager,
                  text, count=1, flags=re.MULTILINE)


def terraify_spec(text, packager):
    """Small edits the Terra policies require but the Fedora spec tools
    (pyp2spec / go2rpm) do not make: the `Packager:` tag and a manual
    `Release: 1%{?dist}` instead of `%autorelease`."""
    return insert_packager(
        re.sub(r"^(Release:\s+)%autorelease\s*$", r"\g<1>1%{?dist}",
               text, count=1, flags=re.MULTILINE), packager)


def fetch_spec_from_tool(tool, spec_args):
    """Run a spec tool (`pyp2spec` / `go2rpm`) in a scratch dir and return
    (spec file name, contents, tool name), or None on any problem. The
    built-in template takes over when the tool fails."""
    binary = shutil.which(tool)
    if not binary:
        warn(f"{tool} is not installed; using the built-in template. "
             f"`dnf install {tool}`.")
        return None
    with tempfile.TemporaryDirectory(prefix=f"{NAME}-{tool}-") as work:
        try:
            result = subprocess.run([binary, *spec_args], cwd=work,
                                    capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as err:
            warn(f"{tool} failed ({err}); using the built-in template.")
            return None
        if result.returncode != 0:
            warn(f"{tool} failed; using the built-in template.\n"
                 + result.stderr.strip())
            return None
        spec_files = [p for p in Path(work).glob("*.spec")]
        if not spec_files:
            warn(f"{tool} wrote no spec file; using the built-in template.")
            return None
        spec_path = spec_files[0]
        return spec_path.name.removesuffix(".spec"), \
            spec_path.read_text(encoding="utf-8", errors="replace"), tool


def forge_license(forge, host, owner, repo, lang):
    """Try to get the SPDX license id as it appears in the forge/registry.
    Facts only: we return None when we cannot check, so no license is made up."""
    if forge == "github":
        info = fetch_json(f"https://api.github.com/repos/{owner}/{repo}")
        if isinstance(info, dict):
            spdx = (info.get("license") or {}).get("spdx_id")
            if spdx and spdx not in ("NOASSERTION", "Other"):
                return spdx
    registry = LANGUAGES[lang].get("registry")
    if registry:
        url, top = REGISTRY_APIS[registry]
        info = fetch_json(url.format(name=repo))
        if isinstance(info, dict) and registry_links_to(info, top, f"{host}/{owner}/{repo}"):
            license_value = (info.get(top) or {}).get("license")
            if license_value:
                return license_value
    del host
    return None


def version_from_tag(tag):
    """Drop the usual `v` prefix from a release tag. Returns None when the
    tag does not look like a plain version, so we never invent a number."""
    if not tag:
        return None
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)[-.]?(\w*)", tag)
    if not match:
        return None
    return match.group(1)


def source_macro(tag, version):
    """Turn a release tag into a `Source0:` pattern like `v%{version}` when
    we are sure. Otherwise leave a TODO so nothing is invented."""
    if not tag:
        return "TODO"
    if tag == f"v{version}":
        return "v%{version}"
    if tag == version:
        return "%{version}"
    return None


def forge_source_url(forge, upstream, repo, tag):
    """Tarball URL for a release tag, shaped per forge (github/codeberg/
    gitlab differ). Returns None when we cannot build one, so the
    template leaves a TODO."""
    if not tag:
        return None
    if forge == "github":
        return f"{upstream}/archive/refs/tags/{tag}.tar.gz"
    if forge == "codeberg":
        return f"{upstream}/archive/{tag}.tar.gz"
    if forge == "gitlab":
        return f"{upstream}/-/archive/{tag}/{repo}-{tag}.tar.gz"
    return None


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

env = Environment(
    loader=PackageLoader("terra_hatch", "templates"),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    undefined=StrictUndefined,
)


def render(template_name, **variables):
    return env.get_template(template_name).render(**variables)


def has_package_files(path):
    """Does the folder directly hold package files (a spec, anda.hcl or
    update.rhai)? Terminal package folders do; aggregate and category
    folders do not."""
    if (path / "anda.hcl").exists() or (path / "update.rhai").exists():
        return True
    return any(path.glob("*.spec"))


# the build-variant layout: `<pkg>/nightly/…` next to `<pkg>/stable/…`
BUILD_VARIANT_NAMES = {"nightly", "stable", "weekly", "quaterdaily", "updbranch"}


def looks_like_subcategory(path):
    """Is a nested folder a destination worth offering (like
    desktops/stardust or langs/rust)? Only the build-variant names are
    known by name — everything else is judged by shape: drop build-variant
    groups (`nightly/` + `stable/`, one piece of software packaged in
    several tracks). Folders that merely group one software's own
    subpackages may still appear; they are real folders and harmless to
    offer."""
    children = [entry for entry in path.iterdir()
                if entry.is_dir() and not entry.name.startswith(".")]
    if not children:
        return False
    if all(child.name.lower() in BUILD_VARIANT_NAMES for child in children):
        return False
    return True


def discover_categories(root):
    """Find the folder options to offer: the subfolders of the working
    `--root` directory (your packages checkout, e.g. terrapkg's packages
    repo or a tuatara one). The Terra repo nests three levels — category,
    subcategory, package — so subfolders make the menu only when they look
    like a subcategory: no package files of their own and at least one
    grandchild shaped like an aggregate folder (its own children hold no
    package files). Build groups like a `nightly/`+`stable/` pair drop out
    this way, without any hardcoded folder names. Returns a list of
    (name, path) pairs, or None when the folder holds nothing."""
    root_dir = Path(root)
    options = []
    if root_dir.is_dir():
        for entry in sorted(root_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            options.append((entry.name, entry.name))
            for sub in sorted(entry.iterdir()):
                if not sub.is_dir() or sub.name.startswith(".") \
                        or has_package_files(sub):
                    continue
                if looks_like_subcategory(sub):
                    options.append((sub.name, f"{entry.name}/{sub.name}"))
    return options or None


def ask_category(options, lang):
    """Ask which Anda folder the package goes in. The (name, path) options
    come from discover_categories(). A piped stdin also works (one choice)."""
    default = DEFAULT_CATEGORY.get(lang)
    if default and default not in {name for name, _ in options}:
        default = None
    print("Which Anda folder does this package go in?")
    for index, (name, path) in enumerate(options, 1):
        line = f"{index:>4}) {name}" + (f"  ({path})" if path != name else "")
        if name == default:
            line += " — default"
        print(line)
    hint = f", Enter = {default}" if default else ""
    while True:
        try:
            answer = input(f"Folder number or name{hint}: ")
        except (EOFError, OSError):
            die("No category choice (stdin closed). Run terra-hatch in a "
                "terminal or pipe one choice: `echo apps | terra-hatch ...`")
        answer = answer.strip().lower()
        if not answer and default:
            for name, path in options:
                if name == default:
                    return path
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][1]
        for name, path in options:
            if answer in (name, path):
                return path
        print(f"Unknown folder {answer!r}; pick one from the list.")


def spec_name_of(lang, package_name, category):
    """The spec file name must match the `Name:` inside (mock constraint)."""
    prefix = LANGUAGES[lang].get("prefix", "")
    if prefix and not package_name.startswith(prefix):
        package_name = prefix + package_name
    if category == "fonts" and not package_name.endswith("-fonts"):
        package_name = f"{package_name}-fonts"
    return package_name


def template_vars(source, ctx, args):
    """Build the values every template uses, then add the language extras."""
    lang = source["lang"]
    base = {
        **ctx,
        "name": ctx["name"],
        "goipath": source.get("goipath") or "",
        "source_tag": ctx["source_tag"],
        "licenses": " ".join(source["top_licenses"] or ["TODO LICENSE-FILE"]),
        "docs": " ".join(source["top_docs"] or ["TODO README-FILE"]),
        "url": ctx["upstream"],
        "nightly": args.nightly,
        "weekly": args.weekly,
        "quaterdaily": args.quaterdaily,
        "subrepo": args.subrepo,
        "source_license": source.get("license") or "TODO SPDX-ID",
        "update_call": ctx["update_call"],
        "commit_call": ctx["commit_call"],
        "gitlab_id": ctx["gitlab_id"],
        "host": ctx["host"],
        "forge": ctx["forge"],
        "unpack_dir": f"{ctx['name']}-%{{version}}",
    }
    extras_fn = EXTRAS.get(lang)
    if extras_fn:
        base.update(extras_fn(source, base))
    return base


def init_root_manifest(args):
    """Write the root anda.hcl for anda builds. anda strips `<root>/` and
    `/pkg` from the build target. Skips when the file already exists."""
    root_dir = Path(args.root)
    manifest = root_dir.parent / "anda.hcl"
    if manifest.exists():
        log(f"Root manifest already exists: {manifest}")
        return manifest
    strip_prefix = f"{root_dir.name}/"
    content = ('config {\n\tstrip_prefix = "%s"\n\tstrip_suffix = "/pkg"\n}\n\n'
               'project {}\n' % strip_prefix)
    if args.dry_run:
        log(f"DRY RUN — would write: {manifest}")
    else:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(content, encoding="utf-8")
        log(f"Wrote {manifest}")
    return manifest


# ---------------------------------------------------------------------------
# main flow
# ---------------------------------------------------------------------------

def guess_packager(git_bin):
    if not git_bin:
        return None
    name = subprocess.run([git_bin, "config", "--global", "user.name"],
                          capture_output=True, text=True).stdout.strip()
    email = subprocess.run([git_bin, "config", "--global", "user.email"],
                           capture_output=True, text=True).stdout.strip()
    return f"{name} <{email}>" if name and email else None


def guess_today():
    return subprocess.run(["date", "+%a %b %d %Y"], capture_output=True,
                          text=True).stdout.strip()


def parse_url(url):
    """Read the repo URL and return (url, host, forge, owner, repo).
    GitHub owner/repo shorthand like `muesli/duf` works too."""
    url = url.strip()
    if "://" not in url and re.fullmatch(r"[\w.-]+/[\w.-]+", url):
        url = f"https://github.com/{url}"
    shorthand = re.fullmatch(
        r"(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/.*)?", url)
    if shorthand:
        owner, repo = shorthand.group(1), shorthand.group(2)
        return (f"https://github.com/{owner}/{repo}",
                "github.com", "github", owner, repo)
    split = urllib.parse.urlsplit(url if "://" in url else f"https://{url}")
    if not split.netloc:
        die(f"Could not parse the repository URL {url!r}.")
    path = split.path.strip("/").removesuffix(".git")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        die(f"Could not find owner/repo in {url!r}.")
    owner, repo = parts[0], parts[1]
    return (f"https://{split.netloc}/{owner}/{repo}",
            split.netloc, detect_forge(split.netloc), owner, repo)


def sanitize_name(name):
    """Keep only the characters RPM names accept."""
    return re.sub(r"[^A-Za-z0-9._+-]", "_", name)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog=NAME,
        description=f"{NAME} (v{__version__}): write a starter Terra package "
                    "(anda.hcl + spec + update.rhai) from an upstream repo URL.",
        epilog="You must check every TODO in the output before you build or "
               "open a PR. See the Terra packaging docs.")
    parser.add_argument("url",
                        help="upstream repository URL (GitHub owner/repo shorthand works too)")
    parser.add_argument("--root", metavar="DIR", default="anda",
                        help="directory holding the Anda category folders (default: anda)")
    parser.add_argument("--name", help="RPM package name (default: from upstream metadata or repo name)")
    parser.add_argument("--license", help="override the license detected upstream")
    parser.add_argument("--summary", help="override the summary detected upstream")
    parser.add_argument("--version", help="override the initial Version")
    parser.add_argument("--packager", metavar='"Name <email>"',
                        help="Packager tag (default: from your git config)")
    parser.add_argument("--nightly", action="store_true",
                        help="make a nightly package (update.rhai tracks commits)")
    parser.add_argument("--weekly", metavar="0-6", type=int,
                        help="make a weekly auto-update package; the number is the day 0-6")
    parser.add_argument("--quaterdaily", action="store_true",
                        help="make a quaterdaily auto-update package "
                             "(updated every 6 hours; the spec needs the "
                             "quaterdaily version macros)")
    parser.add_argument("--subrepo",
                        choices=["extras", "nvidia", "mesa", "multimedia"],
                        help="add the `subrepo` label to anda.hcl (for packages "
                             "that must ship in a Terra subrepo)")
    parser.add_argument("--no-update", action="store_true", help="skip update.rhai generation")
    parser.add_argument("--no-spec-tools", "--no-pyp2spec", dest="no_spec_tools",
                        action="store_true", help="never delegate the spec to the "
                        "Fedora tools (pyp2spec / go2rpm); always use the built-in templates")
    parser.add_argument("--pypi-name", help="PyPI project name if it differs from the repo name")
    parser.add_argument("--crate-name", help="crates.io crate name if it differs from the repo name")
    parser.add_argument("--force", action="store_true", help="overwrite files if the target exists")
    parser.add_argument("--dry-run", action="store_true", help="only print what would be written")
    parser.add_argument("--init-root", action="store_true",
                        help="create the default root anda.hcl (with strip_prefix) if it does not exist")
    parser.add_argument("--keep-clone", action="store_true",
                        help="keep the upstream shallow clone dir and print its path")
    return parser.parse_args(argv)


def review_checklist(source, ctx, args):
    lang = source["lang"]
    tips = []
    if source.get("legacy_setup_py"):
        tips.append("Legacy `setup.py` project: check that %pyproject_wheel still works with the setuptools backend.")
    if ctx.get("by_spec_tool"):
        tips.append(f'Spec generated by {ctx["by_spec_tool"]} — read its own '
                    "review comments (License FIXME, summary, description) too.")
    if ctx.get("by_spec_tool") and lang == "go":
        tips.append("go2rpm naming: rename the package (spec file and `Name:`) "
                    "to a sensible short name; you MAY keep the long go2rpm "
                    "name in `Provides:` (Terra Go guidelines).")
    if "TODO" in ctx["version"]:
        tips.append("`Version:` is a TODO — find the latest release and set it.")
    if "TODO" in ctx["license"]:
        hint = ", ".join(source["top_licenses"]) or "no LICENSE file found at the repo root"
        tips.append(f"`License:` is a TODO ({hint}) — set the correct SPDX id.")
    elif ctx["license_from_registry"]:
        tips.append("`License:` came from the forge/registry — check that it is right.")
    if not ctx["packager"]:
        tips.append("`Packager:` — put your own name and email here.")
    if ctx["source_url"] is None and not ctx.get("by_spec_tool"):
        tips.append("`Source0:`/`Source:` is a TODO — find the upstream release tag and set the tarball URL.")
    elif (ctx.get("source_tag") or "TODO") == "TODO":
        tips.append(f"Upstream tag {ctx.get('tag')!r} is not a plain version; set a `Source0:` URL/pattern that matches it.")
    if args.nightly or args.quaterdaily:
        freq = "quaterdaily" if args.quaterdaily else "nightly"
        tips.append(f"{freq.capitalize()} commit tracking: set `Version:` to the "
                    "commit-tracking format from the Terra policies (for example "
                    "`%{unreleased_version}~%{commit_date}git.%{shortcommit}`) and "
                    "define the matching macros in the spec.")
    tips.extend(LANG_TIPS.get(lang, []))
    if source.get("workspace"):
        tips.append("This is a Cargo workspace — pick the subcrate you want; the spec still points at the repo root.")
    if lang == "go" and source.get("main_dir") is None:
        tips.append("Go: no `package main` found — %gobuild may need another module path.")
    if ctx["update_call"] is None and not args.no_update:
        tips.append("update.rhai — no version-check function could be picked. Write one by hand.")
    tips.append("Follow the Terra packaging policies before you open a PR.")
    return tips


def check_registry_call(lang, source, args, host, owner, repo):
    registry_name = args.crate_name or args.pypi_name or source["name"]
    match = f"{host}/{owner}/{repo}"
    return registry_update_call(lang, registry_name, match=match)


def main(argv=None):
    args = parse_args(argv)
    git_bin = shutil.which("git")
    if not git_bin:
        die("`git` must be installed to clone the upstream sources.")

    upstream, host, forge, owner, repo = parse_url(args.url)
    if args.init_root:
        init_root_manifest(args)
    log(f"Shallow-cloning {upstream}")
    clone_dir = Path(tempfile.mkdtemp(prefix=f"{NAME}-clone-"))
    try:
        result = subprocess.run([git_bin, "clone", "--depth", "1", "--quiet", upstream, str(clone_dir)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            die(f"Cloning {upstream} failed:\n{result.stderr.strip()}")

        source = sniff_repository(clone_dir, repo)
        lang = source["lang"]
        log(f"Detected language: {lang}")

        options = discover_categories(args.root)
        if options is None:
            die(f"{args.root}/ holds no category folders. Run terra-hatch "
                "inside your packages checkout (the folder with apps/, "
                "tools/, ...) so terra-hatch can offer the real folders.")
        category = ask_category(options, lang)
        log(f"Target folder: {args.root}/{category}")

        tag, version_call, gitlab_id = fetch_latest(forge, host, owner, repo)
        if forge is None:
            warn(f"This forge ({host}) has no auto release check. "
                 "Version and update.rhai stay TODO. AndaX also has "
                 "sourcehut/tangled/gitea/forgejo/git_tags helpers.")
        elif version_call:
            log(f"Version tracking: {version_call}")

        update_call = version_call
        registry_call = None
        if lang in ("rust", "python"):
            registry_call = check_registry_call(lang, source, args, host, owner, repo)
            if registry_call and registry_call != update_call:
                log(f"Version tracking: {registry_call} (guidelines prefer registry tracking)")
                update_call = registry_call

        package_name = sanitize_name(args.name or source["name"] or repo.lower())
        spec_name = spec_name_of(lang, package_name, category)

        version = args.version or version_from_tag(tag) or "TODO"
        if version == "TODO":
            warn("Could not find the latest version; `Version:` stays TODO.")

        source_tag = source_macro(tag, version)
        if source_tag is None:
            source_tag = "TODO"
            warn(f"Upstream tag {tag!r} is not a plain version; the Source tag stays TODO.")

        license_value = args.license or source.get("license")
        license_from_registry = False
        if not license_value:
            license_value = forge_license(forge, host, owner, repo, lang)
            license_from_registry = license_value is not None
            if license_from_registry:
                log(f"License auto-detected: {license_value}")
        license_value = license_value or "TODO"
        summary = args.summary or source.get("description") or "TODO summary"
        description = source.get("description") or summary
        packager = args.packager or guess_packager(git_bin) or "TODO Your Name <you@example.com>"

        commit_call = forge_commit_call(host, forge, owner, repo,
                                        gitlab_id or "TODO_PROJECT_ID")
        if not (args.nightly or args.quaterdaily):
            commit_call = None
        source_url = forge_source_url(forge, upstream, repo, tag)
        if source_url:
            log(f"Source0 URL: {source_url}")
        ctx = {
            "name": package_name,
            "spec_name": spec_name,
            "upstream": upstream,
            "host": host,
            "forge": forge,
            "owner": owner,
            "repo": repo,
            "version": version,
            "tag": tag,
            "source_tag": source_tag,
            "source_url": source_url,
            "license": license_value,
            "license_from_registry": license_from_registry,
            "summary": summary,
            "description": description,
            "packager": packager,
            "today": guess_today(),
            "update_call": update_call,
            "commit_call": commit_call,
            "gitlab_id": gitlab_id or "TODO_PROJECT_ID",
        }

        package_dir = Path(args.root) / category / package_name
        # the sniffer may pick a special template (for example setup.py
        # projects use python-setup instead of the pyproject one)
        template_key = source.get("template") or LANGUAGES[lang]["template"]
        template_name = f"{template_key}.spec.j2"

        # spec tools first: the Fedora tools (pyp2spec / go2rpm, declared per
        # language in the LANGUAGES rows) make the spec when they are
        # installed and the facts check out; the built-in template takes
        # over for anything else.
        spec_contents = None
        spec_tool = LANGUAGES[lang].get("tool")
        delegate = None
        if spec_tool and not args.no_spec_tools and not args.dry_run:
            if lang == "go" and source.get("goipath"):
                delegate = fetch_spec_from_tool(spec_tool, [source["goipath"]])
            elif lang == "python" and registry_call:
                delegate = fetch_spec_from_tool(spec_tool,
                                                [args.pypi_name or source["name"]])
        if delegate:
            spec_name, spec_contents, used_tool = delegate
            spec_contents = terraify_spec(spec_contents, packager)
            ctx["spec_name"] = spec_name
            ctx["by_spec_tool"] = used_tool
            log(f"Spec generated by {used_tool}: {spec_name}.spec")
        elif args.dry_run and spec_tool and not args.no_spec_tools:
            if lang == "go" and source.get("goipath"):
                log("DRY RUN — would generate the spec with " + spec_tool)
            elif lang == "python" and registry_call:
                log("DRY RUN — would generate the spec with " + spec_tool)

        files = {
            package_dir / f"{spec_name}.spec": spec_contents
            if spec_contents is not None
            else render(template_name, **template_vars(source, ctx, args)),
            package_dir / "anda.hcl": render("anda.hcl.j2", **template_vars(source, ctx, args)),
        }
        if not args.no_update:
            files[package_dir / "update.rhai"] = render("update.rhai.j2", **template_vars(source, ctx, args))

        if args.dry_run:
            log("DRY RUN — would write:")
            for path in files:
                print(f"  {path}")
            return 0
        if package_dir.exists() and not args.force:
            die(f"{package_dir} already exists (use --force to overwrite).")
        for path, contents in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")
            log(f"Wrote {path}")

        print("\nReview checklist (check every TODO yourself):")
        for tip in review_checklist(source, ctx, args):
            print(f"  - {tip}")
        print(f"  - Build: anda build -c terra-rawhide-x86_64 {args.root}/{category}/{package_name}/pkg")
        print("  - These files are only a start template. Review them; the Terra "
              "packaging policies apply to anything you submit.")
        return 0
    finally:
        if args.keep_clone:
            log(f"Clone kept for inspection: {clone_dir}")
        else:
            shutil.rmtree(clone_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
