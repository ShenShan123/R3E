"""csv table testbench → verilog tb（differential 用，unseen-family/fpga-debugging）.

解析 golden 端口(含参数位宽), csv input 列每周期驱动 DUT, dump output 列到 txt
(oracle_gate _compare 格式: time,out1,out2,...). clk 由 tb 生成. 让 oracle_gate 的
differential(golden vs candidate 同 tb)能用于 csv-tb 的真外部设计.
"""
from __future__ import annotations

import ast
import operator
import re
from pathlib import Path


_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: lambda a, b: int(a / b), ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.LShift: operator.lshift, ast.RShift: operator.rshift,
    ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor,
}
_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Invert: operator.invert}
_VERILOG_INT = re.compile(r"(?i)(\d+)'s?([bodh])([0-9a-f_]+)")


def _eval_int_expr(expression: str, names: dict[str, int]) -> int:
    """Evaluate the small integer-expression subset used by RTL widths."""
    def replace_literal(match: re.Match) -> str:
        base = {"b": 2, "o": 8, "d": 10, "h": 16}[match.group(2).lower()]
        return str(int(match.group(3).replace("_", ""), base))

    normalized = _VERILOG_INT.sub(replace_literal, expression.replace("$clog2", "clog2"))
    tree = ast.parse(normalized, mode="eval")

    def visit(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.Name) and node.id in names:
            return int(names[node.id])
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            return int(_BINOPS[type(node.op)](visit(node.left), visit(node.right)))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARYOPS:
            return int(_UNARYOPS[type(node.op)](visit(node.operand)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "clog2":
            if len(node.args) != 1 or node.keywords:
                raise ValueError("$clog2 expects one argument")
            value = visit(node.args[0])
            if value <= 0:
                raise ValueError("$clog2 argument must be positive")
            return (value - 1).bit_length()
        raise ValueError(f"unsupported RTL integer expression: {expression!r}")

    result = visit(tree)
    if abs(result) > 2**31:
        raise ValueError("RTL integer expression is outside the supported range")
    return result


def parse_module(rtl_text: str):
    params = {}
    for m in re.finditer(
            r'(?:parameter|localparam)\s+(?:integer\s+|signed\s+)?(\w+)\s*=\s*([^,;)\n]+)',
            rtl_text):
        try:  # 按出现顺序解析，以支持 localparam 对前序 parameter 的依赖
            params[m.group(1)] = _eval_int_expr(m.group(2).strip(), params)
        except Exception:  # noqa: BLE001
            pass
    mod = re.search(r'\bmodule\s+(\w+)', rtl_text).group(1)
    ports = []
    for m in re.finditer(
            r'\b(input|output)\b\s*(?:logic|wire|reg)?\s*(?:signed\s+)?(\[[^\]]+\])?\s*(\w+)',
            rtl_text):
        dir_, wexpr, name = m.groups()
        if name in ("logic", "wire", "reg", "signed"):
            continue
        width = 1
        if wexpr:
            mm = re.match(r'\[\s*(.+?)\s*:\s*(.+?)\s*\]', wexpr)
            if not mm:
                raise ValueError(f"invalid packed width expression: {wexpr}")
            hi = _eval_int_expr(mm.group(1), params)
            lo = _eval_int_expr(mm.group(2), params)
            width = abs(hi - lo) + 1
        ports.append((dir_, name, width))
    return mod, ports, params


def gen_tb(golden_rtl, csv_path, out_txt="output_unseen.txt", clk="clk") -> str:
    text = Path(golden_rtl).read_text(errors="ignore")
    mod, ports, _ = parse_module(text)
    rows = [l for l in Path(csv_path).read_text().splitlines() if l.strip()]
    header = [h.strip() for h in rows[0].split(",")]
    pmap = {n: (d, w) for d, n, w in ports}
    inputs = [h for h in header if pmap.get(h, ("", 0))[0] == "input"]
    outputs = [h for h in header if pmap.get(h, ("", 0))[0] == "output"]
    # clk 自动识别(各设计名可能不同, 且 clk 不在 csv 列由 tb 生成)
    if clk is None or clk not in pmap:
        clk = next((n for d, n, _ in ports
                    if d == "input" and ("clk" in n.lower() or "clock" in n.lower())), None)
    has_clk = clk is not None and clk in pmap
    inputs = [h for h in inputs if h != clk]

    t = ["module tb_unseen;"]
    for d, n, w in ports:
        kind = "reg" if d == "input" else "wire"
        rng = f"[{w - 1}:0] " if w > 1 else ""
        t.append(f"  {kind} {rng}{n};")
    conn = ", ".join(f".{n}({n})" for _, n, _ in ports)
    t.append(f"  {mod} DUT({conn});")
    if has_clk:
        t.append(f"  always #5 {clk} = ~{clk};")
    t.append("  integer f; initial begin")
    if has_clk:
        t.append(f"    {clk}=0;")
    t.append(f'    f=$fopen("{out_txt}");')
    t.append(f'    $fdisplay(f,"time,{",".join(outputs)}");')
    for row in rows[1:]:
        vmap = dict(zip(header, [v.strip() for v in row.split(",")]))
        for inp in inputs:
            t.append(f"    {inp}={pmap[inp][1]}'d{vmap[inp]};")
        t.append(f"    @(posedge {clk}); #1;" if has_clk else "    #10;")
        fmt = ",".join("%b" for _ in outputs)
        t.append(f'    $fdisplay(f,"%0d,{fmt}",$time,{",".join(outputs)});')
    t += ["    $fclose(f); $finish;", "  end", "endmodule"]
    return "\n".join(t)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out-tb", required=True)
    ap.add_argument("--out-txt", default="output_unseen.txt")
    args = ap.parse_args()
    Path(args.out_tb).write_text(gen_tb(args.golden, args.csv, args.out_txt))
    print(f"tb → {args.out_tb}")
