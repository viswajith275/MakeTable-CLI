from __future__ import annotations

import json
import math
import sys
from enum import Enum
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from collections import defaultdict

from .models import GeneratedResponse, TimeTableEntryOutput, TimeTableGenerationInput, WeekDay

app = typer.Typer(
    name="timetable",
    help="Generate and inspect school timetables using CP-SAT.",
    add_completion=False,
    no_args_is_help=False,
)
console = Console()
err_console = Console(stderr=True)


class OutputFormat(str, Enum):
    json = "json"
    table = "table"
    csv = "csv"


def _load_input(input_file: Path) -> TimeTableGenerationInput:
    input_file = input_file.expanduser()
    if not input_file.exists():
        err_console.print(f"[red]Input file not found:[/red] {input_file}")
        raise typer.Exit(code=1)

    try:
        raw_text = input_file.read_text(encoding="utf-8")
        return TimeTableGenerationInput.model_validate(json.loads(raw_text))

    except ValidationError as e:
        err_console.print("[red]Input validation failed:[/red]")
        for err in e.errors():
            loc = " -> ".join(str(p) for p in err["loc"])
            err_console.print(f"  [yellow]{loc}[/yellow]: {err['msg']}")
        raise typer.Exit(code=1)

    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        err_console.print(f"[red]Invalid JSON:[/red] {e}")
        raise typer.Exit(code=1)

    except OSError as e:
        err_console.print(f"[red]Could not read input:[/red] {e}")
        raise typer.Exit(code=1)


def _load_hint_input(input_file: Path | None) -> GeneratedResponse | None:
    if not input_file:
        return None

    input_file = input_file.expanduser()
    if not input_file.exists():
        err_console.print(f"[red]Input file not found:[/red] {input_file}")
        raise typer.Exit(code=1)

    try:
        raw_text = input_file.read_text(encoding="utf-8")
        return GeneratedResponse.model_validate(json.loads(raw_text))

    except ValidationError as e:
        err_console.print("[red]Hint validation failed:[/red]")
        for err in e.errors():
            loc = " -> ".join(str(p) for p in err["loc"])
            err_console.print(f"  [yellow]{loc}[/yellow]: {err['msg']}")
        raise typer.Exit(code=1)

    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        err_console.print(f"[red]Invalid JSON:[/red] {e}")
        raise typer.Exit(code=1)

    except OSError as e:
        err_console.print(f"[red]Could not read hint:[/red] {e}")
        raise typer.Exit(code=1)


def _write_file(path: Path, text: str) -> None:
    """Write text to a file, reporting failures without a traceback."""
    path = path.expanduser()
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        err_console.print(f"[red]Could not write {path}:[/red] {e}")
        raise typer.Exit(code=1)


# Only used for testing and raw cli usage
def _print_entries_table(response: GeneratedResponse, target: Console = console):

    if not response.entries:
        target.print("[yellow]No entries to display.[/yellow]")
        return

    by_class: dict[str, list[TimeTableEntryOutput]] = defaultdict(list)
    for entry in response.entries:
        by_class[entry.class_name].append(entry)

    day_order = {
        "Mon": 0,
        "Tue": 1,
        "Wed": 2,
        "Thu": 3,
        "Fri": 4,
        "Sat": 5,
        "Sun": 6,
    }

    all_days = sorted({e.day for e in response.entries}, key=lambda d: day_order[d.value])
    max_slot = max(
        max((e.slot for e in response.entries), default=0),
        max(response.break_slots, default=0),
    )
    all_slots = list(range(1, max_slot + 1))
    break_slots_set = set(response.break_slots)

    for class_name in sorted(by_class.keys()):
        entries = by_class[class_name]

        cell_lookup: dict[WeekDay, dict[int, list[TimeTableEntryOutput]]] = {
            day: defaultdict(list) for day in all_days
        }
        for entry in entries:
            cell_lookup[entry.day][entry.slot].append(entry)

        table = Table(title=f"Timetable — {class_name}", show_lines=True)
        table.add_column("Day", style="bold cyan", justify="center")
        for slot in all_slots:
            label = f"Slot {slot} [dim](Break)[/dim]" if slot in break_slots_set else f"Slot {slot}"
            table.add_column(label, justify="center")

        for day in all_days:
            row = [day.value]
            for slot in all_slots:
                slot_entries = cell_lookup[day].get(slot, [])
                if not slot_entries:
                    if slot in break_slots_set:
                        row.append("[dim italic]BREAK[/dim italic]")
                    else:
                        row.append("[dim]—[/dim]")
                else:
                    parts = []
                    for entry in slot_entries:
                        tag_badge = f" [bold cyan][{entry.tag}][/bold cyan]" if entry.tag else ""
                        block = f" [dim]({entry.period_length}h block)[/dim]" if entry.period_length > 1 else ""
                        parts.append(
                            f"[bold]{entry.subject_name}[/bold]{tag_badge}{block}\n"
                            f"{entry.teacher_name}\n"
                            f"[dim]{entry.room_name}[/dim]"
                        )
                    row.append("\n---\n".join(parts))
            table.add_row(*row)

        target.print(table)
        target.print()


