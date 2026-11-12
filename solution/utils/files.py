"""File utilities for hashing, header normalisation, and drop discovery."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import List, Tuple


def compute_file_sha256(file_path: Path) -> str:
    """Compute the SHA-256 hash of a file for idempotency tracking."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def normalise_header(header: str) -> str:
    """Normalise a column header.

    Rules:
    - Lowercase.
    - Trim whitespace.
    - Replace every run of '_' or whitespace with a single space.
    """
    if not header:
        return ""
    return re.sub(r"[\s_]+", " ", header.lower().strip())


def discover_drop_files(drop_dir: Path) -> Tuple[List[Tuple[str, str, Path]], List[Path]]:
    """Discover lead and event files within a drop directory.

    Structure expected:
    <drop_dir>/
        leads/<workflow_id>/<batch_id>.csv
        events/<events_file>.jsonl

    Returns:
    - List of (workflow_id, batch_id, file_path) sorted by (workflow_id, batch_id)
    - List of event file Paths sorted by filename
    """
    lead_files: List[Tuple[str, str, Path]] = []
    event_files: List[Path] = []

    leads_root = drop_dir / "leads"
    if leads_root.is_dir():
        for wf_dir in sorted(leads_root.iterdir()):
            if wf_dir.is_dir():
                wf_id = wf_dir.name
                for csv_path in sorted(wf_dir.glob("*.csv")):
                    batch_id = csv_path.stem
                    lead_files.append((wf_id, batch_id, csv_path))

    events_root = drop_dir / "events"
    if events_root.is_dir():
        for jsonl_path in sorted(events_root.glob("*.jsonl")):
            event_files.append(jsonl_path)

    return lead_files, event_files
