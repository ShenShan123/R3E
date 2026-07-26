#!/usr/bin/env python3
"""
scan_rtl_benchmarks.py - RTL Corpus Scanner and Manifest Generator

Recursively scans RTL directories, filters out testbenches/simulation files,
and generates a standardized benchmark manifest for downstream calibration.

Usage:
    python3 scan_rtl_benchmarks.py --root /path/to/rtl-data --output benchmark_manifest.jsonl
"""

import argparse
import json
import re
from pathlib import Path
from typing import Optional


# Exclusion patterns for testbench/simulation files (case-insensitive)
EXCLUDE_PATTERNS = [
    r"tb_",
    r"testbench",
    r"_tb",
    r"\bsim\b",
    r"simulation",
    r"wrapper",
    r"\bpkg\b",
    r"define",
    r"include",
    r"test_",
    r"_test\b",
]

# Known benchmark families (for auto-detection)
KNOWN_FAMILIES = {
    "iscas": ["c432", "c499", "c880", "c1355", "c1908", "c2670", "c3540", "c5315", "c6288", "c7552"],
    "boom": ["boom", "rocket", "tile"],
    "opentitan": ["aes", "hmac", "kmac", "otbn", "uart", "spi", "i2c"],
    "verilog_benchmarks": ["fir", "iir", "cordic", "fft", "dct"],
}


def should_exclude(file_path: Path) -> bool:
    """Check if file should be excluded based on exclusion patterns."""
    filename_lower = file_path.name.lower()
    for pattern in EXCLUDE_PATTERNS:
        if re.search(pattern, filename_lower):
            return True
    return False


def infer_family(design_name: str, file_path: Path) -> str:
    """Infer benchmark family from design name or path."""
    design_lower = design_name.lower()
    path_str = str(file_path).lower()

    # Check known families
    for family, keywords in KNOWN_FAMILIES.items():
        if any(kw in design_lower or kw in path_str for kw in keywords):
            return family

    # Check path components
    parts = file_path.parts
    for part in parts:
        part_lower = part.lower()
        if "iscas" in part_lower:
            return "iscas"
        elif "boom" in part_lower or "rocket" in part_lower:
            return "boom"
        elif "opentitan" in part_lower:
            return "opentitan"
        elif "benchmark" in part_lower:
            return "verilog_benchmarks"

    return "unknown"


def extract_top_module(rtl_file: Path) -> Optional[str]:
    """
    Extract top module name from RTL file.
    Looks for 'module <name>' declaration.
    """
    try:
        with open(rtl_file, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # Match: module <name> ( or module <name> #(
        matches = re.findall(r'^\s*module\s+(\w+)\s*[#(;]', content, re.MULTILINE)
        if matches:
            return matches[0]  # Return first module (usually top)
    except Exception as e:
        print(f"  [WARNING] Failed to parse {rtl_file}: {e}")

    return None


def infer_clock_port(rtl_file: Path) -> Optional[str]:
    """
    Infer clock port name from RTL file.
    Common patterns: clk, clock, CLK, sys_clk, core_clk
    """
    try:
        with open(rtl_file, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # Match input declarations with clock-like names
        clock_patterns = [
            r'input\s+(?:wire\s+)?(\w*clk\w*)',
            r'input\s+(?:wire\s+)?(clock)',
        ]

        for pattern in clock_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                return matches[0]
    except Exception:
        pass

    # Default fallback
    return "clk"


def scan_rtl_directory(root_dir: Path) -> list[dict]:
    """
    Recursively scan RTL directory and collect design metadata.

    Returns:
        List of design dictionaries with keys:
        - design_name: str
        - family: str
        - rtl_files: list[str]
        - top_module: str
        - candidate_clock_port: str
        - status: str (always "pending" initially)
    """
    designs = []

    # Find all .v and .sv files
    rtl_files = []
    for ext in ["*.v", "*.sv"]:
        rtl_files.extend(root_dir.rglob(ext))

    print(f"[SCAN] Found {len(rtl_files)} RTL files in {root_dir}")

    # Filter out testbenches/simulation files
    filtered_files = [f for f in rtl_files if not should_exclude(f)]
    print(f"[SCAN] After filtering: {len(filtered_files)} design files")

    # Group files by directory (assume each directory = one design)
    design_dirs = {}
    for rtl_file in filtered_files:
        parent_dir = rtl_file.parent
        if parent_dir not in design_dirs:
            design_dirs[parent_dir] = []
        design_dirs[parent_dir].append(rtl_file)

    print(f"[SCAN] Detected {len(design_dirs)} unique design directories")

    # Process each design directory
    for design_dir, files in sorted(design_dirs.items()):
        # Infer design name from directory name
        design_name = design_dir.name

        # If directory name is generic (src, rtl, verilog), use parent
        if design_name.lower() in ["src", "rtl", "verilog", "hdl", "design"]:
            design_name = design_dir.parent.name

        # Infer family
        family = infer_family(design_name, design_dir)

        # Extract top module from first file
        top_module = None
        candidate_clock_port = "clk"

        for rtl_file in files:
            top_module = extract_top_module(rtl_file)
            if top_module:
                candidate_clock_port = infer_clock_port(rtl_file)
                break

        # If no module found, use design_name as fallback
        if not top_module:
            top_module = design_name

        # Create design entry
        design_entry = {
            "design_name": design_name,
            "family": family,
            "rtl_files": [str(f.resolve()) for f in sorted(files)],
            "top_module": top_module,
            "candidate_clock_port": candidate_clock_port,
            "status": "pending"
        }

        designs.append(design_entry)
        print(f"  [DESIGN] {design_name} (family={family}, top={top_module}, files={len(files)})")

    return designs


def write_manifest(designs: list[dict], output_path: Path):
    """Write designs to JSONL manifest file."""
    with open(output_path, 'w', encoding='utf-8') as f:
        for design in designs:
            f.write(json.dumps(design, ensure_ascii=False) + '\n')

    print(f"\n[OUTPUT] Wrote {len(designs)} designs to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Scan RTL directories and generate benchmark manifest"
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Root directory to scan for RTL files"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark_manifest.jsonl"),
        help="Output manifest file (default: benchmark_manifest.jsonl)"
    )

    args = parser.parse_args()

    if not args.root.exists():
        print(f"[ERROR] Root directory does not exist: {args.root}")
        return 1

    print(f"[SCAN] Starting RTL scan from: {args.root}")
    print(f"[SCAN] Exclusion patterns: {', '.join(EXCLUDE_PATTERNS)}")
    print()

    # Scan directory
    designs = scan_rtl_directory(args.root)

    # Write manifest
    write_manifest(designs, args.output)

    # Summary statistics
    families = {}
    for design in designs:
        family = design["family"]
        families[family] = families.get(family, 0) + 1

    print("\n[SUMMARY] Design families:")
    for family, count in sorted(families.items()):
        print(f"  - {family}: {count} designs")

    return 0


if __name__ == "__main__":
    exit(main())
