---
name: gcode-analyze
description: Analyze CNC G-code files for machining issues, feeds/speeds optimization, cycle time estimates, and program comparison. Complements universal-setup-sheet (which handles setup sheet generation) by focusing on analysis, auditing, and optimization. Use when user says "analyze this G-code", "check this NC file", "review this program", "audit feeds and speeds", "estimate cycle time", "compare these programs", or provides a .nc/.tap/.gcode file for review. Also handles material-specific feed/speed recommendations and canned cycle analysis.
---

# G-code Analyzer

Analyze CNC G-code files (Fanuc/Haas/Mazak/hyperMILL dialects) for issues, optimization opportunities, and machining data. This skill is for **analysis and auditing** — for setup sheet generation, use the universal-setup-sheet project instead.

## Trigger

- User provides or references a `.nc`, `.tap`, `.gcode`, or `.ngc` file
- User asks to analyze, review, check, or audit a CNC program
- User asks about feeds/speeds optimization or cycle time
- User wants to compare two program revisions
- User asks "is this safe to run?" or "anything wrong with this program?"

## Process

### 1. Run the parser script

```bash
python3 ~/.claude/skills/gcode-analyze/scripts/parse_gcode.py "<file_path>"
```

For JSON output (piping to other tools):
```bash
python3 ~/.claude/skills/gcode-analyze/scripts/parse_gcode.py "<file_path>" --json
```

### 2. Read the file directly for context

The parser catches structural data. Also read the file (or key sections) to catch:
- Operator notes in comments that need human interpretation
- Unusual patterns the parser might miss
- Context around flagged issues

### 3. Present results as analysis report

#### Program Overview
- Program number, name, date, material
- Work coordinate systems, units
- Line count, tool count

#### Tool Table
From parser output — tool number, description, H offset, RPM, feeds, stock-to-leave, estimated time per tool.

#### Feeds & Speeds Audit
Per tool, calculate and flag:
- **SFM/SMM** from RPM + tool diameter (extracted from description)
  - Aluminum: target 800-1200 SFM carbide, 200-400 SFM HSS
  - Steel: target 300-600 SFM carbide, 80-150 SFM HSS
  - Stainless: target 200-400 SFM carbide
- **Chip load** from feed / (RPM × flute count) — extract flutes from description if possible
  - 2-flute endmill in aluminum: 0.05-0.15mm/tooth
  - 4-flute in steel: 0.03-0.08mm/tooth
- **Feed per rev** for drilling: F / RPM
  - Drills: 0.01-0.05mm/rev per mm of diameter
- Flag values outside expected ranges with "low/high" warnings

#### Cycle Time Estimate
From parser — total and per-tool. Always caveat: "Estimate based on programmed feed rates. Actual cycle time depends on machine accel/decel, look-ahead, and dwell."

#### Issues & Warnings
Parser auto-detects:
- Missing safety line (no G28/G53)
- Feed moves without spindle (M3/M4)
- Rapid into negative Z (crash risk)
- Cutting without tool length comp (G43)
- Missing M30/M99 at end
- Feed move without spindle running

Claude should also check for:
- **Canned cycle issues**: G83 (peck drill) with too-large peck increment, tapping without rigid tap (G84 without G95)
- **Cutter comp issues**: G41/G42 activated during a cut (should be on approach move)
- **Coolant strategy**: TSC (M51) vs flood (M8) vs mist (M7) — flag if using flood for deep holes where TSC would be better
- **Retract height**: R-plane in canned cycles — flag if R is very high (wasted time) or very low (chip packing risk)
- **Subprogram calls**: M98/M99 — flag if subprogram file not referenced
- **Work offset jumps**: G55→G57 skipping G56 (intentional? or error?)

#### Optimization Suggestions
Based on analysis:
- "Tool X at 800 SFM in aluminum — could push to 1000+ SFM with carbide"
- "Peck drill with 0.5mm pecks — could increase to 1-2mm in aluminum to save cycle time"
- "Face mill making 12 passes — consider wider stepover if surface finish allows"
- Only suggest if confident — don't recommend unsafe speeds

### 4. Comparison mode

If two files provided:
```bash
python3 ~/.claude/skills/gcode-analyze/scripts/parse_gcode.py "<file1>" --json > /tmp/a.json
python3 ~/.claude/skills/gcode-analyze/scripts/parse_gcode.py "<file2>" --json > /tmp/b.json
```

Then diff:
- Tools added/removed/changed
- Feed/speed changes per tool (highlight >10% changes)
- New or removed operations
- WCS changes
- Cycle time delta

### 5. Material-specific mode

If user specifies material or it's in the header:
- Apply material-specific feed/speed ranges to all audit checks
- Common materials: 6061/7075/2024 aluminum, 4140/4340 steel, 304/316 stainless, titanium, Inconel

## Dialect Recognition

The parser handles Mastercam (pipe headers), hyperMILL (OPERATION comments, TOOL LIST blocks), Mazak (G43.4/RTCP), and generic Fanuc. It auto-detects dialect from header patterns.

## Relationship to universal-setup-sheet

- **USS** = setup sheet generation (tool lists, operations, fixture data for machinists)
- **This skill** = program analysis (issues, optimization, cycle time, comparison)
- They parse the same files but for different purposes
- If user wants a setup sheet, point them to USS: `~/universal-setup-sheet/samples/Universal Setup Sheets.html`
