"""Message helpers shared by all terra-hatch modules."""

import sys

from . import NAME


def log(message):
    print(f"[{NAME}] {message}")


def warn(message):
    print(f"[{NAME}] WARNING: {message}", file=sys.stderr)


def die(message):
    sys.exit(f"[{NAME}] ERROR: {message}")
