import re
from pathlib import Path
from typing import Dict, List, Iterable, Tuple

INCLUDE_RE = re.compile(r'^\s*`include\s+"([^"]+)"', re.M)

GENERIC_TOKENS = {
    "rtl", "src", "source", "sources", "verilog", "bench", "benchmark",
    "external", "benchmarks", "nangate45", "expand", "success",
    "top", "core", "master", "module", "design",
}


def tokenize(s: str) -> List[str]:
    toks = re.split(r"[^A-Za-z0-9]+", s.lower())
    return [t for t in toks if t and t not in GENERIC_TOKENS and len(t) >= 2]


def extract_include_names_from_file(path: Path) -> List[str]:
    if not path.exists():
        return []
    txt = path.read_text(errors="ignore")
    out = []
    seen = set()
    for m in INCLUDE_RE.finditer(txt):
        name = m.group(1)
        if name not in seen:
            out.append(name)
            seen.add(name)
    return out


def build_include_index(search_roots: Iterable[Path], max_hits_per_name: int = 200) -> Dict[str, List[str]]:
    idx: Dict[str, List[str]] = {}

    for root in search_roots:
        root = root.expanduser().resolve()
        if not root.exists():
            continue

        for pat in ["*.v", "*.vh", "*.sv", "*.svh"]:
            try:
                for p in root.rglob(pat):
                    idx.setdefault(p.name, [])
                    if len(idx[p.name]) < max_hits_per_name:
                        idx[p.name].append(str(p.resolve()))
            except Exception:
                pass

    return idx


def score_candidate(
    include_file: Path,
    rtl_file_dirs: List[Path],
    design_roots: List[Path],
    preferred_tokens: List[str],
) -> int:
    p = include_file.resolve()
    parent = p.parent.resolve()
    path_s = str(p).lower()
    path_tokens = set(tokenize(path_s))

    score = 0

    # Exact RTL directory match is strongest.
    for d in rtl_file_dirs:
        d = d.resolve()
        if parent == d:
            score += 10000

    # Under nearby design roots is strong, but weaker than exact RTL dir.
    for r in design_roots:
        r = r.resolve()
        try:
            parent.relative_to(r)
            # Avoid giving too much score to very broad roots like /path/to/rtl-data
            depth = len(parent.relative_to(r).parts)
            score += max(1000 - 50 * depth, 100)
        except Exception:
            pass

    # Token overlap with design name and path.
    for t in preferred_tokens:
        if t in path_tokens or t in path_s:
            score += 300

    # Prefer conventional RTL/include directories.
    if "/rtl/" in path_s or path_s.endswith("/rtl/" + p.name):
        score += 80
    if "/include/" in path_s or "/inc/" in path_s:
        score += 80

    # Penalize common wrong matches for generic includes.
    for bad in ["usb_phy", "uart16550", "ethernet", "pci", "vga_lcd", "systemcaes"]:
        if bad in path_s and bad not in preferred_tokens:
            score -= 500

    return score


def resolve_include_dirs(
    include_names: Iterable[str],
    rtl_file_dirs: Iterable[Path],
    design_roots: Iterable[Path],
    global_roots: Iterable[Path],
    design_name: str = "",
) -> Tuple[List[str], Dict[str, List[str]], List[str]]:
    """
    Returns:
      include_dirs, include_resolution_map, missing_include_names
    """
    include_names = list(include_names)
    rtl_file_dirs = [Path(x).expanduser().resolve() for x in rtl_file_dirs]
    design_roots = [Path(x).expanduser().resolve() for x in design_roots]
    global_roots = [Path(x).expanduser().resolve() for x in global_roots]

    preferred_tokens = tokenize(design_name)
    include_dirs: List[str] = []
    seen_dirs = set()
    resolution: Dict[str, List[str]] = {}
    missing: List[str] = []

    def add_dir(d: Path):
        d = d.expanduser().resolve()
        if d.exists() and d.is_dir():
            s = str(d)
            if s not in seen_dirs:
                include_dirs.append(s)
                seen_dirs.add(s)

    # Always include RTL file dirs first.
    for d in rtl_file_dirs:
        add_dir(d)

    index = build_include_index(global_roots)

    for inc in include_names:
        inc_name = Path(inc).name
        candidates: List[Path] = []

        # 1. Direct lookup in RTL file dirs.
        for d in rtl_file_dirs:
            p = d / inc
            if p.exists():
                candidates.append(p.resolve())

        # 2. Direct and recursive lookup in design roots.
        for r in design_roots:
            direct = r / inc
            if direct.exists():
                candidates.append(direct.resolve())

            try:
                for hit in r.rglob(inc_name):
                    if hit.is_file():
                        candidates.append(hit.resolve())
            except Exception:
                pass

        # 3. Global index fallback.
        for hit in index.get(inc_name, []):
            candidates.append(Path(hit).resolve())

        # Dedup candidates.
        dedup = []
        seen = set()
        for c in candidates:
            s = str(c)
            if s not in seen:
                dedup.append(c)
                seen.add(s)

        if not dedup:
            missing.append(inc)
            resolution[inc] = []
            continue

        ranked = sorted(
            dedup,
            key=lambda p: score_candidate(p, rtl_file_dirs, design_roots, preferred_tokens),
            reverse=True,
        )

        # Only add the best directory for each include to avoid wrong timescale.v collisions.
        best = ranked[0]
        add_dir(best.parent)
        resolution[inc] = [str(x) for x in ranked[:10]]

    return include_dirs, resolution, missing
