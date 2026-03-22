#!/usr/bin/env python3
"""G-code parser — extracts tools, feeds/speeds, cycle time, and flags issues."""

import sys
import re
import json
import math
from pathlib import Path

def parse_gcode(filepath):
    lines = Path(filepath).read_text(errors='replace').splitlines()

    result = {
        "file": str(filepath),
        "program": {},
        "tools": {},
        "work_offsets": [],
        "cycle_time": {"per_tool": {}, "total_seconds": 0},
        "issues": [],
        "setup_notes": []
    }

    # State tracking
    current_tool = None
    spindle_on = False
    coolant_on = False
    tool_length_comp = False
    has_safety_line = False
    has_end = False
    units = "metric"  # default
    mode = "absolute"
    x, y, z = 0.0, 0.0, 0.0
    feed_rate = 0.0
    rapid_rate = 25400.0  # mm/min for Haas
    total_time = 0.0
    tool_time = {}
    tool_feeds = {}
    tool_speeds = {}
    seen_offsets = set()
    header_tools = {}
    inline_tools = {}
    # Track tool change sequence for same-tool / missing M01 detection
    tool_change_sequence = []  # list of (tool_num, line_num)
    had_m01_since_last_tc = False
    # Track operations within same tool for missing-M01-between-ops detection
    current_tool_ops = []  # list of (op_name, line_num)
    had_m01_since_last_op = True  # true at start (no prior op)
    last_was_retract = False  # track Z retract before op comment

    # Parse header comments for tool info
    header_pattern = re.compile(
        r'\(\s*T(\d+)\s*\|\s*([^|]+?)\s*\|\s*H(\d+)\s*'
        r'(?:\|\s*(?:D(\d+)\s*\|?\s*)?'
        r'(?:XY STOCK TO LEAVE\s*-\s*([\d.]+))?\s*\|?\s*'
        r'(?:Z STOCK TO LEAVE\s*-\s*([\d.]+))?\s*)?'
    )

    # Program info
    for i, line in enumerate(lines[:5]):
        m = re.match(r'O(\d+)\((.+?)\)', line)
        if m:
            result["program"]["number"] = m.group(1)
            result["program"]["name"] = m.group(2)
        m = re.search(r'DATE=(.+?)\s+TIME=(.+?)[\s)]', line)
        if m:
            result["program"]["date"] = m.group(1).strip()
            result["program"]["time"] = m.group(2).strip()
        m = re.search(r'MATERIAL\s*-\s*(.+?)\)', line)
        if m:
            result["program"]["material"] = m.group(1).strip()

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Header tool comments
        hm = header_pattern.match(stripped)
        if hm:
            tnum = int(hm.group(1))
            header_tools[tnum] = {
                "number": tnum,
                "description": hm.group(2).strip(),
                "h_offset": int(hm.group(3)),
                "xy_stock_leave": hm.group(5),
                "z_stock_leave": hm.group(6),
            }
            continue

        # Setup notes from comments
        for pattern in [r'\(\s*(USE .+)\)', r'\(\s*(TIGHTEN .+)\)', r'\(\s*(ROTATE .+)\)']:
            nm = re.search(pattern, stripped)
            if nm:
                result["setup_notes"].append(nm.group(1).strip())

        # Detect operation name comments within a tool section (no M01 between ops)
        if stripped.startswith('(') and stripped.endswith(')') and current_tool is not None:
            comment_text = stripped[1:-1].strip()
            # Skip header tool comments, datum comments, setup notes
            is_header = '|' in comment_text
            is_datum = bool(re.match(r'^[XYZ]?\s*_?DATUM', comment_text, re.I))
            is_setup = bool(re.match(r'^(USE |TIGHTEN |ROTATE |SPECIFICATION)', comment_text, re.I))
            is_wcs = bool(re.match(r'^G5[4-9]', comment_text))
            is_empty = len(comment_text.strip()) < 3
            # Operation name: looks like "63MM X 2. - OP1 ROUGH SIDE *WEAR* G55"
            # or "ROUGH", "FINISH CONTOUR", "DRILL 6.8 THRU" etc.
            if not is_header and not is_datum and not is_setup and not is_wcs and not is_empty:
                # This looks like a toolpath/operation name comment
                if len(current_tool_ops) > 0 and not had_m01_since_last_op:
                    prev_op = current_tool_ops[-1][0]
                    result["issues"].append({
                        "line": i + 1,
                        "severity": "info",
                        "message": f"T{current_tool}: no M01 between '{prev_op}' and '{comment_text}' — operator can't stop between operations"
                    })
                current_tool_ops.append((comment_text, i + 1))
                had_m01_since_last_op = False

        # Skip pure comments
        if stripped.startswith('(') and stripped.endswith(')'):
            continue
        if stripped in ('%', ''):
            continue

        # Units
        if 'G21' in stripped:
            units = "metric"
            result["program"]["units"] = "metric"
        elif 'G20' in stripped:
            units = "inch"
            result["program"]["units"] = "inch"
            rapid_rate = 1000.0  # IPM

        # Safety line detection
        if re.search(r'G(28|53)', stripped):
            has_safety_line = True

        # Absolute/incremental
        if 'G90' in stripped:
            mode = "absolute"
        elif 'G91' in stripped:
            mode = "incremental"

        # Work offsets
        for wcs in re.findall(r'G5([4-9])', stripped):
            offset = f"G5{wcs}"
            if offset not in seen_offsets:
                seen_offsets.add(offset)
                result["work_offsets"].append(offset)
        for wcs in re.findall(r'G54\.1\s*P(\d+)', stripped):
            offset = f"G54.1 P{wcs}"
            if offset not in seen_offsets:
                seen_offsets.add(offset)
                result["work_offsets"].append(offset)

        # M01 optional stop tracking
        if re.search(r'\bM0*1\b', stripped) and not re.search(r'\bM0*10\b', stripped):
            had_m01_since_last_tc = True
            had_m01_since_last_op = True

        # Tool change
        tm = re.search(r'\bT(\d+)\s*M0*6\b', stripped)
        if tm:
            tnum = int(tm.group(1))
            # Detect same tool called consecutively without M01
            if current_tool is not None and current_tool == tnum and not had_m01_since_last_tc:
                result["issues"].append({
                    "line": i + 1,
                    "severity": "info",
                    "message": f"T{tnum} called again without M01 between uses — consecutive same-tool operations could be merged"
                })
            # Detect consecutive different tools without M01 (no operator stop)
            if current_tool is not None and current_tool != tnum and not had_m01_since_last_tc:
                # Track but don't warn — this is normal in many programs
                pass
            tool_change_sequence.append((tnum, i + 1))
            had_m01_since_last_tc = False
            if current_tool and current_tool in tool_time:
                pass  # time already accumulated
            current_tool = tnum
            spindle_on = False
            tool_length_comp = False
            total_time += 3.0  # tool change time
            current_tool_ops = []  # reset ops for new tool
            had_m01_since_last_op = True  # first op after TC doesn't need M01
            if tnum not in inline_tools:
                inline_tools[tnum] = {"number": tnum, "operations": []}
            if tnum not in tool_feeds:
                tool_feeds[tnum] = set()
            if tnum not in tool_speeds:
                tool_speeds[tnum] = set()
            if tnum not in tool_time:
                tool_time[tnum] = 0.0

        # Extract M-codes properly (word boundary to avoid M30 matching M3)
        mcodes = [int(m.group(1)) for m in re.finditer(r'\bM0*(\d+)', stripped)]
        for mc in mcodes:
            if mc == 3 or mc == 4:
                spindle_on = True
            elif mc == 5:
                spindle_on = False
            elif mc == 8 or mc == 7:
                coolant_on = True
            elif mc == 9:
                coolant_on = False

        # Tool length comp
        if 'G43' in stripped:
            tool_length_comp = True
        elif 'G49' in stripped:
            tool_length_comp = False

        # Spindle speed
        sm = re.search(r'S(\d+)', stripped)
        if sm and current_tool:
            tool_speeds.setdefault(current_tool, set()).add(int(sm.group(1)))

        # Feed rate
        fm = re.search(r'F([\d.]+)', stripped)
        if fm:
            feed_rate = float(fm.group(1))
            if current_tool:
                tool_feeds.setdefault(current_tool, set()).add(feed_rate)

        # Motion - calculate distance and time
        new_x, new_y, new_z = x, y, z
        xm = re.search(r'X(-?[\d.]+)', stripped)
        ym = re.search(r'Y(-?[\d.]+)', stripped)
        zm = re.search(r'Z(-?[\d.]+)', stripped)

        if xm: new_x = float(xm.group(1))
        if ym: new_y = float(ym.group(1))
        if zm: new_z = float(zm.group(1))

        if xm or ym or zm:
            dist = math.sqrt((new_x-x)**2 + (new_y-y)**2 + (new_z-z)**2)

            # Use word-boundary matching to avoid G28 matching G2, etc.
            is_rapid = bool(re.search(r'\bG0(?:\s|$|[^0-9])', stripped))
            is_feed = bool(re.search(r'\bG0?[1-3]\b', stripped))
            is_home = bool(re.search(r'\bG28\b|\bG30\b|\bG53\b', stripped))

            if dist > 0:
                if is_home or is_rapid:
                    move_time = (dist / rapid_rate) * 60
                elif is_feed and feed_rate > 0:
                    move_time = (dist / feed_rate) * 60
                else:
                    move_time = 0

                total_time += move_time
                if current_tool:
                    tool_time[current_tool] = tool_time.get(current_tool, 0) + move_time

            # Skip issue checks for home/retract moves
            if not is_home:
                # Issue: rapid into negative Z
                if is_rapid and new_z < 0 and z > 0 and current_tool:
                    result["issues"].append({
                        "line": i + 1,
                        "severity": "warning",
                        "message": f"Rapid move (G0) to Z{new_z} — potential crash risk"
                    })

                # Issue: cutting without tool length comp
                if is_feed and new_z < z and not tool_length_comp and current_tool:
                    result["issues"].append({
                        "line": i + 1,
                        "severity": "warning",
                        "message": "Cutting in Z without tool length compensation (G43)"
                    })

                # Issue: cutting without spindle
                if is_feed and not spindle_on and current_tool and dist > 0.1:
                    result["issues"].append({
                        "line": i + 1,
                        "severity": "error",
                        "message": "Feed move without spindle running"
                    })

            x, y, z = new_x, new_y, new_z

        # End of program
        if 30 in mcodes or 99 in mcodes or 2 in mcodes:
            has_end = True

        # Operation comments
        op_match = re.match(r'\(\s*(.+?)\s*\)', stripped)
        if op_match and current_tool:
            op_text = op_match.group(1).strip()
            if op_text and not op_text.startswith('T') and len(op_text) > 2:
                inline_tools.setdefault(current_tool, {"number": current_tool, "operations": []})
                inline_tools[current_tool]["operations"].append(op_text)

    # Merge header + inline tool data
    all_tool_nums = sorted(set(list(header_tools.keys()) + list(inline_tools.keys())))
    for tnum in all_tool_nums:
        tool_data = {
            "number": tnum,
            "description": header_tools.get(tnum, {}).get("description", "Unknown"),
            "h_offset": header_tools.get(tnum, {}).get("h_offset", tnum),
            "xy_stock_leave": header_tools.get(tnum, {}).get("xy_stock_leave"),
            "z_stock_leave": header_tools.get(tnum, {}).get("z_stock_leave"),
            "speeds_rpm": sorted(tool_speeds.get(tnum, [])),
            "feeds": sorted(tool_feeds.get(tnum, [])),
            "operations": inline_tools.get(tnum, {}).get("operations", []),
            "estimated_time_seconds": round(tool_time.get(tnum, 0), 1)
        }
        result["tools"][str(tnum)] = tool_data

    # Cycle time
    result["cycle_time"]["total_seconds"] = round(total_time, 1)
    result["cycle_time"]["total_formatted"] = f"{int(total_time//60)}m {int(total_time%60)}s"
    for tnum, t in tool_time.items():
        result["cycle_time"]["per_tool"][str(tnum)] = {
            "seconds": round(t, 1),
            "formatted": f"{int(t//60)}m {int(t%60)}s"
        }

    # Global issues
    if not has_safety_line:
        result["issues"].insert(0, {
            "line": 0, "severity": "warning",
            "message": "No safety line detected (G28 or G53)"
        })

    if not has_end:
        result["issues"].append({
            "line": len(lines), "severity": "warning",
            "message": "No program end (M30/M99) detected"
        })

    # Check for missing coolant on first cutting tool
    # (simplified — already tracked per-tool above)

    result["line_count"] = len(lines)
    result["tool_count"] = len(all_tool_nums)
    result["tool_change_sequence"] = [{"tool": t, "line": l} for t, l in tool_change_sequence]

    return result