# Also this
def _print_violations(response: GeneratedResponse, target: Console = console):

    if not response.violations:
        target.print("[green]No violations — all constraints fully satisfied.[/green]")
        return

    table = Table(title=f"Violations ({len(response.violations)})")
    table.add_column("Message", style="yellow")
    table.add_column("Value", justify="right")

    for v in response.violations:
        table.add_row(v.error_msg, str(v.val))

    target.print(table)


def _resolve_format(
    fmt: OutputFormat | None, output_file: Path | None
) -> OutputFormat:
    """Pick a format from the output suffix when --format was not given."""
    if fmt is not None:
        return fmt
    if output_file is not None:
        suffix = output_file.suffix.lower()
        if suffix == ".json":
            return OutputFormat.json
        if suffix == ".csv":
            return OutputFormat.csv
    return OutputFormat.table


# Writes the res json to a result file
def _write_output(
    response: GeneratedResponse,
    output_file: Path | None,
    fmt: OutputFormat,
    show_violations: bool,
):
    if output_file is not None:
        output_file = output_file.expanduser()
    if fmt == OutputFormat.json:
        text = response.model_dump_json(indent=2)
        if output_file:
            _write_file(output_file, text)
            err_console.print(
                f"[green]Wrote {len(response.entries)} entries to[/green] {output_file}"
            )
            if show_violations:
                _print_violations(response)
        else:
            # Keep stdout machine-readable: violations are already in the JSON.
            typer.echo(text)
        return

    if fmt == OutputFormat.csv:
        csv_text = response.to_csv()
        if output_file:
            _write_file(output_file, csv_text)
            err_console.print(
                f"[green]Wrote {len(response.entries)} entries to[/green] {output_file}"
            )
            if show_violations:
                _print_violations(response)
        else:
            typer.echo(csv_text, nl=False)
        return

    if output_file:
        # Render the rich tables into the file instead of silently ignoring -o.
        renderer = Console(record=True, width=120)
        _print_entries_table(response, renderer)
        if show_violations:
            _print_violations(response, renderer)
        _write_file(output_file, renderer.export_text())
        err_console.print(
            f"[green]Wrote table for {len(response.entries)} entries to[/green] {output_file}"
        )
        return

    _print_entries_table(response)
    if show_violations:
        _print_violations(response)


def _launch_tui(file: Path | None) -> None:
    if not sys.stdin.isatty():
        err_console.print("[red]The TUI needs an interactive terminal.[/red]")
        raise typer.Exit(code=1)
    try:
        from .tui import run_tui
    except ImportError as e:
        err_console.print(f"[red]TUI is unavailable:[/red] {e}")
        err_console.print("Install it with: [bold]uv sync[/bold] (or pip install textual)")
        raise typer.Exit(code=1)
    run_tui(file)


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    """Open the interactive editor when no command is given."""
    if ctx.invoked_subcommand is not None:
        return
    if not sys.stdin.isatty():
        typer.echo(ctx.get_help())
        return
    _launch_tui(None)


@app.command()
def tui(
    file: Path = typer.Argument(
        None,
        help="JSON project to open (created on first save if it does not exist)",
    ),
):
    """Open the interactive editor for projects, teachers, subjects and more."""
    _launch_tui(file)


