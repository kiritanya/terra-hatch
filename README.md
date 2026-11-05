# terra-hatch

Hatch new Terra packages.

```
terra-hatch https://github.com/hashicorp/terraform-ls
```

writes `anda.hcl`, `<name>.spec`, `update.rhai` for the Terra packages repo.
Japanese docs: [README.ja.md](README.ja.md).

## Install

```sh
pip install .
```

## Use

```sh
terra-hatch https://github.com/azat/chdig --category apps   # pick folder
terra-hatch owner/repo --nightly                            # commit tracking
terra-hatch owner/repo --dry-run                            # just look
terra-hatch owner/repo --no-pyp2spec                        # never use pyp2spec
```

## What it does

- detects the language (`go.mod` / `Cargo.toml` / `pyproject.toml` / other)
- fills in real facts: latest version, license (SPDX), summary, your
  packager line, `update.rhai` tracking (`crates()` / `pypi()` preferred)
- unknown facts become `TODO` + a review checklist for you

The output is only a template. You must fill every TODO and follow the
[Terra packaging policies](https://docs.terrapkg.com/contributing/policies)
before build and PR.

For Python projects, the spec comes from the Fedora
[pyp2spec](https://pypi.org/project/pyp2spec/) tool by default when it is
installed (`dnf install pyp2spec`). It is only used when the PyPI project
links back to the same upstream repo, so a same-named different project
never gets used. Otherwise — or with `--no-pyp2spec` — the built-in
templates are used.

## Build

```sh
anda build -c terra-rawhide-x86_64 anda/<category>/<name>/pkg
```

New scratch dirs need a root `anda.hcl` — pass `--init-root` to create it.

## More languages

Language support lives in `terra_hatch/languages/`, one file per
language. To add a language:

1. create `languages/<name>.py` with a `ROW`, a `sniff()` and a template
2. register it in `languages/__init__.py` (the `LANGUAGES` / `SNIFFERS` tables)
3. drop a `.spec.j2` template into `templates/`
