<div align="center">
  <h1>MakeTable</h1>
  <p><strong>School &amp; department timetable generator powered by Google OR-Tools CP-SAT</strong></p>
  <p>Ships as an interactive terminal editor (TUI), a scriptable CLI, and an embeddable Python library.</p>
</div>

---

## Features

- **Constraint-based scheduling** — Solves schedules with Google OR-Tools CP-SAT while balancing workload and minimizing idle gaps.
- **Interactive TUI** — Manage projects, rooms, classes, teachers, subjects, and assignments with real-time feasibility checks and weekly grid views.
- **Flexible CLI** — Validate inputs, inspect the JSON Schema, and output schedules as rich terminal tables, JSON, or formula-safe CSV.
- **Validation guard rails** — Catches duplicate names/IDs, missing references, impossible min/max ranges, over-allocated weekly slots, and break-crossing blocks before solving.
- **Department &amp; college support** — Universal break slots, teacher unavailable/avoided slots, max working days per week, multi-period atomic lab blocks, merged lectures, concurrent split batches, and fixed subject day/slot pins.
- **Warm starts** — Reuse a previous schedule JSON as a solver hint for faster re-solving.

---

## Installation

### Prebuilt Binary (Linux &amp; macOS)

<p>Install the <code>timetable</code> command:</p>

```bash
curl -fsSL https://raw.githubusercontent.com/viswajith275/MakeTable-CLI/main/scripts/install.sh | bash
```

<p>To uninstall:</p>

```bash
curl -fsSL https://raw.githubusercontent.com/viswajith275/MakeTable-CLI/main/scripts/uninstall.sh | bash
```

### From Source

<p>Requires Python 3.13+.</p>

```bash
git clone https://github.com/viswajith275/MakeTable-CLI.git
cd MakeTable-CLI
uv sync
```

<p>Or with <code>pip</code>:</p>

```bash
pip install -e .
```

---

## How to Use

### 1. Interactive Terminal Editor (TUI)

<p>Launch the editor directly or pass a project file (new files are created on save):</p>

```bash
timetable
timetable tui school.json
```

<details>
<summary><strong>TUI Workflow &amp; Keyboard Shortcuts</strong></summary>

<br>

<p>Work through the tabs from left to right to build a timetable:</p>

<ol>
  <li><strong>Project</strong> — Set project name, slots per day (<code>1..24</code>), active days, and universal <code>break_slots</code>. Use the live <em>Feasibility</em> panel to confirm available weekly capacity covers each class's minimum periods, or click <strong>Load sample</strong> to explore <code>test_json.json</code>.</li>
  <li><strong>Rooms</strong> — Define classrooms and labs, including simultaneous class <code>capacity</code> for shared halls/auditoriums.</li>
  <li><strong>Classes</strong> — Add classes or sections and assign each one a default home room.</li>
  <li><strong>Teachers</strong> — Configure daily, weekly, and consecutive class limits, optional <code>max_days_per_week</code>, hard <code>unavailable_slots</code> (e.g. <code>Mon:1, Wed:3-4</code>), and soft <code>avoid_slots</code>.</li>
  <li><strong>Subjects</strong> — Set <code>morning_tendency</code> (<code>Low</code>, <code>Med</code>, <code>High</code>), daily/weekly/consecutive min and max limits, and <code>fixed_slots</code> to pin the subject to specific day/slot pairs (e.g. <code>Tue:6-8, Thu:6-8</code>).</li>
  <li><strong>Assignments</strong> — Pair a class, teacher, and subject with optional batch/elective <code>tag</code>, <code>target_room_id</code> override, atomic <code>period_length</code>, <code>co_schedule_with</code> links, and <code>first_slot_days</code>.</li>
  <li><strong>Timetable</strong> — Adjust <em>Time</em>, <em>Seed</em>, <em>Workers</em>, <em>Gap</em>, <em>Patience</em>, <em>Fast</em>, and <em>Hint</em>, then generate. Inspect <strong>Class Schedule</strong>, <strong>Teacher Schedule</strong>, <strong>Room Schedule</strong>, or <strong>All Entries</strong>, and export to CSV or JSON.</li>
