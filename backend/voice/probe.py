"""Check staged Kokoro files in a short-lived process."""

import sys
from pathlib import Path

from .service import _engine, _render


if __name__ == "__main__":
    _render(_engine(Path(sys.argv[1])), "The local voice engine is ready.", 1, 1.0, sys.argv[2])
