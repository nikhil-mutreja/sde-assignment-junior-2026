"""Configuration and environment settings for the pipeline."""

from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

# Base directories
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMAS_DIR = PROJECT_ROOT / "assignment-a-data" / "schemas"
DEFAULT_DISPOSITIONS_PATH = DEFAULT_SCHEMAS_DIR / "dispositions.csv"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "pipeline.db"

# Timezones
# IST is UTC+05:30. Naive timestamps are strictly interpreted as IST per specification.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")
UTC = timezone.utc

# Security Keys
# 32-byte keys represented in hex (64 hex characters)
# Safe development keys for local development and testing; production overrides via environment
DEV_PII_KEY_HEX = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
DEV_HMAC_KEY_HEX = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"

PII_ENCRYPTION_KEY_HEX = os.getenv("PII_ENCRYPTION_KEY", DEV_PII_KEY_HEX)
PHONE_HMAC_KEY_HEX = os.getenv("PHONE_HMAC_KEY", DEV_HMAC_KEY_HEX)
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")
