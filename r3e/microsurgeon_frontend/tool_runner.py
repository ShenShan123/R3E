import subprocess
import time
from typing import List, Optional
from .schemas import ToolResult


def run_cmd(cmd: List[str], timeout_s: Optional[int] = None) -> ToolResult:
    t0 = time.time()

    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
        )
        return ToolResult(
            tool=cmd[0],
            ok=(p.returncode == 0),
            returncode=p.returncode,
            stdout=p.stdout,
            stderr=p.stderr,
            runtime_s=time.time() - t0,
            command=cmd,
        )

    except subprocess.TimeoutExpired as e:
        return ToolResult(
            tool=cmd[0],
            ok=False,
            returncode=124,
            stdout=e.stdout or "",
            stderr=(e.stderr or "") + f"\n[TIMEOUT] command exceeded {timeout_s}s",
            runtime_s=time.time() - t0,
            command=cmd,
        )

    except FileNotFoundError as e:
        return ToolResult(
            tool=cmd[0],
            ok=False,
            returncode=127,
            stdout="",
            stderr=str(e),
            runtime_s=time.time() - t0,
            command=cmd,
        )
