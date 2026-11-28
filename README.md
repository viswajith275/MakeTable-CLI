
# MakeTable

**School & department timetable generator powered by Google OR-Tools CP-SAT**

Interactive terminal UI · Scriptable CLI · Embeddable Python library

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![OR-Tools](https://img.shields.io/badge/solver-OR--Tools%20CP--SAT-orange)](https://developers.google.com/optimization)


---

## Why MakeTable?

Building conflict-free school and college timetables by hand is painful.  
MakeTable turns the problem into a proper constraint satisfaction model and solves it with Google’s battle-tested CP-SAT solver.

You get:

- An interactive **Textual TUI** for building and exploring schedules
- A fast **CLI** for scripting and CI
- A clean **Python library** you can embed in your own tools

Real-world features for schools *and* departments: break slots, teacher unavailability, multi-period labs, merged lectures, concurrent split batches, fixed slots, warm starts, and soft-constraint violation reporting.

---

## Features

| Area | Capabilities |
|------|--------------|
| **Solver** | OR-Tools CP-SAT with hard + soft constraints, workload balancing, gap minimization, morning preference, warm-start hints, early stopping |
| **TUI** | Full project editor, live feasibility checks, class/teacher/room grid views, background solving, CSV/JSON export |
| **CLI** | Validate → Generate → Export (table / JSON / formula-safe CSV). Rich terminal output |
| **Modeling** | Universal break slots · Teacher unavailable/avoid slots · Max working days · Atomic multi-period blocks · Merged lectures · Concurrent split batches · Fixed subject day/slot pins |
| **Quality** | Pre-solve validation guardrails, soft-constraint violation reports, deterministic mode, seed control |

---

## Installation

### Prebuilt Binary (Linux & macOS)

```bash
# Install
curl -fsSL https://raw.githubusercontent.com/viswajith275/MakeTable-TUI/main/scripts/install.sh | bash

# Uninstall
curl -fsSL https://raw.githubusercontent.com/viswajith275/MakeTable-TUI/main/scripts/uninstall.sh | bash
```

### Prebuilt Binary (Windows PowerShell)

```powershell
# Install
irm https://raw.githubusercontent.com/viswajith275/MakeTable-TUI/main/scripts/install.ps1 | iex

# Uninstall
irm https://raw.githubusercontent.com/viswajith275/MakeTable-TUI/main/scripts/uninstall.ps1 | iex
```

After installation the `timetable` command is available globally.

### From Source

Requires **Python 3.13+**.

```bash
git clone https://github.com/viswajith275/MakeTable-TUI.git
cd MakeTable-TUI

# With uv (recommended)
uv sync
uv run timetable

# Or with pip
pip install -e .
timetable
```

---

## Quick Start

```bash
# Launch the interactive TUI
timetable

# Or open / create a project file
timetable tui school.json

# Validate a project
timetable validate school.json

# Generate a timetable
timetable generate school.json
```

---

## Usage

### 1. Interactive Terminal Editor (TUI)

```bash
timetable                  # new project
timetable tui school.json  # open existing project
```

Work through the tabs from left to right:

1. **Project** — Set project name, slots per day (`1..24`), active days, and universal `break_slots`. Use the live *Feasibility* panel to confirm available weekly capacity covers each class’s minimum periods, or click **Load sample** to explore `test_json.json`.
2. **Rooms** — Define classrooms and labs, including simultaneous class `capacity` for shared halls/auditoriums.
3. **Classes** — Add classes or sections and assign each one a default home room.
4. **Teachers** — Configure daily, weekly, and consecutive class limits, optional `max_days_per_week`, hard `unavailable_slots` (e.g. `Mon:1, Wed:3-4`), and soft `avoid_slots`.
5. **Subjects** — Set `morning_tendency` (`Low`, `Med`, `High`), daily/weekly/consecutive min and max limits, and `fixed_slots` to pin the subject to specific day/slot pairs (e.g. `Tue:6-8, Thu:6-8`).
6. **Assignments** — Pair a class, teacher, and subject with optional batch/elective `tag`, `target_room_id` override, atomic `period_length`, `co_schedule_with` links, and `first_slot_days`.
7. **Timetable** — Adjust *Time*, *Seed*, *Workers*, *Gap*, *Patience*, *Fast*, and *Hint*, then generate. Inspect **Class Schedule**, **Teacher Schedule**, **Room Schedule**, or **All Entries**, and export to CSV or JSON.

#### Keyboard Shortcuts

| Shortcut                      | Action                                              |
|-------------------------------|-----------------------------------------------------|
| `Ctrl+S` / `Ctrl+Shift+S`     | Save / Save As                                      |
| `Ctrl+O`                      | Open project JSON                                   |
| `Ctrl+N`                      | New project                                         |
| `Ctrl+G`                      | Generate timetable                                  |
| `F5`                          | Validate project                                    |
| `?` / `F1`                    | Open help modal                                     |
| `Ctrl+Q`                      | Quit                                                |
| `a`                           | Add item in active tab                              |
| `e` / `Enter`                 | Edit selected row (or Project settings on Project tab) |
| `c`                           | Duplicate selected row                              |
| `d` / `Delete`                | Delete selected row (prompts to cascade assignments when referenced) |

---

### 2. Command-Line Interface (CLI)

```bash
# Validate a project and all cross-references
timetable validate school.json

# Generate and print per-class timetable tables
timetable generate school.json

# Write JSON output with a custom time budget
timetable generate school.json -o result.json -t 120

# Warm-start from a previous JSON result
timetable generate school.json --previous-hint result.json

# Print the input JSON Schema
timetable schema
```

#### CLI Options

| Option                        | Description |
|-------------------------------|-------------|
| `-o, --output PATH`           | Output file (omit to print to stdout) |
| `-f, --format json\|table\|csv` | Output format (defaults to the `--output` extension, else `table`) |
| `-t, --time-limit SECONDS`    | Maximum solver time in seconds (default `60`) |
| `-s, --seed INT`              | Random seed (default `42`) |
| `-w, --workers INT`           | Solver threads (default `8`; use `1` for deterministic runs) |
| `--fast`                      | Quick preview capped at 5 seconds |
| `--gap-limit FLOAT`           | Stop once within this relative optimality gap (e.g. `0.02` = 2%) |
| `--patience SECONDS`          | Stop after this many seconds without improvement (default `5`; `0` disables) |
| `--previous-hint PATH`        | Warm-start from a previous JSON result |
| `--violations` / `--no-violations` | Include or hide the soft-constraint violation report (default on) |

#### Common Recipes

```bash
# Quick 5-second preview
timetable generate school.json --fast

# Deterministic JSON export
timetable generate school.json -w 1 -s 42 -o result.json

# Pipe machine-readable JSON to jq, or export clean CSV
timetable generate school.json -f json | jq '.entries | length'
timetable generate school.json -o schedule.csv --no-violations

# Deep search using a previous result as a hint with no patience cutoff
timetable generate school.json --previous-hint result.json -t 180 --patience 0 -o result_v2.json
```

---

### 3. Input Format & Modeling Patterns

Input files match `TimeTableGenerationInput` (`id` fields are optional and default to a generated UUID):

```json
{
  "project": {
    "name": "My School",
    "slots": 8,
    "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
    "break_slots": [4]
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
      "constraints": {
        "max_per_day": 6,
        "max_per_week": 25,
        "max_consecutive": 3,
        "max_days_per_week": 5,
        "unavailable_slots": [{ "day": "Mon", "slot": 1 }],
        "avoid_slots": [{ "day": "Fri", "slot": 8 }]
      }
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
        "min_consecutive": 1,
        "fixed_slots": [
          { "day": "Tue", "slot": 6 },
          { "day": "Tue", "slot": 7 },
          { "day": "Tue", "slot": 8 }
        ]
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
        "first_slot_days": ["Mon"],
        "co_schedule_with": ["<batch_2_assignment_id>"]
      }
    }
  ]
}
```

#### Modeling Patterns

- **Merged lectures (across classes)** — Create one assignment per class with the same teacher, subject, and `target_room_id` (room `capacity >= 2`), and link their IDs in `co_schedule_with`.
- **Concurrent split batches (within a class)** — Create two assignments on the same class with distinct `tag` values (e.g. `"Batch 1"`, `"Batch 2"`), separate teachers and rooms, and link them via `co_schedule_with`.
- **Atomic lab blocks** — Set `period_length > 1`; blocks are scheduled contiguously and never cross `break_slots`.
- **Fixed subject slots** — Add `fixed_slots` to a subject to pin it to specific day/slot pairs for every class that takes it (e.g. minor courses on `Tue:6-8` and `Thu:6-8`; list the whole block for multi-period assignments). The TUI accepts ranges like `Tue:6-8, Thu:6-8`.

---

### 4. Embedding as a Python Library

```python
from pathlib import Path
from maketable import (
    TimeTableGenerationInput,
    TimeTableGenerator,
    format_day_slots,
    generate,
    parse_day_slots,
    validate_project,
)

# Validate from Path, JSON string, dict, or TimeTableGenerationInput
errors = validate_project(Path("school.json"))
if errors:
    raise ValueError("\n".join(errors))

data = TimeTableGenerationInput.model_validate_json(
    Path("school.json").read_text(encoding="utf-8")
)

# One-call solve (fast=True caps at 5s; gap_limit and patience tune early stopping)
response = generate(data, time_limit_sec=60, seed=42, workers=1, patience=5)

if response.success:
    for entry in response.entries:
        print(entry.day, entry.slot, entry.class_name, entry.subject_name)

for violation in response.violations:
    print("soft violation:", violation.error_msg, violation.val)

# Inspect grids by class, teacher, or room
class_grid = response.get_class_grid("Grade 10 - A", total_slots=data.project.slots)
teacher_grid = response.get_teacher_grid("Ananya Sharma", total_slots=data.project.slots)
room_grid = response.get_room_grid("Room 101", total_slots=data.project.slots)

# Export formula-safe CSV
Path("schedule.csv").write_text(response.to_csv(), encoding="utf-8")

# Parse and format DaySlot strings
slots = parse_day_slots("Mon:1, Wed:2-3")
assert format_day_slots(slots) == "Mon:1, Wed:2, Wed:3"

# Or instantiate TimeTableGenerator directly with a warm-start hint
generator = TimeTableGenerator(RawData=data, HintData=response)
refined = generator.solve(time_limit_sec=60, seed=42, workers=8)
```

---

## Architecture

MakeTable is built in Python 3.13 around four focused modules:

| Module              | Role |
|---------------------|------|
| `src/maketable/models.py` | Pydantic v2 data models, cross-reference validators, feasibility calculations, grid builders, and CSV export |
| `src/maketable/solver.py` | OR-Tools CP-SAT constraint model, slack tracking, greedy & warm-start hinting, and stagnation-based early stopping |
| `src/maketable/cli.py`    | Typer CLI with Rich formatted timetable tables, CSV/JSON output, and schema inspection |
| `src/maketable/tui.py`    | Textual interactive terminal UI with modal validation guard rails, search filters, background solving, and grid views |

### Inside the CP-SAT Solver

- **Co-scheduled variable sharing** — Linked assignments (via `co_schedule_with`) are grouped with union-find so every member of a group shares a single boolean decision grid per day and slot.
- **Atomic multi-period blocks** — When `period_length > 1`, decision variables represent valid contiguous start slots that never cross `break_slots`.
- **Hard constraints** — At most one non-co-scheduled period per class and per teacher in any slot, room capacity limits, universal break slots fixed to zero, and teacher unavailable slots fixed to zero.
- **Soft constraints with slack reporting** — Teacher limits, subject min/max rules, assignment `first_slot_days`, and subject `fixed_slots` use bounded slack variables weighted in the objective.
- **Silent quality objectives** — Penalize idle gaps, balance daily teacher loads, reward `morning_tendency`, and encourage slot variety.
- **Hinting & early stopping** — Lightweight greedy pass seeds conflict-free initial hints. A plateau stopper halts solving once the objective stops improving for `patience` seconds.

---

## Development

```bash
uv sync
uv run python -m unittest discover -s tests -v
```

---

## License

MIT © [viswajith275](https://github.com/viswajith275)
