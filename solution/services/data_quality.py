"""Data quality service to compute and format data_quality.json."""

from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from solution.models import IngestedFile, InteractionEvent, RejectedLead


def compute_data_quality_metrics(session: Session) -> Dict[str, Any]:
    """Calculate the exact 10 data quality metrics matching the output contract.

    Structure:
    {
      "lead_rows_total": int,
      "lead_rows_rejected": int,
      "rejections_by_reason": {reason: count, ...},
      "duplicate_lead_rows_merged": int,
      "field_warnings": int,
      "events_total": int,
      "malformed_events": int,
      "duplicate_events_dropped": int,
      "orphan_events": int,
      "unknown_disposition_events": int
    }
    """
    # Sum file-level metrics across all unique ingested lead files
    lead_file_sums = session.execute(
        select(
            func.coalesce(func.sum(IngestedFile.lead_rows_total), 0),
            func.coalesce(func.sum(IngestedFile.duplicate_lead_rows_merged), 0),
            func.coalesce(func.sum(IngestedFile.field_warnings), 0),
        ).where(IngestedFile.file_type == "leads")
    ).one()

    lead_rows_total = int(lead_file_sums[0])
    duplicate_lead_rows_merged = int(lead_file_sums[1])
    field_warnings = int(lead_file_sums[2])

    # Rejection counts from rejected_leads table
    rejection_rows = session.execute(
        select(RejectedLead.reason, func.count(RejectedLead.id))
        .group_by(RejectedLead.reason)
        .order_by(RejectedLead.reason)
    ).all()

    rejections_by_reason = {row[0]: int(row[1]) for row in rejection_rows}
    lead_rows_rejected = sum(rejections_by_reason.values())

    # Sum file-level metrics across all unique ingested event files
    event_file_sums = session.execute(
        select(
            func.coalesce(func.sum(IngestedFile.events_total), 0),
            func.coalesce(func.sum(IngestedFile.malformed_events), 0),
            func.coalesce(func.sum(IngestedFile.duplicate_events_dropped), 0),
        ).where(IngestedFile.file_type == "events")
    ).one()

    events_total = int(event_file_sums[0])
    malformed_events = int(event_file_sums[1])
    duplicate_events_dropped = int(event_file_sums[2])

    # Final orphan count: events in ledger currently unable to link to a valid lead
    orphan_events = (
        session.scalar(
            select(func.count(InteractionEvent.id)).where(InteractionEvent.is_linked.is_(False))
        )
        or 0
    )

    # Unknown disposition count on linked events
    unknown_disposition_events = (
        session.scalar(
            select(func.count(InteractionEvent.id)).where(
                InteractionEvent.is_linked.is_(True),
                InteractionEvent.is_unknown_disposition.is_(True),
            )
        )
        or 0
    )

    return {
        "lead_rows_total": lead_rows_total,
        "lead_rows_rejected": lead_rows_rejected,
        "rejections_by_reason": rejections_by_reason,
        "duplicate_lead_rows_merged": duplicate_lead_rows_merged,
        "field_warnings": field_warnings,
        "events_total": events_total,
        "malformed_events": malformed_events,
        "duplicate_events_dropped": duplicate_events_dropped,
        "orphan_events": int(orphan_events),
        "unknown_disposition_events": int(unknown_disposition_events),
    }