@app.command()
def validate(
    input_file: Path = typer.Argument(
        ..., help="Path to the input JSON file matching TimeTableGenerationInput"
    ),
):
    """Validate a project file, including all references between entities."""
    data = _load_input(input_file)
    break_info = f", {len(data.project.break_slots)} break slot(s)" if data.project.break_slots else ""
    console.print(
        f"[green]Input is valid.[/green] "
        f"{len(data.rooms)} rooms, {len(data.classes)} classes, "
        f"{len(data.teachers)} teachers, {len(data.subjects)} subjects, "
        f"{len(data.teacher_assignments)} assignments{break_info}."
    )


@app.command()
def generate(
    input_file: Path = typer.Argument(
        ..., help="Path to the input JSON file matching TimeTableGenerationInput"
    ),
    output_file: Path = typer.Option(
        None, "--output", "-o", help="Where to write results (omit to print to stdout)"
    ),
    hint_file: Path = typer.Option(
        None,
        "--previous-hint",
        "-hp",
        help="Hints for creating timetable from previous responses",
    ),
    fmt: OutputFormat | None = typer.Option(
        None,
        "--format",
        "-f",
        help="Output format (defaults to the --output file extension, else table)",
    ),
    time_limit: float = typer.Option(
        60.0,
        "--time-limit",
        "-t",
        help="Maximum solver time in seconds (raise it to search longer)",
    ),
    seed: int = typer.Option(
        42, "--seed", "-s", help="Random seed (reproducible with --workers 1)"
    ),
    workers: int = typer.Option(
        8, "--workers", "-w", help="Solver worker threads (1 for reproducibility)"
    ),
    fast: bool = typer.Option(
        False,
        "--fast",
        help="Quick result with a 5-second solver budget (lower quality)",
    ),
    gap_limit: float | None = typer.Option(
        None,
        "--gap-limit",
        help="Stop once within this relative gap of the best bound (0.02 = 2%)",
    ),
    patience: float = typer.Option(
        5.0,
        "--patience",
        help="Stop after this many seconds without a better timetable (0 disables)",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Require structural constraints to hold strictly without soft fallback",
    ),
    show_violations: bool = typer.Option(
        True,
        "--violations/--no-violations",
        help="Print violation report after generation",
    ),
):
    """Generate a timetable from a project file."""
    if not math.isfinite(time_limit) or time_limit <= 0:
        err_console.print("[red]Time limit must be a finite number greater than zero.[/red]")
        raise typer.Exit(code=1)
    if workers < 1:
        err_console.print("[red]Workers must be at least 1.[/red]")
        raise typer.Exit(code=1)
    if not math.isfinite(patience) or patience < 0:
        err_console.print("[red]Patience must be a finite number >= 0.[/red]")
        raise typer.Exit(code=1)
    if gap_limit is not None and (not math.isfinite(gap_limit) or gap_limit < 0):
        err_console.print("[red]Gap limit must be a finite number >= 0.[/red]")
        raise typer.Exit(code=1)

    data = _load_input(input_file)
    if not data.teacher_assignments:
        err_console.print(
            "[yellow]Input project has no teacher assignments. Nothing to schedule.[/yellow]"
        )
        raise typer.Exit(code=1)

    hint_timetable = _load_hint_input(hint_file)

    with err_console.status("[bold green]Solving..."):
        from .solver import TimeTableGenerator

        generator = TimeTableGenerator(RawData=data, HintData=hint_timetable)
        response: GeneratedResponse = generator.solve(
            time_limit_sec=time_limit,
            seed=seed,
            workers=workers,
            fast=fast,
            gap_limit=gap_limit,
            patience=patience,
            strict=strict,
        )

    if not response.success:
        err_console.print("[red]No feasible timetable found.[/red]")
        if response.violations:
            _print_violations(response, err_console)
        raise typer.Exit(code=1)

    _write_output(
        response, output_file, _resolve_format(fmt, output_file), show_violations
    )

    err_console.print(
        f"[bold green]Solved.[/bold green] {len(response.entries)} entries generated."
    )


@app.command()
def schema(
    output_file: Path = typer.Option(
        None, "--output", "-o", help="Write the JSON schema to this file instead of stdout"
    ),
):
    """Print the JSON schema for a project file."""
    schema_dict = TimeTableGenerationInput.model_json_schema()
    text = json.dumps(schema_dict, indent=2)
    if output_file:
        _write_file(output_file, text)
        err_console.print(f"[green]Wrote schema to[/green] {output_file.expanduser()}")
    else:
        typer.echo(text)


if __name__ == "__main__":
    app()
