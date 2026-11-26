# MakeTable

Generate optimized school timetables with Google OR-Tools CP-SAT. It ships as
a CLI, an interactive terminal editor (TUI), and a small Python library you can
embed in another application.

## Features

- **Constraint-based scheduling** with CP-SAT
- **Interactive TUI** for projects, rooms, classes, teachers, subjects and
  teacher assignments, with weekly grid views (by class, teacher, and room)
- **JSON in / JSON, CSV and tables out** for integration with other apps
- **Rich CLI output** with per-class timetable tables, CSV streaming, and a violation report
- **Validation guard rails** for references, duplicate names/ids (case-insensitive), impossible
  min/max ranges, over-allocation of weekly slots, and inconsistent project days
- **Warm starts** from a previous result to speed up re-solving

Supported constraints:

- Teacher daily/weekly/consecutive limits, workload balancing, gap minimization
- Subject daily/weekly/consecutive minimums and maximums, morning preference,
  slot variety
- Room capacity and simultaneous-use limits
- Class conflicts and teacher conflicts
- Per-assignment first-slot requirements

## Installation

### Prebuilt binary (Linux & macOS)

Supported platforms: Linux (`x86_64`, glibc) and macOS (`arm64` / Apple Silicon).

```bash
curl -fsSL https://raw.githubusercontent.com/viswajith275/MakeTable-CLI/main/scripts/install.sh | bash
```

Installer options (pass after `bash -s --`):

- `-d, --dir <path>` — install directory (default: `~/.local/bin`)
- `-v, --version <tag>` — release tag to install (default: `latest`)
- `-f, --file <path>` — install from a local `.tar.gz` archive or binary
- `--no-verify` — skip SHA-256 checksum verification
- `--dry-run` — preview actions without modifying files

To uninstall:

```bash
curl -fsSL https://raw.githubusercontent.com/viswajith275/MakeTable-CLI/main/scripts/uninstall.sh | bash
```

Pass `--purge-rc` (via `bash -s -- --purge-rc`) to also remove installer PATH lines and custom `timetable` shell functions from your RC files.

### From source

Requires Python 3.13+.

```bash
git clone https://github.com/viswajith275/MakeTable-CLI.git
cd MakeTable-CLI
uv sync
```

Or with pip:

```bash
pip install -e .
```

Either way you get the `timetable` command.

## TUI

Open the editor:

```bash
timetable tui school.json
```

If `school.json` does not exist yet, the TUI starts a new project and creates
the file when you save. Running `timetable` with no arguments also opens the
TUI.

Tabs cover the whole project:

- **Project** — name, slots per day, active days, validation, open/save
- **Rooms** — name, lab flag, simultaneous capacity
- **Classes** — name and home room
- **Teachers** — daily/weekly/consecutive limits
- **Subjects** — morning tendency and min/max limits
- **Assignments** — class + teacher + subject, optional batch/track tag (e.g. "Batch 1", "Batch 2"), target room, period length (atomic blocks), co-scheduling (merged classes or split lab/elective batches), and first-slot days
- **College Department Features** — universal break slots, teacher unavailable/avoid slots, teacher max working days per week, atomic multi-period blocks, merged lectures, and parallel split sessions (concurrent labs/electives)
- **Timetable** — generate, filter, and inspect results and violations

Quality-of-life behaviour:

- Search & filter inputs on every entity tab for fast lookup
- Pre-submit form validation guard rails: prevents modal dismissal when constraints are invalid so typed data is never lost
- Foreign keys are dropdowns, so assignments cannot point at missing entities
- Add (`a`), Edit (`e`), Duplicate (`c`), Delete (`d` / `Delete`) shortcuts; Enter on a row edits it
- Deleting a class, teacher or subject offers to cascade delete its assignments
- Deleting a room that is a class home room is blocked until classes are moved
- Weekly grid views: toggle between Class Schedule, Teacher Schedule, Room Schedule, and List View
- Export generated timetable directly to CSV or JSON with one click
- "Load sample" button in the Project tab to test and explore immediately (uses the `test_json.json` shipped in the source checkout)
- Every edit is validated against the full project before it is accepted
- Feasibility analysis: checks total capacity vs required subject periods in real time
- Unsaved changes are highlighted and confirmed before quitting
- Generate runs in a background thread and reuses the last result as a hint
- `Ctrl+S` save, `Ctrl+O` open, `Ctrl+G` generate, `Ctrl+N` new, `F5` validate, `?` help, `Ctrl+Q` quit

## CLI

```bash
# Validate a project, including all cross-references
timetable validate school.json

# Print tables per class
timetable generate school.json

# Write JSON, with a custom time budget
timetable generate school.json -o result.json -t 120

# Re-solve using a previous result as a warm start
timetable generate school.json --previous-hint result.json

# Print the input JSON schema
timetable schema
```

`generate` options:

