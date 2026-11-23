"""Integrity-pinned bundled catalog loader."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from .models import Catalog

CATALOG_PATH = Path(__file__).with_name("catalog.json")
# Trust anchor embedded in the application source; update with an intentional catalog review.
BUILTIN_CATALOG_SHA256 = "e372ab3c73ac0c00b6ac1938669d13b256ac127c0cd4ab41cf8e869a6cc84b0e"


class CatalogIntegrityError(ValueError):
    """The bundled catalog changed or failed typed validation."""


def load_catalog(path: Path = CATALOG_PATH, expected_sha256: str = BUILTIN_CATALOG_SHA256) -> Catalog:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CatalogIntegrityError("The bundled component catalog could not be read.") from error

    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise CatalogIntegrityError("The bundled component catalog failed its integrity check.")
    try:
        return Catalog.model_validate_json(raw)
    except ValidationError as error:
        raise CatalogIntegrityError("The bundled component catalog failed manifest validation.") from error
