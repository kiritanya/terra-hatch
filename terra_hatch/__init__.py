"""terra-hatch: write a starter Terra package from upstream sources.

    <root>/<category>/<pkg-name>/
        <pkg-name>.spec
        anda.hcl
        update.rhai         (unless --no-update)

The generated files are only starting templates based on facts found in the
upstream sources; every TODO left in them MUST be read and fixed by a human
before building or submitting anything (see the Terra packaging policies).
"""

NAME = "terra-hatch"
HTTP_UA = "terrapkg-terra-hatch"
__version__ = "0.1.0"
