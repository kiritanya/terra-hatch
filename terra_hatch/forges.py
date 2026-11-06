"""Forge backends: latest release, summary, license, source URL and the
Rhai functions for update.rhai.

Each forge is a class so all of its quirks live in one place. The CLI only
asks the object it got from ``forge_for(host)``; unknown hosts get a
``Unknown`` object whose methods return None.
"""

import http.client
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from . import HTTP_UA


def fetch_json(url):
    """Get a JSON URL. Returns None on any error (offline, 404, ...)."""
    try:
        headers = {"User-Agent": HTTP_UA}
        token = os.environ.get("GITHUB_TOKEN")
        if token and "api.github.com" in url:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except (urllib.error.URLError, urllib.error.HTTPError,
            http.client.HTTPException, ValueError, OSError):
        return None


def fetch_text(url):
    """Get a plain page as text. Returns None on any error."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": HTTP_UA})
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, urllib.error.HTTPError,
            http.client.HTTPException, OSError):
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


def version_key(version):
    """Sort key for the numeric versions version_from_tag() allows."""
    return tuple(int(part) for part in version.split("."))


@dataclass(frozen=True)
class Release:
    """What a forge knows about the newest release."""
    tag: str | None = None
    version_call: str | None = None
    project_id: str | None = None


class Forge:
    """One forge's API shape. Subclasses fill in only the parts they have."""

    name: str | None = None
    hosts: tuple[str, ...] = ()

    def __init__(self, host):
        self.host = host

    @classmethod
    def matches(cls, host):
        return host.lower().removeprefix("www.") in cls.hosts

    def latest(self, owner, repo):
        return Release()

    def summary(self, owner, repo):
        return None

    def license(self, owner, repo):
        return None

    def commit_call(self, owner, repo, project_id):
        return None

    def source_url(self, upstream, repo, source_tag):
        return None


class GitHub(Forge):
    name = "github"
    hosts = ("github.com",)

    def latest(self, owner, repo):
        slug = f"{owner}/{repo}"
        release = fetch_json(
            f"https://api.github.com/repos/{slug}/releases/latest")
        if isinstance(release, dict) and release.get("tag_name"):
            return Release(release["tag_name"], f'gh("{slug}")')
        tags = fetch_json(f"https://api.github.com/repos/{slug}/tags")
        if isinstance(tags, list) and tags and tags[0].get("name"):
            return Release(tags[0]["name"], f'gh_tag("{slug}")')
        return Release()

    def summary(self, owner, repo):
        info = fetch_json(f"https://api.github.com/repos/{owner}/{repo}")
        return info.get("description") if isinstance(info, dict) else None

    def license(self, owner, repo):
        info = fetch_json(f"https://api.github.com/repos/{owner}/{repo}")
        if not isinstance(info, dict):
            return None
        spdx = (info.get("license") or {}).get("spdx_id")
        if spdx and spdx not in ("NOASSERTION", "Other"):
            return spdx
        return None

    def commit_call(self, owner, repo, project_id):
        return f'gh_commit("{owner}/{repo}")'

    def source_url(self, upstream, repo, source_tag):
        if not source_tag or source_tag == "TODO":
            return None
        return f"{upstream}/archive/refs/tags/{source_tag}.tar.gz"


class GitLab(Forge):
    name = "gitlab"

    @classmethod
    def matches(cls, host):
        return "gitlab" in host.lower()

    def project(self, owner, repo):
        path = urllib.parse.quote(f"{owner}/{repo}", safe="")
        info = fetch_json(f"https://{self.host}/api/v4/projects/{path}")
        return info if isinstance(info, dict) else None

    def latest(self, owner, repo):
        project = self.project(owner, repo)
        if not project or not project.get("id"):
            return Release()
        project_id = str(project["id"])
        releases = fetch_json(
            f"https://{self.host}/api/v4/projects/{project_id}"
            "/releases?per_page=1")
        if isinstance(releases, list) and releases \
                and releases[0].get("tag_name"):
            return Release(releases[0]["tag_name"],
                           f"gitlab({project_id})", project_id)
        return Release(None, f"gitlab_tag({project_id})", project_id)

    def summary(self, owner, repo):
        project = self.project(owner, repo)
        return project.get("description") if project else None

    def commit_call(self, owner, repo, project_id):
        return f'gitlab_commit("{self.host}", "{project_id}", "main")'

    def source_url(self, upstream, repo, source_tag):
        if not source_tag or source_tag == "TODO":
            return None
        return f"{upstream}/-/archive/{source_tag}/{repo}-{source_tag}.tar.gz"


