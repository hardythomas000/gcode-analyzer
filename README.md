# G-code Analyzer

A CNC G-code analysis tool that catches programming issues before they reach the machine. Built as a Claude Code skill with a standalone Python parser.

## What it does

Point it at any `.nc` / `.tap` / `.gcode` file and get:

- **Tool list** with descriptions, RPM, feed rates, stock-to-leave values
- **Cycle time estimate** (per-tool and total, based on programmed feeds + rapid rates)
- **Tool change sequence** visualization (T1 → T126 → T34 → ...)
- **Issue detection:**
  - Multiple toolpaths sharing the same tool without M01 between them (operator can't stop to inspect)
  - Missing safety line (no G28/G53)
  - Feed moves without spindle running
  - Cutting without tool length compensation (G43)
  - Rapid moves into negative Z (crash risk)
  - Missing program end (M30)

## Why it exists

When you're a CAM programmer pushing out programs in Mastercam or hyperMILL, it's easy to miss things like:

- **Forgotten M01 stops** — you have 3 operations on the same endmill (rough, chamfer, finish) and Mastercam just chains them together. The operator has no way to stop between ops to check the part, measure, or clear chips.
- **Silent failures** — spindle didn't start, tool length comp missing, safety line absent. These are the kind of things that cause crashes at 3am when nobody's watching.

This tool does a quick structural audit of the G-code itself — no CAM software needed, just the `.nc` file.

### Relationship to [universal-setup-sheet](https://github.com/hardythomas000/universal-setup-sheet)

The universal-setup-sheet project has a much more sophisticated G-code parser (~700+ lines of JS) designed for **generating setup sheets** — it handles coolant subsystems (Flood/TSC/Mist/Wash), wear compensation tracking, M00 stop buffering, hyperMILL multi-op detection, and tool library import from CSV/HTML.

This tool is different — it focuses on **analysis and auditing**:
- Cycle time estimation
- Feed/speed auditing against material ranges
- Issue detection and program comparison
- Quick structural health check before posting to the machine

They parse the same files for different purposes.

## How it works

### Parser (`scripts/parse_gcode.py`)

A ~300-line Python script that:

1. **Pre-scans** the header for Mastercam pipe-format tool comments `( T1 | DESC | H1 | ... )` and program metadata (date, material, program number)
2. **Walks every line** tracking modal state: current tool, spindle on/off, coolant, tool length comp, work offsets, XYZ position
3. **Extracts M-codes** with proper word-boundary matching (`\bM0*(\d+)`) so M30 doesn't false-match as M3
4. **Calculates distances** between moves and estimates time at programmed feed rates (rapids at 25.4 m/min for Haas)
5. **Detects operation boundaries** — both Mastercam style `( ROUGH FACE )` and hyperMILL style `( OPERATION 1: T15 3D ROUGHING )` — and flags when consecutive operations on the same tool have no M01 between them

### Skill (`SKILL.md`)

The SKILL.md file is a Claude Code skill definition. When loaded into Claude Code, it tells Claude how to use the parser, what to look for beyond the automated checks (canned cycle issues, cutter comp activation during cuts, coolant strategy), and how to present results.

## Usage

### Standalone Python
```bash
# Markdown report
python3 scripts/parse_gcode.py program.nc

# JSON output (for piping to other tools)
python3 scripts/parse_gcode.py program.nc --json
```

### As a Claude Code skill
Copy to your skills directory:
```bash
cp -r . ~/.claude/skills/gcode-analyze/
```
Then ask Claude: "analyze this G-code" or "check ~/programs/part.nc"

## Supported dialects

- **Mastercam** (Haas/Fanuc post) — pipe-delimited tool headers, N-numbered lines
- **hyperMILL** — TOOL LIST blocks, OPERATION comments, multi-op per tool
- **Mazak** — Fanuc-compatible, G43.4/RTCP
- **Generic Fanuc** — any standard G-code with G0/G1/G2/G3 motion

## Example output

```
## Program: 16A32067_REV-B
- Program #: O0000
- Material: ALUMINUM MM - 2024
- Units: metric
- Work Offsets: G55, G57

## Tool List
| T#  | Description              | RPM   | Feeds         | Stock Leave    |
|-----|--------------------------|-------|---------------|----------------|
| T1  | 63MM X 2.0MM FACE MILL   | 15000 | 7500/11250    | XY:0. Z:.1     |
| T126| SHORT_SP-DR-10           | 4000  | 400           | -              |
| T197| EM-2X38X8_FINISHER       | 15000 | 350/900       | -              |

## Issues
- [!] T1: no M01 between 'ROUGH FACE' and 'ROUGH SIDE *WEAR*'
- [!] T1: no M01 between 'ROUGH SIDE' and 'CHAMFER OUTSIDE'
- [!] T180: no M01 between 'FIN EXCESS' and 'FIN EXCESS'
```
