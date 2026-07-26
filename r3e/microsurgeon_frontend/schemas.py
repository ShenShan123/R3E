from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
import json


@dataclass
class DesignInput:
    design_name: str
    rtl_files: List[str]
    top_module: str
    family: str = "unknown"
    clock_port: Optional[str] = None
    candidate_clock_port: Optional[str] = None
    clock_period: float = 1.0

    @property
    def resolved_clock_port(self) -> Optional[str]:
        return self.clock_port or self.candidate_clock_port


@dataclass
class ToolResult:
    tool: str
    ok: bool
    returncode: int
    stdout: str = ""
    stderr: str = ""
    runtime_s: float = 0.0
    command: List[str] = field(default_factory=list)


@dataclass
class FrontendGateResult:
    design_name: str
    family: str
    top_module: str
    clock_port: Optional[str]
    clock_period: float

    rtl_files: List[str]

    file_exists_ok: bool = False
    iverilog_ok: bool = False
    yosys_ok: bool = False
    backend_contract_ok: bool = False

    failure_class: str = "UNKNOWN"
    failure_reason: str = ""

    yosys_stat: Dict[str, Any] = field(default_factory=dict)
    suspected_patterns: List[str] = field(default_factory=list)

    handoff_ready: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_designs_jsonl(path: str) -> List[DesignInput]:
    out = []
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)

            clock_port = obj.get("clock_port") or obj.get("candidate_clock_port")

            out.append(
                DesignInput(
                    design_name=obj.get("design_name") or obj.get("name") or obj.get("id"),
                    family=obj.get("family", "unknown"),
                    rtl_files=obj.get("rtl_files", []),
                    top_module=obj.get("top_module"),
                    clock_port=clock_port,
                    candidate_clock_port=obj.get("candidate_clock_port"),
                    clock_period=float(obj.get("clock_period", 1.0)),
                )
            )
    return out