class Codeberg(Forge):
    name = "codeberg"
    hosts = ("codeberg.org",)

    def latest(self, owner, repo):
        slug = f"{owner}/{repo}"
        release = fetch_json(
            f"https://codeberg.org/api/v1/repos/{slug}/releases?limit=1")
        if isinstance(release, list) and release \
                and release[0].get("tag_name"):
            return Release(release[0]["tag_name"], f'codeberg("{slug}")')
        tags = fetch_json(f"https://codeberg.org/api/v1/repos/{slug}/tags")
        if isinstance(tags, list) and tags and tags[0].get("name"):
            return Release(tags[0]["name"], f'codeberg_tag("{slug}")')
        return Release()

    def summary(self, owner, repo):
        info = fetch_json(f"https://codeberg.org/api/v1/repos/{owner}/{repo}")
        return info.get("description") if isinstance(info, dict) else None

    def commit_call(self, owner, repo, project_id):
        return f'codeberg_commit("{owner}/{repo}")'

    def source_url(self, upstream, repo, source_tag):
        if not source_tag or source_tag == "TODO":
            return None
        return f"{upstream}/archive/{source_tag}.tar.gz"


class Sourcehut(Forge):
    name = "sourcehut"
    hosts = ("git.sr.ht",)

    def latest(self, owner, repo):
        call = f'sourcehut("{owner}/{repo}")'
        return Release(self._latest_tag(owner, repo), call)

    def _latest_tag(self, owner, repo):
        """Newest clean version tag (no rc/beta suffix). Sourcehut has no
        public releases API, so ask git directly."""
        git_bin = shutil.which("git")
        if not git_bin:
            return None
        try:
            result = subprocess.run(
                [git_bin, "ls-remote", "--tags", "--refs",
                 f"https://{self.host}/{owner}/{repo}"],
                capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        candidates = []
        for line in result.stdout.splitlines():
            _, _, ref = line.partition("\t")
            if not ref.startswith("refs/tags/"):
                continue
            tag = ref.removeprefix("refs/tags/")
            version = version_from_tag(tag)
            if not version or source_macro(tag, version) is None:
                continue
            candidates.append((version_key(version), tag))
        return max(candidates)[1] if candidates else None

    def summary(self, owner, repo):
        """Sourcehut has no JSON API for this; the repo page carries the
        one-line description in its header."""
        html = fetch_text(f"https://{self.host}/{owner}/{repo}")
        if not html:
            return None
        match = re.search(
            r'<div class="header-extension">.*?'
            r'<div class="col-md-12">\s*(.*?)\s*</div>',
            html, re.S)
        if not match:
            return None
        text = re.sub(r"<[^>]+>", "", match.group(1))
        return " ".join(text.split()) or None

    def commit_call(self, owner, repo, project_id):
        return f'sourcehut_commit("{owner}/{repo}")'

    def source_url(self, upstream, repo, source_tag):
        if not source_tag or source_tag == "TODO":
            return None
        return f"{upstream}/archive/{source_tag}.tar.gz"


class Unknown(Forge):
    """A host we have no backend for. Every lookup stays None."""


FORGES = (GitHub, GitLab, Codeberg, Sourcehut)


def forge_for(host):
    """Pick the backend for this hostname."""
    for forge_class in FORGES:
        if forge_class.matches(host):
            return forge_class(host)
    return Unknown(host)
