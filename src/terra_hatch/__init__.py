"""terra-hatch: hatch a new Terra package scaffold from upstream sources.

    <root>/<category>/<pkg-name>/
        <pkg-name>.spec
        anda.hcl
        update.rhai         (unless --no-update)

The generated files are only starting templates based on facts found in the
upstream sources; every TODO left in them MUST be resolved and reviewed by a
human before building or submitting anything (see the Terra packaging
policies). This tool does not produce finished package submissions.
"""
