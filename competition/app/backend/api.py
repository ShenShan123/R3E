"""Small JSON HTTP API for Repair Studio and verification traces.

Run with ``python -m competition.app.backend.api`` from the repository root.
The server is intentionally dependency-light so the competition demo does not
need a second web framework or a JavaScript build chain.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from .health import health
from ...services.artifact_service import ArtifactService
from ...services.benchmark_service import BenchmarkService
from ...services.case_service import CaseCatalog, CaseError
from ...services.diagnosis_service import DiagnosisService
from ...services.evolution_service import EvolutionService
from ...services.memory_service import MemoryService
from ...services.repair_service import RepairService
from ...services.verification_service import VerificationService


ROOT = Path(__file__).resolve().parents[3]
FRONTEND = ROOT / "competition" / "app" / "frontend"
DEFAULT_OUTPUT = Path(os.getenv("R3E_AIC_OUTPUT_ROOT", "/tmp/r3e-aic-runs"))


class CompetitionContext:
    def __init__(self, root: Path = ROOT, output_root: Path = DEFAULT_OUTPUT):
        self.root = root.resolve()
        self.output_root = output_root.resolve()
        self.catalog = CaseCatalog(self.root)
        self.diagnosis = DiagnosisService(self.root, self.output_root)
        self.repair = RepairService(self.root, self.output_root)
        self.verification = VerificationService(self.root, self.output_root)
        self.evolution = EvolutionService(self.root)
        self.memory = MemoryService(self.root)
        self.benchmark = BenchmarkService(self.root)

    def run_case(self, payload: dict[str, Any]) -> dict[str, Any]:
        case_id = str(payload.get("case_id") or "")
        mode = str(payload.get("mode") or "demo")
        proposals = self.repair.generate(case_id, mode=mode)
        verified = []
        for candidate in proposals["candidates"]:
            result = self.verification.verify(
                case_id,
                candidate["replacement_rtl"],
                run_id=f"{mode}-{candidate['id']}",
            )
            verified.append({"candidate": candidate, "verification": result})
        return {"proposals": proposals, "verified_candidates": verified}


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    context: CompetitionContext

    def _send_json(self, status: int, payload: Any) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _payload(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("request body must be JSON") from exc
        if not isinstance(raw, dict):
            raise ValueError("request body must be an object")
        return raw

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/api/health":
                return self._send_json(200, health(self.context.root))
            if path == "/api/cases":
                return self._send_json(200, {"cases": self.context.catalog.summaries()})
            if path == "/api/evolution":
                return self._send_json(200, self.context.evolution.timeline())
            if path == "/api/memory":
                return self._send_json(200, self.context.memory.list_memories())
            if path == "/api/benchmarks":
                return self._send_json(200, self.context.benchmark.dashboard())
            if path == "/" or path == "/index.html":
                return self._send_file(FRONTEND / "index.html")
            if path.startswith("/static/"):
                relative = Path(path.removeprefix("/static/"))
                if relative.is_absolute() or ".." in relative.parts:
                    return self._send_json(400, {"error": "unsafe path"})
                target = (FRONTEND / relative).resolve()
                target.relative_to(FRONTEND.resolve())
                if target.is_file():
                    return self._send_file(target)
            return self._send_json(404, {"error": "not_found"})
        except (CaseError, ValueError, OSError) as exc:
            return self._send_json(400, {"error": type(exc).__name__, "message": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._payload()
            if path == "/api/diagnose":
                result = self.context.diagnosis.diagnose(str(payload.get("case_id") or ""))
            elif path == "/api/repair":
                result = self.context.repair.generate(
                    str(payload.get("case_id") or ""),
                    mode=str(payload.get("mode") or "demo"),
                )
            elif path == "/api/verify":
                result = self.context.verification.verify(
                    str(payload.get("case_id") or ""),
                    str(payload.get("replacement_rtl") or ""),
                )
            elif path == "/api/run-case":
                result = self.context.run_case(payload)
            else:
                return self._send_json(404, {"error": "not_found"})
            return self._send_json(200, result)
        except (CaseError, ValueError, RuntimeError, OSError) as exc:
            return self._send_json(400, {"error": type(exc).__name__, "message": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the R3E-AIC demo API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    context = CompetitionContext(ROOT, Path(args.output_root))
    Handler.context = context
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"R3E-AIC API listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
