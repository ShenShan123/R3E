"""Icarus Verilog Grounded Runtime provider."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Mapping

from r3e.protocol.hashing import hash_file, hash_payload

from .command_runner import GroundedCommandRunner
from .provider_receipts import (
    build_provider_receipt,
    provider_implementation_hash,
)
from .receipts import build_command_receipt


ICARUS_PROVIDER_VERSION = "r3e-icarus-grounded-provider-v1"
_ORACLE_LINE = re.compile(
    r"^R3E_ORACLE pass=(?P<passed>[01]) "
    r"signature=(?P<signature>[A-Za-z0-9_.:-]+) "
    r"first=(?P<first>[A-Za-z0-9_.:-]+) "
    r"topology=(?P<topology>[A-Za-z0-9_.:-]+)$"
)
_WAVEFORM_LINE = re.compile(
    r"^R3E_WAVEFORM "
    r"signal=(?P<signal>[A-Za-z_][A-Za-z0-9_$]*|none) "
    r"first_cycle=(?P<first_cycle>[0-9]+|none) "
    r"cycle_offset=(?P<cycle_offset>[0-9]+|none) "
    r"relation=(?P<relation>[A-Za-z0-9_.:-]+|none) "
    r"assignment=(?P<assignment>[A-Za-z0-9_.:-]+|none) "
    r"cone_depth=(?P<cone_depth>[0-9]+|none) "
    r"pattern=(?P<pattern>[A-Za-z0-9_.:-]+|none)$"
)
_VERILOG_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_RECEIPT_PREFIX = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


class IcarusProviderViolation(RuntimeError):
    """Raised when the Icarus execution/oracle contract is invalid."""


def locate_icarus_tools() -> dict[str, Path]:
    tools = {}
    for name in ("iverilog", "vvp"):
        path = shutil.which(name)
        if not path:
            raise IcarusProviderViolation(f"required Icarus tool is missing: {name}")
        tools[name] = Path(path).resolve()
    return tools


def icarus_toolchain_fingerprint(tools: dict[str, Path]) -> dict[str, Any]:
    grounded_root = Path(__file__).resolve().parent
    engine_paths = {
        name: path.parent.parent / "libexec" / name
        for name, path in tools.items()
    }
    if any(not path.is_file() for path in engine_paths.values()):
        engine_paths = dict(tools)
    payload = {
        "schema_version": ICARUS_PROVIDER_VERSION,
        "provider_id": "icarus-verilog",
        "iverilog_hash": hash_file(tools["iverilog"]),
        "vvp_hash": hash_file(tools["vvp"]),
        "iverilog_engine_hash": hash_file(engine_paths["iverilog"]),
        "vvp_engine_hash": hash_file(engine_paths["vvp"]),
        "oracle_provider": "r3e-stdout-oracle-v1",
        "oracle_provider_implementation_hash": provider_implementation_hash(
            provider_kind="oracle_parser",
            provider_id="r3e-stdout-oracle",
            provider_version="1",
        ),
        "runner_implementation_hash": hash_payload({
            "command_runner.py": hash_file(
                grounded_root / "command_runner.py"
            ),
            "receipts.py": hash_file(grounded_root / "receipts.py"),
        }),
    }
    payload["toolchain_fingerprint_hash"] = hash_payload(payload)
    return payload


def verify_icarus_toolchain_fingerprint(
    fingerprint: Mapping[str, Any],
) -> dict[str, Any]:
    payload = dict(fingerprint)
    expected = icarus_toolchain_fingerprint(locate_icarus_tools())
    if payload != expected:
        raise IcarusProviderViolation(
            "Icarus toolchain fingerprint differs from local authority"
        )
    return payload


def parse_oracle_output(stdout: bytes) -> dict[str, Any]:
    try:
        text = stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise IcarusProviderViolation("oracle stdout is not UTF-8") from exc
    oracle_lines = [
        line.strip() for line in text.splitlines()
        if line.strip().startswith("R3E_ORACLE")
    ]
    if len(oracle_lines) != 1:
        raise IcarusProviderViolation(
            "simulation must emit exactly one R3E_ORACLE record"
        )
    match = _ORACLE_LINE.fullmatch(oracle_lines[0])
    if match is None:
        raise IcarusProviderViolation(
            "simulation emitted a malformed R3E_ORACLE record"
        )
    passed = match.group("passed") == "1"
    descriptors = {
        match.group("signature"),
        match.group("first"),
        match.group("topology"),
    }
    if (passed and descriptors != {"none"}) or (
        not passed and "none" in descriptors
    ):
        raise IcarusProviderViolation(
            "oracle pass bit and failure descriptors are inconsistent"
        )
    waveform_lines = [
        line.strip() for line in text.splitlines()
        if line.strip().startswith("R3E_WAVEFORM")
    ]
    if len(waveform_lines) > 1:
        raise IcarusProviderViolation(
            "simulation must emit at most one R3E_WAVEFORM record"
        )
    waveform = {
        "waveform_observation_complete": False,
        "first_divergence_signal": "",
        "first_divergence_cycle": None,
        "cycle_offset": None,
        "temporal_relation": "",
        "assignment_type": "",
        "cone_depth": None,
        "mismatch_pattern": "",
    }
    if waveform_lines:
        waveform_match = _WAVEFORM_LINE.fullmatch(waveform_lines[0])
        if waveform_match is None:
            raise IcarusProviderViolation(
                "simulation emitted a malformed R3E_WAVEFORM record"
            )
        values = waveform_match.groupdict()
        none_fields = {
            name for name, value in values.items() if value == "none"
        }
        if (passed and len(none_fields) != len(values)) or (
            not passed and none_fields
        ):
            raise IcarusProviderViolation(
                "waveform record is inconsistent with oracle verdict"
            )
        if not passed:
            first_cycle = int(values["first_cycle"])
            cycle_offset = int(values["cycle_offset"])
            cone_depth = int(values["cone_depth"])
            if max(first_cycle, cycle_offset, cone_depth) > 1_000_000:
                raise IcarusProviderViolation(
                    "waveform numeric observation exceeds protocol limit"
                )
            waveform = {
                "waveform_observation_complete": True,
                "first_divergence_signal": values["signal"],
                "first_divergence_cycle": first_cycle,
                "cycle_offset": cycle_offset,
                "temporal_relation": values["relation"],
                "assignment_type": values["assignment"],
                "cone_depth": cone_depth,
                "mismatch_pattern": values["pattern"],
            }
        else:
            waveform["waveform_observation_complete"] = True
    waveform["waveform_observation_hash"] = hash_payload(waveform)
    result = {
        "oracle_pass": passed,
        "functional_mismatch": not passed,
        "effect_signature": match.group("signature"),
        "first_divergence_hash": hash_payload({
            "first_divergence": match.group("first"),
        }),
        "mismatch_topology_hash": hash_payload({
            "mismatch_topology": match.group("topology"),
        }),
        "raw_record_hash": hash_payload({"record": match.group(0)}),
        **waveform,
    }
    return result


class IcarusGroundedProvider:
    def __init__(
        self,
        *,
        workspace: str | Path,
        run_context_hash: str,
        timeout_seconds: float = 10.0,
        max_output_bytes: int = 1_000_000,
        max_memory_bytes: int = 1_000_000_000,
    ):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.tools = locate_icarus_tools()
        self.toolchain = icarus_toolchain_fingerprint(self.tools)
        self.toolchain_hash = self.toolchain["toolchain_fingerprint_hash"]
        self.command_runner = GroundedCommandRunner(
            allowed_root=self.workspace,
            artifact_root=self.workspace / "command_artifacts",
            allowed_executables=self.tools,
            run_context_hash=run_context_hash,
            toolchain_fingerprint_hash=self.toolchain_hash,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            max_memory_bytes=max_memory_bytes,
        )

    def execute(
        self,
        *,
        receipt_prefix: str,
        mode: str,
        rtl_path: str | Path,
        testbench_path: str | Path,
        top_module: str,
        semantic_hash: str,
        target_ast_node_exists: bool = True,
        operator_preconditions_hold: bool = True,
        semantic_provider_receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if mode not in {
            "clean_baseline",
            "poison_execution",
            "revert_execution",
        }:
            raise IcarusProviderViolation(f"unsupported execution mode: {mode}")
        if not _VERILOG_IDENTIFIER.fullmatch(top_module):
            raise IcarusProviderViolation(
                "top module is not a Verilog identifier"
            )
        if not _RECEIPT_PREFIX.fullmatch(receipt_prefix):
            raise IcarusProviderViolation(
                "receipt prefix contains unsafe path characters"
            )
        rtl = Path(rtl_path).resolve()
        testbench = Path(testbench_path).resolve()
        for path, label in ((rtl, "RTL"), (testbench, "testbench")):
            try:
                path.relative_to(self.workspace)
            except ValueError as exc:
                raise IcarusProviderViolation(
                    f"{label} escapes provider workspace"
                ) from exc
            if not path.is_file():
                raise IcarusProviderViolation(f"{label} is missing")
        subject_hash = hash_file(rtl)
        run_dir = self.workspace / "runs" / receipt_prefix
        run_dir.mkdir(parents=True, exist_ok=True)
        simulation_binary = run_dir / "simulation.out"
        common = ["-g2012", str(rtl), str(testbench)]
        parse = self.command_runner.run(
            receipt_id=f"{receipt_prefix}:parse",
            phase="parse",
            subject_hash=subject_hash,
            executable="iverilog",
            arguments=["-g2012", "-tnull", str(rtl), str(testbench)],
            cwd=run_dir,
            artifact_paths={"rtl": rtl, "testbench": testbench},
            observe=lambda _out, _err, _code, kind: {
                "parse_ok": kind == "completed",
            },
        )
        elaborate = self.command_runner.run(
            receipt_id=f"{receipt_prefix}:elaborate",
            phase="elaborate",
            subject_hash=subject_hash,
            executable="iverilog",
            arguments=[
                "-g2012",
                "-s",
                top_module,
                "-tnull",
                str(rtl),
                str(testbench),
            ],
            cwd=run_dir,
            artifact_paths={"rtl": rtl, "testbench": testbench},
            observe=lambda _out, _err, _code, kind: {
                "elaboration_ok": kind == "completed",
                "top_module": top_module,
            },
        )
        compile_result = self.command_runner.run(
            receipt_id=f"{receipt_prefix}:compile",
            phase="compile",
            subject_hash=subject_hash,
            executable="iverilog",
            arguments=[
                "-g2012",
                "-s",
                top_module,
                "-o",
                str(simulation_binary),
                *common[1:],
            ],
            cwd=run_dir,
            artifact_paths={
                "rtl": rtl,
                "testbench": testbench,
                "simulation_binary": simulation_binary,
            },
            observe=lambda _out, _err, _code, kind: {
                "compile_ok": kind == "completed",
            },
        )
        simulation = self.command_runner.run(
            receipt_id=f"{receipt_prefix}:simulation",
            phase="simulation",
            subject_hash=subject_hash,
            executable="vvp",
            arguments=[str(simulation_binary)],
            cwd=run_dir,
            artifact_paths={
                "simulation_binary_input": simulation_binary,
                "testbench": testbench,
            },
            observe=lambda _out, _err, _code, kind: {
                "simulation_complete": kind == "completed",
            },
        )
        try:
            oracle_result = parse_oracle_output(simulation.stdout)
        except IcarusProviderViolation:
            oracle_result = {
                "oracle_pass": False,
                "functional_mismatch": False,
                "effect_signature": "invalid_oracle_output",
                "first_divergence_hash": hash_payload({
                    "first_divergence": "unknown",
                }),
                "mismatch_topology_hash": hash_payload({
                    "mismatch_topology": "unknown",
                }),
                "raw_record_hash": hash_payload({
                    "invalid_stdout": simulation.receipt["artifact_hashes"]["stdout"],
                }),
                "waveform_observation_complete": False,
                "first_divergence_signal": "",
                "first_divergence_cycle": None,
                "cycle_offset": None,
                "temporal_relation": "",
                "assignment_type": "",
                "cone_depth": None,
                "mismatch_pattern": "",
            }
            oracle_result["waveform_observation_hash"] = (
                hash_payload({
                    key: value
                    for key, value in oracle_result.items()
                    if key
                    in {
                        "waveform_observation_complete",
                        "first_divergence_signal",
                        "first_divergence_cycle",
                        "cycle_offset",
                        "temporal_relation",
                        "assignment_type",
                        "cone_depth",
                        "mismatch_pattern",
                    }
                })
            )
        oracle_provider = build_provider_receipt(
            provider_kind="oracle_parser",
            provider_id="r3e-stdout-oracle",
            provider_version="1",
            input_artifact_hashes={
                "simulation_stdout": simulation.receipt["artifact_hashes"][
                    "stdout"
                ],
                "testbench": hash_file(testbench),
            },
            result=oracle_result,
        )
        children = [
            parse.receipt,
            elaborate.receipt,
            compile_result.receipt,
            simulation.receipt,
        ]
        child_kinds = [row["result_kind"] for row in children]
        if "timeout" in child_kinds:
            result_kind = "timeout"
        elif "resource_limit" in child_kinds:
            result_kind = "resource_limit"
        elif "crash" in child_kinds:
            result_kind = "crash"
        elif any(kind != "completed" for kind in child_kinds):
            result_kind = "tool_error"
        else:
            result_kind = "completed"
        observed = {
            "parse_ok": parse.receipt["result_kind"] == "completed",
            "elaboration_ok": elaborate.receipt["result_kind"] == "completed",
            "compile_ok": compile_result.receipt["result_kind"] == "completed",
            "simulation_complete": simulation.receipt["result_kind"]
            == "completed",
            "oracle_pass": bool(oracle_result["oracle_pass"]),
            "semantic_hash": semantic_hash,
            "provider_receipts": children,
            "oracle_provider_receipt": oracle_provider,
            "toolchain_fingerprint": self.toolchain,
        }
        if semantic_provider_receipt is not None:
            observed["semantic_provider_receipt"] = dict(
                semantic_provider_receipt
            )
        if mode == "poison_execution":
            observed.update({
                "target_ast_node_exists": bool(target_ast_node_exists),
                "operator_preconditions_hold": bool(
                    operator_preconditions_hold
                ),
                "functional_mismatch": bool(
                    oracle_result["functional_mismatch"]
                ),
                "first_divergence_hash": oracle_result[
                    "first_divergence_hash"
                ],
                "mismatch_topology_hash": oracle_result[
                    "mismatch_topology_hash"
                ],
                "effect_signature": oracle_result["effect_signature"],
                "waveform_observation_complete": oracle_result[
                    "waveform_observation_complete"
                ],
                "waveform_observation_hash": oracle_result[
                    "waveform_observation_hash"
                ],
                "first_divergence_signal": oracle_result[
                    "first_divergence_signal"
                ],
                "first_divergence_cycle": oracle_result[
                    "first_divergence_cycle"
                ],
                "cycle_offset": oracle_result["cycle_offset"],
                "temporal_relation": oracle_result[
                    "temporal_relation"
                ],
                "assignment_type": oracle_result["assignment_type"],
                "cone_depth": oracle_result["cone_depth"],
                "mismatch_pattern": oracle_result["mismatch_pattern"],
            })
        elif mode == "revert_execution":
            observed["restored_semantic_hash"] = semantic_hash
        aggregate = build_command_receipt(
            receipt_id=f"{receipt_prefix}:aggregate",
            phase=mode,
            subject_hash=subject_hash,
            run_context_hash=parse.receipt["run_context_hash"],
            toolchain_fingerprint_hash=self.toolchain_hash,
            command_argv=[ICARUS_PROVIDER_VERSION, mode],
            working_directory_hash=hash_payload({
                "run_directory": receipt_prefix,
            }),
            exit_code=0 if result_kind == "completed" else 1,
            timed_out=result_kind == "timeout",
            crashed=result_kind == "crash",
            result_kind=result_kind,
            wall_time_ms=sum(int(row["wall_time_ms"]) for row in children),
            artifact_hashes={
                **{
                    f"{row['phase']}_receipt": row["receipt_hash"]
                    for row in children
                },
                "oracle_provider_receipt": oracle_provider["receipt_hash"],
                "rtl": subject_hash,
                "testbench": hash_file(testbench),
                **(
                    {
                        "semantic_provider_receipt": str(
                            semantic_provider_receipt["receipt_hash"]
                        )
                    }
                    if semantic_provider_receipt is not None
                    else {}
                ),
            },
            observed=observed,
        )
        return aggregate
