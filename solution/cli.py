"""Command Line Interface (CLI) for the pipeline using Typer."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from solution.config import (
    DEFAULT_DISPOSITIONS_PATH,
    DEFAULT_SCHEMAS_DIR,
)
from solution.database import get_session, init_db
from solution.ingestion.events import ingest_events_jsonl
from solution.ingestion.leads import ingest_lead_csv
from solution.reporting.reports import generate_all_reports
from solution.schemas import DispositionCatalogue, load_workflow_schemas
from solution.services.data_quality import compute_data_quality_metrics
from solution.services.ledger import resolve_pending_events
from solution.utils.files import discover_drop_files

app = typer.Typer(
    help="Agentic Universe: Interaction Ledger & Reporting Pipeline CLI.",
    no_args_is_help=True,
)
console = Console()


@app.command("init-db")
def init_db_cmd(
    db_url: Optional[str] = typer.Option(
        None, "--db", help="SQLAlchemy database URL. Defaults to data/pipeline.db."
    ),
) -> None:
    """Initialize database tables."""
    init_db(db_url)
    console.print("[green]Database initialized successfully.[/green]")


@app.command("ingest")
def ingest_cmd(
    path: Path = typer.Argument(
        ..., help="Path to drop directory (e.g. data/drop_1) or single file."
    ),
    schemas_dir: Path = typer.Option(
        DEFAULT_SCHEMAS_DIR, "--schemas", help="Directory containing workflow JSON schemas."
    ),
    dispositions_path: Path = typer.Option(
        DEFAULT_DISPOSITIONS_PATH, "--dispositions", help="Path to dispositions.csv."
    ),
    db_url: Optional[str] = typer.Option(None, "--db", help="SQLAlchemy database URL."),
) -> None:
    """Ingest a drop directory containing leads and events."""
    if not path.exists():
        console.print(f"[red]Error: Path '{path}' does not exist.[/red]")
        raise typer.Exit(code=1)

    init_db(db_url)
    schemas = load_workflow_schemas(schemas_dir)
    catalogue = DispositionCatalogue.from_csv(dispositions_path)

    with get_session(db_url) as session:
        if path.is_dir():
            lead_files, event_files = discover_drop_files(path)
            console.print(
                f"[bold cyan]Ingesting drop '{path.name}': "
                f"{len(lead_files)} lead files, {len(event_files)} event files.[/bold cyan]"
            )

            # Ingest leads
            for wf_id, batch_id, csv_path in lead_files:
                if wf_id not in schemas:
                    console.print(
                        f"[yellow]Warning: No schema found for workflow '{wf_id}'. Skipping {csv_path}.[/yellow]"
                    )
                    continue
                schema = schemas[wf_id]
                res = ingest_lead_csv(session, csv_path, wf_id, batch_id, schema)
                if res.skipped_as_duplicate:
                    console.print(f"  [dim]Leads {csv_path.name}: already ingested (skipped)[/dim]")
                else:
                    console.print(
                        f"  [green]Leads {csv_path.name}:[/green] "
                        f"{res.lead_rows_total} rows, "
                        f"{res.lead_rows_rejected} rejected, "
                        f"{res.duplicate_lead_rows_merged} merged, "
                        f"{res.field_warnings} warnings"
                    )

            # Resolve any pending events before ingesting new events
            resolve_pending_events(session, catalogue)

            # Ingest events
            for jsonl_path in event_files:
                ev_res = ingest_events_jsonl(session, jsonl_path, catalogue)
                if ev_res.skipped_as_duplicate:
                    console.print(
                        f"  [dim]Events {jsonl_path.name}: already ingested (skipped)[/dim]"
                    )
                else:
                    console.print(
                        f"  [green]Events {jsonl_path.name}:[/green] "
                        f"{ev_res.events_total} lines, "
                        f"{ev_res.malformed_events} malformed, "
                        f"{ev_res.duplicate_events_dropped} duplicates dropped"
                    )

            # Final resolution pass
            resolved = resolve_pending_events(session, catalogue)
            if resolved > 0:
                console.print(f"  [cyan]Resolved {resolved} pending orphan events.[/cyan]")

        elif path.is_file():
            # Ingest single file
            if path.suffix == ".csv":
                console.print(f"[bold cyan]Ingesting single lead CSV: {path.name}[/bold cyan]")
                # Attempt to determine workflow_id from parent or schema
                wf_id = path.parent.name
                if wf_id not in schemas:
                    wf_id = next(iter(schemas.keys()))
                schema = schemas[wf_id]
                res = ingest_lead_csv(session, path, wf_id, path.stem, schema)
                console.print(f"[green]Completed: {res.lead_rows_total} rows.[/green]")
            elif path.suffix in (".jsonl", ".json"):
                console.print(f"[bold cyan]Ingesting single event JSONL: {path.name}[/bold cyan]")
                ev_res = ingest_events_jsonl(session, path, catalogue)
                console.print(f"[green]Completed: {ev_res.events_total} lines.[/green]")
            resolve_pending_events(session, catalogue)

    console.print("[bold green]Ingestion completed successfully.[/bold green]")


@app.command("report")
def report_cmd(
    out: Path = typer.Option(
        Path("reports"), "--out", "-o", help="Directory where reports will be written."
    ),
    dispositions_path: Path = typer.Option(
        DEFAULT_DISPOSITIONS_PATH, "--dispositions", help="Path to dispositions.csv."
    ),
    db_url: Optional[str] = typer.Option(None, "--db", help="SQLAlchemy database URL."),
) -> None:
    """Generate all five client reports matching the exact output contracts."""
    init_db(db_url)
    catalogue = DispositionCatalogue.from_csv(dispositions_path)

    with get_session(db_url) as session:
        generate_all_reports(session, catalogue, out)

    console.print(f"[bold green]All 5 reports generated successfully in '{out}'.[/bold green]")
    for fname in [
        "batch_summary.csv",
        "daily_overview.csv",
        "lead_report.csv",
        "rejections.csv",
        "data_quality.json",
    ]:
        fpath = out / fname
        size = fpath.stat().st_size if fpath.exists() else 0
        console.print(f"  [cyan]• {fname}[/cyan] ({size} bytes)")


@app.command("stats")
def stats_cmd(
    db_url: Optional[str] = typer.Option(None, "--db", help="SQLAlchemy database URL."),
) -> None:
    """Show current pipeline ingestion and data quality statistics."""
    init_db(db_url)
    with get_session(db_url) as session:
        metrics = compute_data_quality_metrics(session)

    table = Table(title="Pipeline Ingestion & Data Quality Metrics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="magenta")

    for k, v in metrics.items():
        if isinstance(v, dict):
            formatted_dict = ", ".join(f"{rk}: {rv}" for rk, rv in v.items()) or "None"
            table.add_row(k, formatted_dict, style="magenta")
        else:
            table.add_row(k, str(v), style="magenta")

    console.print(table, markup=False)


if __name__ == "__main__":
    app()
