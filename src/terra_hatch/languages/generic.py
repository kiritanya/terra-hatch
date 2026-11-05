"""Generic fallback for repos no other sniffer knows."""


ROW = {
    "template": "generic",
    "registry": None,
    "prefix": "",
}

TIPS = [
    "Language not auto-detected — %prep/%build/%install are all TODO.",
]


def sniff(src_dir, repo_name):
    return {"lang": "generic", "name": repo_name, "url": None}
