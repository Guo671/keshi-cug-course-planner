"""Stable catalog identity used by drafts and immutable planning history."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..infrastructure.tables import CatalogSnapshot


def catalog_fingerprint(db: Session) -> str:
    """Hash the ordered snapshot evidence that can change planning results."""

    snapshots = list(db.scalars(select(CatalogSnapshot).order_by(CatalogSnapshot.id)))
    evidence = [
        {
            "id": snapshot.id,
            "sha256": snapshot.source_sha256,
            "rank": snapshot.source_rank,
            "primary": snapshot.is_primary,
        }
        for snapshot in snapshots
    ]
    return hashlib.sha256(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