def format_report(data):
    """Format parsed data as a readable markdown report."""
    lines = []
    p = data["program"]

    lines.append(f"## Program: {p.get('name', 'Unknown')}")
    if p.get('number'): lines.append(f"- **Program #:** O{p['number']}")
    if p.get('date'): lines.append(f"- **Date:** {p['date']}")
    if p.get('material'): lines.append(f"- **Material:** {p['material']}")
    lines.append(f"- **Units:** {p.get('units', 'metric')}")
    lines.append(f"- **Lines:** {data['line_count']}")
    if data["work_offsets"]:
        lines.append(f"- **Work Offsets:** {', '.join(data['work_offsets'])}")
    lines.append("")

    # Tool table
    lines.append("## Tool List")
    lines.append("")
    lines.append("| T# | Description | H# | RPM | Feeds | Stock Leave (XY/Z) | Est. Time |")
    lines.append("|-----|------------|-----|-----|-------|-------------------|-----------|")
    for tnum in sorted(data["tools"].keys(), key=int):
        t = data["tools"][tnum]
        rpms = "/".join(str(s) for s in t["speeds_rpm"]) if t["speeds_rpm"] else "-"
        feeds = "/".join(f"{f:.0f}" for f in t["feeds"]) if t["feeds"] else "-"
        stock = ""
        if t.get("xy_stock_leave"): stock += f"XY:{t['xy_stock_leave']}"
        if t.get("z_stock_leave"): stock += f" Z:{t['z_stock_leave']}"
        stock = stock.strip() or "-"
        time_str = f"{t['estimated_time_seconds']:.0f}s"
        lines.append(f"| T{t['number']} | {t['description']} | H{t['h_offset']} | {rpms} | {feeds} | {stock} | {time_str} |")
    lines.append("")

    # Cycle time
    ct = data["cycle_time"]
    lines.append(f"## Cycle Time Estimate: {ct['total_formatted']}")
    lines.append("*(Based on programmed feeds + 25.4m/min rapids + 3s/tool change. Actual will vary.)*")
    lines.append("")

    # Issues
    if data["issues"]:
        lines.append("## Issues")
        lines.append("")
        for issue in data["issues"]:
            icon = "!!" if issue["severity"] == "error" else "!"
            line_ref = f" (line {issue['line']})" if issue["line"] > 0 else ""
            lines.append(f"- **[{icon}]** {issue['message']}{line_ref}")
        lines.append("")

    # Tool change sequence
    if data.get("tool_change_sequence"):
        seq = data["tool_change_sequence"]
        lines.append("## Tool Change Sequence")
        lines.append(f"T{' → T'.join(str(s['tool']) for s in seq)}")
        lines.append("")

    # Setup notes
    if data["setup_notes"]:
        lines.append("## Setup Notes")
        for note in data["setup_notes"]:
            lines.append(f"- {note}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: parse_gcode.py <file.nc> [--json]")
        sys.exit(1)

    filepath = sys.argv[1]
    output_json = "--json" in sys.argv

    data = parse_gcode(filepath)

    if output_json:
        print(json.dumps(data, indent=2))
    else:
        print(format_report(data))
