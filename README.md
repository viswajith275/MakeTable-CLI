# MakeTable-CLI

A powerful command-line tool for generating optimized school timetables using constraint programming. This tool uses Google OR-Tools' CP-SAT solver to handle complex scheduling constraints and generate feasible timetables for schools.

## Features

- **Constraint-based Scheduling**: Uses CP-SAT solver for robust constraint satisfaction
- **Flexible Input Format**: JSON-based configuration for easy integration
- **Multiple Output Formats**: Display results as formatted tables or export to JSON
- **Rich CLI Output**: Beautiful console output using Rich library
- **Comprehensive Constraints Support**:
  - Teacher availability and workload limits (daily, weekly, consecutive)
  - Subject scheduling preferences (morning preference, slot variety, consecutive limits)
  - Room capacity and availability
  - Class scheduling requirements
  - Custom teacher assignments with first-slot preferences

## Installation

### Prerequisites
- Python 3.13 or higher
- UV package manager (recommended) or pip

### Using UV (Recommended)
```bash
git clone https://github.com/viswajith275/MakeTable-CLI.git
cd MakeTable-CLI
uv sync
```

### Using pip
```bash
git clone https://github.com/viswajith275/MakeTable-CLI.git
cd MakeTable-CLI
pip install -e .
```

## Dependencies

- **ortools** (≥9.15): Google's operations research tools for constraint programming
- **pydantic** (≥2.13): Data validation using Python type annotations
- **typer** (≥0.26): Modern CLI framework
- **rich** (≥15.0): Beautiful terminal output formatting

## Usage

### Basic Commands

#### 1. Validate Input
Validate your input JSON against the schema:
```bash
python main.py validate input.json
```

#### 2. Generate Timetable
Generate a timetable from your input configuration:
```bash
python main.py generate input.json --output output.json --format table
```

#### 3. View Schema
Display the JSON schema for the input format:
```bash
python main.py schema --output schema.json
```

### Command Options

#### Generate Command
```bash
python main.py generate INPUT_FILE [OPTIONS]
```

**Options:**
- `--output, -o` (PATH): Output file path (omit to print to stdout)
- `--format, -f` (FORMAT): Output format - `json` or `table` (default: `table`)
- `--time-limit, -t` (FLOAT): Solver time budget in seconds (default: 60.0)
- `--seed, -s` (INT): Random seed for reproducibility (default: 42)
- `--violations/--no-violations`: Show violation report (default: True)

## Input Format

The tool accepts JSON input matching the `TimeTableGenerationInput` schema. Key components:

### Structure
```json
{
  "project": {
    "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
    "slots": 6
  },
  "teachers": [...],
  "subjects": [...],
  "rooms": [...],
  "classes": [...],
  "teacher_assignments": [...]
}
```

### Get the Full Schema
```bash
python main.py schema
```

## Output Format

### Table Format
Displays a beautifully formatted timetable for each class in the terminal:
```
Timetable — 10-A
┏━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┓
┃ Day   ┃ Slot 0    ┃ Slot 1    ┃ Slot 2    ┃ Slot 3    ┃ Slot 4    ┃
┡━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━┩
│ Mon   │ Maths     │ Science   │ English   │ History   │ PE        │
│       │ Mr. Smith │ Dr. Johnson│ Ms. Brown │ Mr. Davis │ Coach Lee │
│       │ Room 101  │ Room 102  │ Room 103  │ Room 104  │ Gym       │
```

### JSON Format
Complete structured output with all details and violations.

## Constraints

The solver handles multiple types of constraints:

### Teacher Constraints
- Maximum classes per day
- Maximum classes per week
- Maximum consecutive classes
- Daily workload balancing
- Gap minimization

### Subject Constraints
- Maximum/minimum classes per day
- Maximum/minimum classes per week
- Maximum/minimum consecutive classes
- Morning preference tendency
- Slot variety (avoid same slot on multiple days)

### Room Constraints
- Capacity limits
- Single-use vs. multi-use rooms

### General Constraints
- Teachers cannot teach two classes simultaneously
- Classes cannot have two subjects simultaneously
- Room capacity adherence
- Custom assignment requirements

## Architecture

### Key Components

- **main.py**: CLI interface and I/O handling
- **solver.py**: Core constraint programming logic using OR-Tools CP-SAT
- **models.py**: Pydantic data models for validation

### Solver Strategy

1. Creates boolean variables for each possible assignment
2. Applies hard constraints (no conflicts, capacity limits)
3. Applies soft constraints with slack variables for violation tracking
4. Minimizes violations and optimizes for desired properties
5. Returns the best feasible solution within time limit

## Testing

A sample test configuration is provided in `test_json.json`. You can use it to test the tool:

```bash
python main.py generate test_json.json --format table
```

## Performance Tips

- **Time Limit**: Increase `--time-limit` for larger problems (more time = better solutions)
- **Random Seed**: Try different seeds if the solver gets stuck in local optima
- **Problem Size**: For very large schools, consider breaking the problem into smaller sub-problems
- **Constraint Tuning**: Adjust soft constraint weights in `solver.py` based on your priorities

## Troubleshooting

### "No feasible timetable found"
- Relax some constraints (increase time limits for subjects/teachers)
- Check for conflicting requirements
- Increase the `--time-limit`

### "Input validation failed"
- Verify your JSON matches the schema: `python main.py schema`
- Check for required fields and correct data types

## Building

The project includes a build script for creating executables:

```bash
./build.sh
```

## License

This project is open source and available under the MIT License.

## Contributing

Contributions are welcome! Feel free to open issues or submit pull requests.

## Acknowledgments

- Built with [Google OR-Tools](https://developers.google.com/optimization/install)
- CLI powered by [Typer](https://typer.tiangolo.com/)
- Output formatting with [Rich](https://rich.readthedocs.io/)