</ol>

<table>
  <thead>
    <tr>
      <th>Shortcut</th>
      <th>Action</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><kbd>Ctrl</kbd>+<kbd>S</kbd> / <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>S</kbd></td>
      <td>Save / Save As</td>
    </tr>
    <tr>
      <td><kbd>Ctrl</kbd>+<kbd>O</kbd></td>
      <td>Open project JSON</td>
    </tr>
    <tr>
      <td><kbd>Ctrl</kbd>+<kbd>N</kbd></td>
      <td>New project</td>
    </tr>
    <tr>
      <td><kbd>Ctrl</kbd>+<kbd>G</kbd></td>
      <td>Generate timetable</td>
    </tr>
    <tr>
      <td><kbd>F5</kbd></td>
      <td>Validate project</td>
    </tr>
    <tr>
      <td><kbd>?</kbd> / <kbd>F1</kbd></td>
      <td>Open help modal</td>
    </tr>
    <tr>
      <td><kbd>Ctrl</kbd>+<kbd>Q</kbd></td>
      <td>Quit</td>
    </tr>
    <tr>
      <td><kbd>a</kbd></td>
      <td>Add item in active tab</td>
    </tr>
    <tr>
      <td><kbd>e</kbd> / <kbd>Enter</kbd></td>
      <td>Edit selected row (or Project settings on Project tab)</td>
    </tr>
    <tr>
      <td><kbd>c</kbd></td>
      <td>Duplicate selected row</td>
    </tr>
    <tr>
      <td><kbd>d</kbd> / <kbd>Delete</kbd></td>
      <td>Delete selected row (prompts to cascade assignments when referenced)</td>
    </tr>
  </tbody>
</table>

</details>

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

<details>
<summary><strong>CLI Options &amp; Usage Recipes</strong></summary>

<br>

<table>
  <thead>
    <tr>
      <th>Option</th>
      <th>Description</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><code>-o, --output PATH</code></td>
      <td>Output file (omit to print to stdout)</td>
    </tr>
    <tr>
      <td><code>-f, --format json|table|csv</code></td>
      <td>Output format (defaults to the <code>--output</code> extension, else <code>table</code>)</td>
    </tr>
    <tr>
      <td><code>-t, --time-limit SECONDS</code></td>
      <td>Maximum solver time in seconds (default <code>60</code>)</td>
    </tr>
    <tr>
      <td><code>-s, --seed INT</code></td>
      <td>Random seed (default <code>42</code>)</td>
    </tr>
    <tr>
      <td><code>-w, --workers INT</code></td>
      <td>Solver threads (default <code>8</code>; use <code>1</code> for deterministic runs)</td>
    </tr>
    <tr>
      <td><code>--fast</code></td>
      <td>Quick preview capped at 5 seconds</td>
    </tr>
    <tr>
      <td><code>--gap-limit FLOAT</code></td>
      <td>Stop once within this relative optimality gap (e.g. <code>0.02</code> = 2%)</td>
    </tr>
    <tr>
      <td><code>--patience SECONDS</code></td>
      <td>Stop after this many seconds without improvement (default <code>5</code>; <code>0</code> disables)</td>
    </tr>
    <tr>
      <td><code>--previous-hint PATH</code></td>
      <td>Warm-start from a previous JSON result</td>
    </tr>
    <tr>
      <td><code>--violations / --no-violations</code></td>
      <td>Include or hide the soft-constraint violation report (default on)</td>
    </tr>
  </tbody>
</table>

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

</details>

### 3. Input Format &amp; Modeling Patterns

<details>
<summary><strong>JSON Structure, Constraints &amp; Co-Scheduling Patterns</strong></summary>

<br>

<p>Input files match <code>TimeTableGenerationInput</code> (<code>id</code> fields are optional and default to a generated UUID):</p>

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

