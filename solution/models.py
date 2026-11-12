"""SQLAlchemy models for leads, interaction ledger events, rejections, and ingestion tracking."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class IngestedFile(Base):
    """Tracks files processed by the pipeline for idempotency and quality counters."""

    __tablename__ = "ingested_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_path: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'leads' or 'events'
    workflow_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    batch_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Ingestion metrics for this file
    lead_rows_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lead_rows_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_lead_rows_merged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    field_warnings: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    events_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    malformed_events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_events_dropped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Lead(Base):
    """Normalized and encrypted customer leads."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    batch_id: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # First batch permanently
    external_ref: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    phone_last4: Mapped[str] = mapped_column(String(4), nullable=False)

    # PII fields encrypted at rest via AES-256-GCM
    encrypted_customer_name: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_canonical_phone: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_pii_fields: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Non-PII workflow attributes (JSON string: date, decimal, enum, product, etc.)
    workflow_attributes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Unique identity is (workflow_id, canonical phone hash)
    __table_args__ = (
        UniqueConstraint("workflow_id", "phone_hash", name="uq_leads_workflow_phone"),
        Index("ix_leads_workflow_ref", "workflow_id", "external_ref"),
        Index("ix_leads_workflow_batch", "workflow_id", "batch_id"),
    )

    events: Mapped[list["InteractionEvent"]] = relationship(
        "InteractionEvent", back_populates="lead"
    )


class RejectedLead(Base):
    """Log of rejected lead rows with exact validation failure reason."""

    __tablename__ = "rejected_leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(String(50), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(50), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)

    __table_args__ = (
        UniqueConstraint("workflow_id", "batch_id", "row_number", name="uq_rejected_lead_row"),
        Index("ix_rejections_sort", "workflow_id", "batch_id", "row_number"),
    )


class InteractionEvent(Base):
    """Append-only interaction ledger for all telephony and disposition events."""

    __tablename__ = "interaction_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    workflow_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    call_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    phone_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    # Foreign key to lead when resolved
    lead_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("leads.id"), nullable=True, index=True
    )
    lead: Mapped[Optional["Lead"]] = relationship("Lead", back_populates="events")

    # Stored in UTC
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # Encrypted phone if present in event payload
    encrypted_phone: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Non-PII payload (duration, reason, disposition_code, etc.)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # Status flags
    is_linked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    is_unknown_disposition: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    ingested_file_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ingested_files.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_events_call_occurred", "call_id", "occurred_at"),
        Index("ix_events_lead_occurred", "lead_id", "occurred_at"),
        Index("ix_events_unlinked", "is_linked", "workflow_id"),
    )
