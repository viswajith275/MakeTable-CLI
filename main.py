# cli.py
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from pydantic import ValidationError

from models import TimeTableGenerationInput, GeneratedResponse
from solver import TimeTableGenerator

app = typer.Typer(
    name="timetable",
    help="Generate and inspect school timetables using CP-SAT.",
    add_completion=False,
)
console = Console()


class OutputFormat(str, Enum):
    json = "json"
    table = "table"
    csv = "csv"


def _load_input(input_file: Path) -> TimeTableGenerationInput:
    """Load and validate the raw input file against the Pydantic schema."""
    if not input_file.exists():
        console.print(f"[red]Input file not found:[/red] {input_file}")
        raise typer.Exit(code=1)

    try:
        raw_text = input_file.read_text()
        return TimeTableGenerationInput.model_validate_json(raw_text)
    
    except ValidationError as e:

        console.print("[red]Input validation failed:[/red]")
        for err in e.errors():
            loc = " -> ".join(str(p) for p in err["loc"])
            console.print(f"  [yellow]{loc}[/yellow]: {err['msg']}")
        raise typer.Exit(code=1)
    
    except json.JSONDecodeError as e:
        console.print(f"[red]Invalid JSON:[/red] {e}")
        raise typer.Exit(code=1)


def _print_entries_table(response: GeneratedResponse):

    table = Table(title="Generated Timetable")
    for col in ["Day", "Slot", "Class", "Subject", "Teacher", "Room"]:
        table.add_column(col)

    for e in sorted(response.entries, key=lambda x: (x.day.value, x.slot)):
        table.add_row(
            e.day.value,
            str(e.slot),
            e.class_name,
            e.subject_name,
            e.teacher_name,
            e.room_name,
        )
    console.print(table)


def _print_violations(response: GeneratedResponse):

    if not response.violations:
        console.print("[green]No violations — all constraints fully satisfied.[/green]")
        return

    table = Table(title=f"Violations ({len(response.violations)})")
    table.add_column("Message", style="yellow")
    table.add_column("Value", justify="right")

    for v in response.violations:
        table.add_row(v.error_msg, str(v.val))

    console.print(table)


def _write_output(response: GeneratedResponse, output_file: Path | None, fmt: OutputFormat):
    if fmt == OutputFormat.json:
        text = response.model_dump_json(indent=2)

    elif fmt == OutputFormat.csv:
        import csv
        import io
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["assignment_id", "day", "slot", "class_name", "subject_name", "teacher_name", "room_name"])
        for e in response.entries:
            writer.writerow([str(e.assignment_id), e.day.value, e.slot, e.class_name, e.subject_name, e.teacher_name, e.room_name])
        text = buf.getvalue()

    else:  # table -> print directly, nothing to write
        _print_entries_table(response)
        _print_violations(response)
        return

    if output_file:
        output_file.write_text(text)
        console.print(f"[green]Wrote {len(response.entries)} entries to[/green] {output_file}")
        
    else:
        console.print(text)


@app.command()
def validate(
    input_file: Path = typer.Argument(..., help="Path to the input JSON file matching TimeTableGenerationInput"),
):
    """Validate an input file against the Pydantic schema without generating anything."""
    _load_input(input_file)
    console.print("[green]Input is valid.[/green]")


@app.command()
def generate(
    input_file: Path = typer.Argument(..., help="Path to the input JSON file matching TimeTableGenerationInput"),
    output_file: Path = typer.Option(None, "--output", "-o", help="Where to write results (omit to print to stdout)"),
    fmt: OutputFormat = typer.Option(OutputFormat.table, "--format", "-f", help="Output format"),
    time_limit: float = typer.Option(60.0, "--time-limit", "-t", help="Solver time budget in seconds"),
    seed: int = typer.Option(42, "--seed", "-s", help="Random seed for reproducibility"),
    show_violations: bool = typer.Option(True, "--violations/--no-violations", help="Print violation report after generation"),
):
    """Generate a timetable from the given input file."""
    data = _load_input(input_file)

    with console.status("[bold green]Solving..."):
        generator = TimeTableGenerator(data)
        response: GeneratedResponse = generator.solve(
            time_limit_sec=time_limit,
            seed=seed,
        )

    if not response.success:
        console.print("[red]No feasible timetable found.[/red]")
        if response.violations:
            _print_violations(response)
        raise typer.Exit(code=1)

    console.print(f"[bold green]Solved.[/bold green] {len(response.entries)} entries generated.")

    _write_output(response, output_file, fmt)

    if show_violations and fmt != OutputFormat.table:
        # table format already prints violations inline above
        _print_violations(response)


@app.command()
def schema(
    output_file: Path = typer.Option(None, "--output", "-o", help="Write the JSON schema to this file instead of stdout"),
):
    """Print the JSON schema for the expected input file, for reference."""
    schema_dict = TimeTableGenerationInput.model_json_schema()
    text = json.dumps(schema_dict, indent=2)
    if output_file:
        output_file.write_text(text)
        console.print(f"[green]Wrote schema to[/green] {output_file}")
    else:
        console.print(text)


if __name__ == "__main__":
    app()