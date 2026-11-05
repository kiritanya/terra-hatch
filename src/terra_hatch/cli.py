#!/usr/bin/env python3
"""terra-hatch CLI: write a starter Terra package (anda.hcl, <name>.spec,
update.rhai).

Language support is data-driven and lives in ``languages.py``. This module
holds the forge helpers and the main flow.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

from . import HTTP_UA, NAME, __version__
from .languages import EXTRAS, LANGUAGES, LANG_TIPS, REGISTRY_APIS, sniff_repository
from .log import die, log, warn

CATEGORIES = [
    "apps", "buildsys", "desktops", "devs", "fonts", "games", "langs", "lib",
    "misc", "multimedia", "system", "terra", "themes", "tools",
]

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
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError):
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


def registry_update_call(lang, name):
    """Check crates.io / PyPI for the project. Terra guidelines prefer
    registry checks over GitHub API calls, so we try these first."""
    registry = LANGUAGES[lang].get("registry")
    if not registry or not name:
        return None
    url, truthy_field = REGISTRY_APIS[registry]
    info = fetch_json(url.format(name=name))
    if isinstance(info, dict) and info.get(truthy_field):
        return f'{registry}("{name}")'
    return None


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
        if isinstance(info, dict):
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
    parser.add_argument("--category", choices=CATEGORIES, default="misc",
                        help=f"target Anda category (default: misc; choices: {', '.join(CATEGORIES)})")
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
    parser.add_argument("--no-update", action="store_true", help="skip update.rhai generation")
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
    if "TODO" in ctx["version"]:
        tips.append("`Version:` is a TODO — find the latest release and set it.")
    if "TODO" in ctx["license"]:
        hint = ", ".join(source["top_licenses"]) or "no LICENSE file found at the repo root"
        tips.append(f"`License:` is a TODO ({hint}) — set the correct SPDX id.")
    elif ctx["license_from_registry"]:
        tips.append("`License:` came from the forge/registry — check that it is right.")
    if not ctx["packager"]:
        tips.append("`Packager:` — put your own name and email here.")
    if (ctx.get("source_tag") or "TODO") == "TODO":
        tips.append("`Source0:`/`Source:` tag name is a TODO — check upstream tags.")
    tips.extend(LANG_TIPS.get(lang, []))
    if source.get("workspace"):
        tips.append("This is a Cargo workspace — pick the subcrate you want; the spec still points at the repo root.")
    if lang == "go" and source.get("main_dir") is None:
        tips.append("Go: no `package main` found — %gobuild may need another module path.")
    if ctx["update_call"] is None and not args.no_update:
        tips.append("update.rhai — no version-check function could be picked. Write one by hand.")
    tips.append("Follow the Terra packaging policies before you open a PR.")
    return tips


def check_registry_call(lang, source, args):
    registry_name = args.crate_name or args.pypi_name or source["name"]
    return registry_update_call(lang, registry_name)


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

        tag, version_call, gitlab_id = fetch_latest(forge, host, owner, repo)
        if forge is None:
            warn(f"This forge ({host}) has no auto release check. "
                 "Version and update.rhai stay TODO. AndaX also has "
                 "sourcehut/tangled/gitea/forgejo/git_tags helpers.")
        elif version_call:
            log(f"Version tracking: {version_call}")

        update_call = version_call
        if lang in ("rust", "python"):
            registry_call = check_registry_call(lang, source, args)
            if registry_call and registry_call != update_call:
                log(f"Version tracking: {registry_call} (guidelines prefer registry tracking)")
                update_call = registry_call

        package_name = sanitize_name(args.name or source["name"] or repo.lower())
        spec_name = spec_name_of(lang, package_name, args.category)

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
                                        gitlab_id or "TODO_PROJECT_ID") if args.nightly else None
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

        package_dir = Path(args.root) / args.category / package_name
        template_name = f"{LANGUAGES[lang]['template']}.spec.j2"
        files = {
            package_dir / f"{spec_name}.spec": render(template_name, **template_vars(source, ctx, args)),
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
        print(f"  - Build: anda build -c terra-rawhide-x86_64 anda/{args.category}/{package_name}/pkg")
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