| Option | Description |
| --- | --- |
| `-o, --output PATH` | Output file (omit to print to stdout) |
| `-f, --format json\|table\|csv` | Output format (defaults to the `--output` extension, else `table`) |
| `-t, --time-limit SECONDS` | Maximum solver time (default `60`; raise it to search longer) |
| `-s, --seed INT` | Random seed (default `42`) |
| `-w, --workers INT` | Solver threads (default `8`; use `1` for reproducible runs) |
| `--fast` | Quick result with a 5-second solver budget (lower quality) |
| `--gap-limit FLOAT` | Stop once within this relative gap of the best bound (e.g. `0.02` = 2%) |
| `--patience SECONDS` | Stop after this long without a better timetable (default `5`; `0` disables) |
| `--previous-hint PATH` | Warm-start from a previous JSON result |
| `--violations/--no-violations` | Print the violation report (default on) |

The solver stops early once it has gone `--patience` seconds without finding a
better timetable, so most runs finish well inside the time limit without
giving up quality. Use `--fast` for a quick preview, or a larger
`--time-limit` when you want it to keep searching.

`src/main.py` remains a thin entry point for the existing Nuitka build:

```bash
python src/main.py generate school.json
```

## Input format

The input is a JSON object matching `TimeTableGenerationInput`:

```json
{
  "project": {
    "name": "My School",
    "slots": 8,
    "days": ["Mon", "Tue", "Wed", "Thu", "Fri"]
  },
  "rooms": [
    { "name": "Room 101", "is_lab": false, "constraints": { "capacity": 1 } }
  ],
  "classes": [
    { "name": "Grade 10 - A", "room_id": "..." }
  ],
  "teachers": [
    {
      "name": "Ananya Sharma",
      "constraints": { "max_per_day": 6, "max_per_week": 25, "max_consecutive": 3 }
    }
  ],
  "subjects": [
    {
      "name": "Mathematics",
      "constraints": {
        "morning_tendency": "High",
        "max_per_day": 2,
        "min_per_day": 0,
        "max_per_week": 7,
        "min_per_week": 7,
        "max_consecutive": 2,
        "min_consecutive": 1
      }
    }
  ],
  "teacher_assignments": [
    {
      "class_id": "...",
      "teacher_id": "...",
      "subject_id": "...",
      "tag": "Batch 1",
      "target_room_id": "...",
      "constraints": {
        "period_length": 2,
        "co_schedule_with": ["<batch_2_assignment_id>"]
      }
    }
  ]
}
```

`id` fields are optional and default to a new UUID. Get the full schema with
`timetable schema`. Validation rejects duplicate ids/names, unknown references,
duplicate class/teacher/subject/tag assignments, min values above max values, empty
day lists and first-slot days that are not active project days. `slots` is capped
at 24 per day, and an assignment's `period_length` must fit between the project's
break slots. Co-scheduled assignments synchronize time slots across classes
(merged lectures) or within the same class (concurrent split lab batches,
electives, and minors). The room `is_lab` flag is descriptive metadata; it does
not constrain which rooms the solver assigns.

## Embedding

```python
from maketable import TimeTableGenerationInput, generate

data = TimeTableGenerationInput.model_validate_json(
    open("school.json", encoding="utf-8").read()
)
# fast=True caps the solve at 5 s; gap_limit and patience tune early stopping.
response = generate(data, time_limit_sec=60, seed=42, workers=1)

if response.success:
    for entry in response.entries:
        print(entry.day, entry.slot, entry.class_name, entry.subject_name)
for violation in response.violations:
    print("soft violation:", violation.error_msg, violation.val)
```

For finer control, use `TimeTableGenerator` directly:

```python
from maketable import TimeTableGenerationInput, TimeTableGenerator

data = TimeTableGenerationInput.model_validate_json(raw_json)
generator = TimeTableGenerator(RawData=data, HintData=previous_response)
response = generator.solve(time_limit_sec=60, seed=42, workers=8)
```

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Building

```bash
./build.sh
```

The script uses Nuitka to produce a single executable. The checked-in command
targets Linux and includes the TUI dependencies; Windows and macOS variants are
kept below it as comments.

Onefile compression needs the `zstandard` dev dependency, which `uv sync`
installs by default. Without it Nuitka stores the payload uncompressed and the
binary is roughly five times larger. Textual's tree-sitter grammar files are
deliberately not bundled (they belong to its optional `syntax` extra).

### GitHub Actions releases

[`.github/workflows/release.yml`](.github/workflows/release.yml) builds
compressed Nuitka `--onefile` binaries for `linux-x86_64` and `macos-aarch64`,
runs the test suite and binary smoke tests, and packages
`timetable-<target>.tar.gz` archives with `.sha256` checksums. Pushing a `v*`
tag (e.g., `v0.1.0`) automatically publishes the GitHub Release used by
`scripts/install.sh`; the workflow can also be triggered manually via
`workflow_dispatch`.

## Architecture

- `src/maketable/models.py` — pydantic models and cross-reference validation
- `src/maketable/solver.py` — CP-SAT model construction and solving
- `src/maketable/cli.py` — Typer CLI
- `src/maketable/tui.py` — Textual editor
- `src/main.py` — entry point kept for the existing build script

## License

MIT
