from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from pydantic import ValidationError
from collections import defaultdict

from src.models import TimeTableGenerationInput, GeneratedResponse, TimeTableEntryOutput

app = typer.Typer(
    name="timetable",
    help="Generate and inspect school timetables using CP-SAT.",
    add_completion=False,
)
console = Console()


class OutputFormat(str, Enum):
    json = "json"
    table = "table"


def _load_input(input_file: Path) -> TimeTableGenerationInput:
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


# Only used for testing and raw cli usage
def _print_entries_table(response: GeneratedResponse):

    if not response.entries:
        console.print("[yellow]No entries to display.[/yellow]")
        return

    by_class: dict[str, list[TimeTableEntryOutput]] = defaultdict(list)
    for entry in response.entries:
        by_class[entry.class_name].append(entry)

    day_order = {'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4, 'Sat': 5, 'Sun': 6}

    all_days = sorted({e.day for e in response.entries}, key=lambda d: day_order[d.value])
    all_slots = sorted({e.slot for e in response.entries})

    for class_name in sorted(by_class.keys()):
        entries = by_class[class_name]

        cell_lookup: dict = {day: {} for day in all_days}
        for entry in entries:
            cell_lookup[entry.day][entry.slot] = entry

        table = Table(title=f"Timetable — {class_name}", show_lines=True)
        table.add_column("Day", style="bold cyan", justify="center")
        for slot in all_slots:
            table.add_column(f"Slot {slot}", justify="center")

        for day in all_days:
            row = [day.value]
            for slot in all_slots:
                entry = cell_lookup[day].get(slot)
                if entry is None:
                    row.append("[dim]—[/dim]")
                else:
                    cell_text = (
                        f"[bold]{entry.subject_name}[/bold]\n"
                        f"{entry.teacher_name}\n"
                        f"[dim]{entry.room_name}[/dim]"
                    )
                    row.append(cell_text)
            table.add_row(*row)

        console.print(table)
        console.print()

# Also this
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

# Writes the res json to a result file
def _write_output(response: GeneratedResponse, output_file: Path | None, fmt: OutputFormat):
    if fmt == OutputFormat.json:
        text = response.model_dump_json(indent=2)

    else:
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
    data = _load_input(input_file)

    with console.status("[bold green]Solving..."):
        from src.solver import TimeTableGenerator
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
        _print_violations(response)

# For knowing the input schema json
@app.command()
def schema(
    output_file: Path = typer.Option(None, "--output", "-o", help="Write the JSON schema to this file instead of stdout"),
):
    schema_dict = TimeTableGenerationInput.model_json_schema()
    text = json.dumps(schema_dict, indent=2)
    if output_file:
        output_file.write_text(text)
        console.print(f"[green]Wrote schema to[/green] {output_file}")
    else:
        console.print(text)


if __name__ == "__main__":
    app()