<ul>
  <li><strong>Merged lectures (across classes)</strong> — Create one assignment per class with the same teacher, subject, and <code>target_room_id</code> (room <code>capacity &gt;= 2</code>), and link their IDs in <code>co_schedule_with</code>.</li>
  <li><strong>Concurrent split batches (within a class)</strong> — Create two assignments on the same class with distinct <code>tag</code> values (e.g. <code>"Batch 1"</code>, <code>"Batch 2"</code>), separate teachers and rooms, and link them via <code>co_schedule_with</code>.</li>
  <li><strong>Atomic lab blocks</strong> — Set <code>period_length</code> &gt; <code>1</code>; blocks are scheduled contiguously and never cross <code>break_slots</code>.</li>
  <li><strong>Fixed subject slots</strong> — Add <code>fixed_slots</code> to a subject to pin it to specific day/slot pairs for every class that takes it (e.g. minor courses on <code>Tue:6-8</code> and <code>Thu:6-8</code>; list the whole block for multi-period assignments). The TUI accepts ranges like <code>Tue:6-8, Thu:6-8</code>.</li>
</ul>

</details>

### 4. Embedding as a Python Library

<details>
<summary><strong>Python API Examples (<code>generate</code>, <code>validate_project</code>, Grids &amp; CSV)</strong></summary>

<br>

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

</details>

---

## How It's Made

<p>MakeTable is built in Python 3.13 around four focused modules:</p>

<table>
  <thead>
    <tr>
      <th>Module</th>
      <th>Role</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><code>src/maketable/models.py</code></td>
      <td>Pydantic v2 data models, cross-reference validators, feasibility calculations, grid builders, and CSV export</td>
    </tr>
    <tr>
      <td><code>src/maketable/solver.py</code></td>
      <td>OR-Tools CP-SAT constraint model, slack tracking, greedy &amp; warm-start hinting, and stagnation-based early stopping</td>
    </tr>
    <tr>
      <td><code>src/maketable/cli.py</code></td>
      <td>Typer CLI with Rich formatted timetable tables, CSV/JSON output, and schema inspection</td>
    </tr>
    <tr>
      <td><code>src/maketable/tui.py</code></td>
      <td>Textual interactive terminal UI with modal validation guard rails, search filters, background solving, and grid views</td>
    </tr>
  </tbody>
</table>

<details>
<summary><strong>Inside the CP-SAT Solver &amp; Constraint Model</strong></summary>

<br>

<ul>
  <li><strong>Co-scheduled variable sharing</strong> — Linked assignments (via <code>co_schedule_with</code>) are grouped with union-find so every member of a group shares a single boolean decision grid per day and slot rather than duplicating variables and equality constraints.</li>
  <li><strong>Atomic multi-period blocks</strong> — When <code>period_length &gt; 1</code>, decision variables represent valid contiguous start slots that never cross <code>break_slots</code>; slot occupancy is derived from overlapping start windows and consecutive starts are constrained with <code>at_most_one</code>.</li>
  <li><strong>Hard constraints</strong> — At most one non-co-scheduled period per class and per teacher in any slot, room <code>capacity</code> limits per slot, universal <code>break_slots</code> fixed to zero, and teacher <code>unavailable_slots</code> fixed to zero.</li>
  <li><strong>Soft constraints with slack reporting</strong> — Teacher limits (<code>max_per_day</code>, <code>max_per_week</code>, <code>max_consecutive</code>, <code>max_days_per_week</code>, <code>avoid_slots</code>), subject min/max rules, assignment <code>first_slot_days</code>, and subject <code>fixed_slots</code> use bounded slack variables weighted in the minimization objective so impossible combinations degrade gracefully and surface actionable violation messages.</li>
  <li><strong>Silent quality objectives</strong> — Linear prefix/suffix occupancy checks penalize idle teacher and class gaps (ignoring break slots), balance daily teacher loads, reward <code>morning_tendency</code>, and encourage slot variety across days.</li>
  <li><strong>Hinting &amp; early stopping</strong> — When no previous result is supplied, a lightweight greedy pass seeds conflict-free initial hints (prioritizing pinned first-slot classes, multi-period labs, and shared resources). During search, a background <code>_PlateauStopper</code> callback halts solving once the objective stops improving for <code>patience</code> seconds.</li>
</ul>

</details>

---

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## License

MIT
