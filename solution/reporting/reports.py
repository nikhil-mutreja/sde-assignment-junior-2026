"""Report generation adhering strictly to the assignment's five output contracts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from solution.models import RejectedLead
from solution.reporting.aggregator import (
    CallFact,
    LeadFact,
    build_call_facts,
    build_lead_facts,
)
from solution.schemas import DispositionCatalogue
from solution.services.data_quality import compute_data_quality_metrics


def generate_all_reports(
    session: Session,
    catalogue: DispositionCatalogue,
    output_dir: Path,
) -> None:
    """Generate all five client and operational reports into output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)

    call_facts = build_call_facts(session, catalogue)
    lead_facts = build_lead_facts(session, call_facts)

    write_batch_summary(lead_facts, output_dir / "batch_summary.csv")
    write_daily_overview(call_facts, output_dir / "daily_overview.csv")
    write_lead_report(lead_facts, output_dir / "lead_report.csv")
    write_rejections(session, output_dir / "rejections.csv")
    write_data_quality(session, output_dir / "data_quality.json")


def write_batch_summary(lead_facts: List[LeadFact], out_path: Path) -> None:
    """Generate Report 1: batch_summary.csv sorted by workflow_id, batch_id."""
    # Group leads by (workflow_id, batch_id)
    batches: Dict[Tuple[str, str], Dict[str, int]] = {}

    for lf in lead_facts:
        key = (lf.workflow_id, lf.batch_id)
        if key not in batches:
            batches[key] = {
                "total_leads": 0,
                "yet_to_reach_out": 0,
                "in_progress_telephony_failed": 0,
                "in_progress_dialed": 0,
                "completed": 0,
            }
        b_data = batches[key]
        b_data["total_leads"] += 1
        b_data[lf.bucket] += 1

    header = [
        "workflow_id",
        "batch_id",
        "total_leads",
        "yet_to_reach_out",
        "in_progress_telephony_failed",
        "in_progress_dialed",
        "completed",
    ]

    sorted_keys = sorted(batches.keys())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for wf_id, b_id in sorted_keys:
            d = batches[(wf_id, b_id)]
            writer.writerow(
                [
                    wf_id,
                    b_id,
                    d["total_leads"],
                    d["yet_to_reach_out"],
                    d["in_progress_telephony_failed"],
                    d["in_progress_dialed"],
                    d["completed"],
                ]
            )


def write_daily_overview(call_facts: Dict[str, CallFact], out_path: Path) -> None:
    """Generate Report 2: daily_overview.csv sorted by workflow_id, date."""
    # Group calls by (workflow_id, ist_date)
    daily_groups: Dict[Tuple[str, str], List[CallFact]] = {}

    for c in call_facts.values():
        key = (c.workflow_id, c.ist_date)
        daily_groups.setdefault(key, []).append(c)

    header = [
        "workflow_id",
        "date",
        "total_calls",
        "connected_calls",
        "connectivity_rate",
        "success_leads",
    ]

    sorted_keys = sorted(daily_groups.keys())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for wf_id, ist_date in sorted_keys:
            calls = daily_groups[(wf_id, ist_date)]
            total_calls = len(calls)
            connected_calls = sum(1 for c in calls if c.is_connected)
            rate = connected_calls / total_calls if total_calls > 0 else 0.0
            rate_str = f"{rate:.4f}"

            # Distinct leads with at least one successful call on that date
            success_leads_set: Set[int] = {c.lead_id for c in calls if c.is_success}
            success_leads = len(success_leads_set)

            writer.writerow(
                [
                    wf_id,
                    ist_date,
                    total_calls,
                    connected_calls,
                    rate_str,
                    success_leads,
                ]
            )


def write_lead_report(lead_facts: List[LeadFact], out_path: Path) -> None:
    """Generate Report 3: lead_report.csv sorted by workflow_id, external_ref."""
    header = [
        "workflow_id",
        "external_ref",
        "batch_id",
        "phone_masked",
        "bucket",
        "total_calls",
        "connected_calls",
        "last_call_at",
        "current_disposition",
        "ever_success",
    ]

    # Sort by workflow_id, external_ref
    sorted_leads = sorted(lead_facts, key=lambda x: (x.workflow_id, x.external_ref))

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for lf in sorted_leads:
            writer.writerow(
                [
                    lf.workflow_id,
                    lf.external_ref,
                    lf.batch_id,
                    lf.phone_masked,
                    lf.bucket,
                    lf.total_calls,
                    lf.connected_calls,
                    lf.last_call_at,
                    lf.current_disposition,
                    "true" if lf.ever_success else "false",
                ]
            )


def write_rejections(session: Session, out_path: Path) -> None:
    """Generate Report 4: rejections.csv sorted by workflow_id, batch_id, row_number."""
    header = ["workflow_id", "batch_id", "row_number", "reason"]

    stmt = select(RejectedLead).order_by(
        RejectedLead.workflow_id,
        RejectedLead.batch_id,
        RejectedLead.row_number,
    )
    rejections = session.scalars(stmt).all()

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for r in rejections:
            writer.writerow([r.workflow_id, r.batch_id, r.row_number, r.reason])


def write_data_quality(session: Session, out_path: Path) -> None:
    """Generate Report 5: data_quality.json."""
    metrics = compute_data_quality_metrics(session)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
