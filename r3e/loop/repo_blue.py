"""Blue on repository tasks (HWE): repair RTL inside the visible build's scope.

Scope: RTL repair within the public build of one directed test, not full-repository
or multi-configuration repair.

Inputs come only from a task's ``public/`` directory (``task.json``, ``spec.txt``,
``visible/build.json`` and its test sources); ``oracle/``, ``validation/`` and the
evaluator manifest are never read. Every file read is recorded for isolation checks.

Context (decided once per task, before any model call, from the baseline):
- the RTL **dependency closure** is what the visible build actually reads,
  taken from Verilator's dependency file of a baseline build (explicit sources,
  headers and packages found through ``-I``), not the command-line file list;
- tokens are counted with a fixed tokenizer (``token_count.py``; consistent with
  the provider on its 16 calibration requests); if the closure's repository
  files fit ``rtl_budget`` the task is ``direct`` (one repair call per attempt
  with all of them), else ``select`` (each attempt: a file-selection call on a
  repository map, then a repair call with the chosen files, within the same
  budget, counted on the files' current content). Before every repair request
  the shown RTL is recounted on its current content; over the budget, the
  request is not sent (``rtl_context_limit``, not a Blue failure). No file is
  ever truncated;
- the whole request is checked separately against ``context_tokens`` minus the
  output reservation; a request that does not fit is not sent and ends the
  encounter as ``request_too_large`` (a runner limit, not a Blue failure).

Selection: Blue returns paths with a reason. The runner checks deterministically
that every path is in the closure, without duplicates, within the budget. An
invalid selection uses up that attempt (its call is spent; no repair call); there
is no hidden extra try. Selecting the files a reference patch changed would only
be "file selection coverage", never a diagnosis.

Candidate state carries across attempts: attempt k edits the files as Blue's
earlier applied edits left them, and is shown those current contents. Only
files shown in the attempt (all of the closure on ``direct``, the selection on
``select``) may be edited in it. An edit
batch is applied atomically: if any edit fails, none applies and the reason is
reported. Only closure repository RTL is editable; tests, harness sources, the
build recipe, path traversal and files outside the closure are refused.

Results: ``compile_fail`` (Verilator error), ``visible_fail`` (fail marker,
nonzero exit, or no pass marker, including a test's own ``TB_FAIL timeout``),
``timeout`` (only when the runner kills a process group at its time limit),
``infra_error`` (tool or environment; not a Blue failure), ``invalid_edit``,
``invalid_selection``, ``visible_pass`` (exit 0, pass marker, no fail marker).

Only provider failures are caught; any other exception (configuration, internal
error) stops the run instead of being booked as a failed repair. Every applied
candidate is archived (complete changed files and a patch, tied to the baseline
hashes) before it is built, so it survives an early exit. The result's call count
is the ledger's: calls made during the encounter, including those of an attempt
that ended early.
"""
from __future__ import annotations

import json
import os
import difflib
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from r3e.protocol.hashing import canonical_json, hash_payload

from .blue_provider import _locate

RTL_SUFFIXES = (".sv", ".v", ".svh", ".vh")
TEST_PATH = re.compile(r"(^|/)(dv|test|tests|tb|verif)(/|$)")
SYSTEM_SELECT = ("You choose which repository files to read before proposing an RTL repair. Return one strict "
                 "JSON object only. You do not repair, verify, or claim correctness in this step.")
SYSTEM_REPAIR = ("You propose one RTL repair candidate for a repository. Return one strict JSON object only. You "
                 "do not verify, rank, select, or claim correctness. Give the candidate as edits to the shown "
                 "files or as complete replacement files, and a concise description of your edit.")
LENS = ("You repair a functional RTL bug in a repository using the specification, the visible directed test and "
        "its results. Only the files marked editable may change.")


class RepoTaskError(RuntimeError):
    pass


@dataclass
class RepoTask:
    task_dir: Path
    task: dict[str, Any]
    spec: str
    build: dict[str, Any]
    harness: dict[str, str]
    read_files: list[str] = field(default_factory=list)

    @property
    def public(self) -> Path:
        return self.task_dir / "public"

    @property
    def repo(self) -> Path:
        return self.public / self.task["source_root"]

    @classmethod
    def load(cls, task_dir: Path) -> "RepoTask":
        public = Path(task_dir) / "public"
        read: list[str] = []

        def read_text(rel: str) -> str:
            path = (public / rel).resolve()
            if public.resolve() not in path.parents:
                raise RepoTaskError(f"task input outside public/: {rel}")
            read.append(str(path.relative_to(Path(task_dir).resolve())))
            return path.read_text(encoding="utf-8", errors="replace")
        task = json.loads(read_text("task.json"))
        build = json.loads(read_text(task["visible_test"]))
        visible = Path(task["visible_test"]).parent
        harness = {name: read_text(str(visible / name)) for name in build["source_files"]}
        return cls(Path(task_dir), task, read_text(task["spec"]), build, harness, read)


# ----------------------------------------------------------------------- execution


@dataclass(frozen=True)
class RunResult:
    tier: str
    stage: str
    evidence: dict[str, Any]
    build_seconds: float = 0.0
    sim_seconds: float = 0.0
    closure: tuple[str, ...] = ()


def _excerpt(text: str, *, errors_only: bool = False, limit: int = 4000) -> str:
    lines = text.splitlines()
    if errors_only:
        lines = [l for l in lines if "%Error" in l] or lines[-30:]
    else:
        lines = lines[-60:]
    out = "\n".join(lines)
    return out[-limit:]


def _run(cmd: list[str], cwd: Path, timeout: float) -> tuple[int | None, str]:
    """Run ``cmd`` in its own process group; on timeout the whole group is killed. Returns (rc or None, output)."""
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, _ = proc.communicate()
        return None, out


def execute(task: RepoTask, overlay: Mapping[str, str], *, build_timeout: float = 900, sim_timeout: float = 120,
            keep: Path | None = None) -> RunResult:
    """Build and run the visible test on baseline + ``overlay`` in a fresh scratch copy of ``public/``."""
    with tempfile.TemporaryDirectory(dir=keep) as tmp:
        work = Path(tmp)
        repo = work / "repo"
        shutil.copytree(task.repo, repo, symlinks=True)
        for rel, text in overlay.items():
            (repo / rel).write_text(text, encoding="utf-8")
        harness = work / "harness"
        harness.mkdir()
        for name, text in task.harness.items():
            (harness / name).write_text(text, encoding="utf-8")
        argv = [a.replace("{harness}", str(harness)) for a in task.build["argv"]]
        tool = shutil.which(task.build["tool"])
        if tool is None:
            return RunResult("infra_error", "build", {"message": f"{task.build['tool']} not found"})
        t0 = time.monotonic()
        try:
            rc, log = _run([tool, *argv], repo, build_timeout)
        except OSError as exc:
            return RunResult("infra_error", "build", {"message": str(exc)[:300]})
        build_s = time.monotonic() - t0
        if rc is None:
            return RunResult("timeout", "build", {"message": f"build process group killed after {build_timeout}s"},
                             build_seconds=build_s)
        closure = _closure(harness / "obj_dir", repo, harness)
        if rc != 0:
            tier = "compile_fail" if "%Error" in log else "infra_error"
            return RunResult(tier, "build", {"returncode": rc, "log": _excerpt(log, errors_only=True)},
                             build_seconds=build_s, closure=closure)
        binary = harness / "obj_dir" / task.build["binary"]
        if not binary.exists():
            return RunResult("infra_error", "build", {"message": "binary not produced", "log": _excerpt(log)},
                             build_seconds=build_s, closure=closure)
        t1 = time.monotonic()
        try:
            sim_rc, out = _run([str(binary)], repo, sim_timeout)
        except OSError as exc:  # the simulator could not be started: environment, not Blue
            return RunResult("infra_error", "simulation", {"message": str(exc)[:300]},
                             build_seconds=build_s, closure=closure)
        sim_s = time.monotonic() - t1
        if sim_rc is None:
            return RunResult("timeout", "simulation", {"message": f"simulation process group killed after {sim_timeout}s"},
                             build_seconds=build_s, sim_seconds=sim_s, closure=closure)
        passed = (sim_rc == 0 and task.build["pass_marker"] in out and task.build["fail_marker"] not in out)
        evidence = {"returncode": sim_rc, "pass_marker_found": task.build["pass_marker"] in out,
                    "fail_marker_found": task.build["fail_marker"] in out, "log": _excerpt(out)}
        return RunResult("visible_pass" if passed else "visible_fail", "simulation", evidence,
                         build_seconds=build_s, sim_seconds=sim_s, closure=closure)


def _closure(obj_dir: Path, repo: Path, harness: Path) -> tuple[str, ...]:
    """Files Verilator read, from its ``*__ver.d`` dependency file: repository paths, and ``harness:`` names."""
    files: set[str] = set()
    for dep in obj_dir.glob("*__ver.d"):
        for token in dep.read_text().replace("\\\n", " ").split():
            path = Path(token.rstrip(":"))
            if path.suffix not in RTL_SUFFIXES:
                continue
            if not path.is_absolute():  # Verilator writes paths relative to the build's working directory
                path = repo / path
            try:
                files.add(str(path.resolve().relative_to(repo.resolve())))
            except ValueError:
                if harness.resolve() in path.resolve().parents:
                    files.add("harness:" + path.name)
    return tuple(sorted(files))


# ----------------------------------------------------------------------- context


_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def repository_map(state: Mapping[str, str], counter) -> list[dict[str, Any]]:
    """Per closure file (current content): declared modules/packages, imports, instantiated closure modules,
    parameters, size."""
    files = list(state)
    texts = {f: _COMMENT.sub(" ", state[f]) for f in files}
    declared = {f: re.findall(r"\b(?:module|interface)\s+(?:automatic\s+)?(\w+)", t) for f, t in texts.items()}
    known = {m for ms in declared.values() for m in ms}
    out = []
    for f, t in texts.items():
        instances = sorted({m for m in re.findall(r"^\s*(\w+)\s*(?:#\s*\(|\w+\s*\()", t, re.M) if m in known}
                           - set(declared[f]))
        out.append({"path": f, "editable": editable(f), "tokens": counter.count(state[f]),
                    "modules": declared[f], "packages": re.findall(r"\bpackage\s+(\w+)", t),
                    "imports": sorted(set(re.findall(r"\bimport\s+(\w+)::", t))),
                    "instantiates": instances,
                    "parameters": sorted(set(re.findall(r"\bparameter\s+(?:[\w:]+\s+)?(?:\[[^\]]*\]\s*)?(\w+)\s*=", t)))})
    return out


def editable(rel: str) -> bool:
    return rel.endswith(RTL_SUFFIXES) and not TEST_PATH.search(rel) and not rel.startswith("harness:")


def build_configuration(task: RepoTask) -> dict[str, Any]:
    argv = task.build["argv"]
    return {"tool": task.build["tool"],
            "top_module": next((argv[i + 1] for i, a in enumerate(argv) if a == "--top-module"), None),
            "defines": [a[2:] for a in argv if a.startswith("-D")],
            "include_dirs": [a[2:] for a in argv if a.startswith("-I")]}


# ----------------------------------------------------------------------- edits


def apply_batch(state: dict[str, str], edits: Any, allowed: set[str]) -> tuple[dict[str, str] | None, str | None]:
    """All edits or none: returns (new state, None) or (None, reason)."""
    if not isinstance(edits, list) or not edits:
        return None, "edits must be a non-empty list"
    new = dict(state)
    for i, e in enumerate(edits):
        if not isinstance(e, dict) or not isinstance(e.get("file"), str):
            return None, f"edit {i}: needs a string file"
        raw = e["file"]
        rel = os.path.normpath(raw)
        if os.path.isabs(raw) or rel.startswith("..") or rel != raw:
            return None, f"edit {i}: path {raw!r} is not a plain, normalized repository path"
        if rel not in allowed:
            return None, f"edit {i}: {rel} is not an editable file of the visible build's RTL"
        if set(e) == {"file", "content"} and isinstance(e["content"], str):
            new[rel] = e["content"]
            continue
        if set(e) != {"file", "find", "replace"} or not all(isinstance(e[k], str) for k in ("find", "replace")):
            return None, f"edit {i}: needs file+find+replace or file+content"
        text = new[rel]
        if e["find"] == "":
            new[rel] = text.rstrip("\n") + "\n" + e["replace"] + ("" if e["replace"].endswith("\n") else "\n")
            continue
        span = _locate(text, e["find"])
        if isinstance(span, str):
            return None, f"edit {i} ({rel}): find text {span}"
        new[rel] = text[:span[0]] + e["replace"] + text[span[1]:]
    return new, None


# ----------------------------------------------------------------------- runner


@dataclass(frozen=True)
class RepoBlueConfig:
    budget_k: int = 3
    max_infra_retries: int = 2
    rtl_budget: int = 40_000
    context_tokens: int = 131_072
    output_reserve: int = 32_768
    max_evidence_chars: int = 6000

    def worst_case_calls(self, route: str) -> int:
        return (2 if route == "select" else 1) * self.budget_k + self.max_infra_retries


@dataclass
class Setup:
    route: str
    closure: tuple[str, ...]
    repo_files: list[str]
    file_tokens: dict[str, int]
    rtl_tokens: int
    baseline: RunResult
    baseline_hashes: dict[str, str] = field(default_factory=dict)


def setup(task: RepoTask, counter, cfg: RepoBlueConfig) -> Setup:
    """Baseline run (closure and initial evidence) and the locked route, before any model call."""
    base = execute(task, {})
    if base.tier != "visible_fail":
        raise RepoTaskError(f"baseline is {base.tier}, not a visible failure")
    repo_files = [f for f in base.closure if not f.startswith("harness:")]
    file_tokens = {f: counter.count((task.repo / f).read_text(errors="replace")) for f in repo_files}
    tokens = sum(file_tokens.values())
    hashes = {f: hash_payload((task.repo / f).read_text(errors="replace")) for f in repo_files}
    return Setup("direct" if tokens <= cfg.rtl_budget else "select", base.closure, repo_files, file_tokens, tokens,
                 base, hashes)


class RepoBlueRunner:
    def __init__(self, *, client, counter, archive: Path, cfg: RepoBlueConfig = RepoBlueConfig()):
        self.client, self.counter, self.cfg = client, counter, cfg
        self.archive = Path(archive)  # mandatory: every applied candidate must be recoverable
        self.archive.mkdir(parents=True, exist_ok=True)
        probe = self.archive / ".write_probe"
        probe.write_text("ok")  # fails at start, before any call, if the archive is not writable
        probe.unlink()

    def _save(self, task: RepoTask, s: Setup, overlay: Mapping[str, str]) -> str | None:
        """Archive an applied candidate (changed files and patch) before it is built."""
        if not overlay:
            return None
        h = hash_payload(dict(overlay))
        if True:
            patch = "".join("".join(difflib.unified_diff((task.repo / f).read_text(errors="replace").splitlines(True),
                                                         overlay[f].splitlines(True), "a/" + f, "b/" + f))
                            for f in sorted(overlay))
            path = self.archive / f"{task.task['task_id']}__{h[7:23]}.json"
            path.write_text(json.dumps({"task_id": task.task["task_id"], "candidate_hash": h,
                                        "baseline_hashes": {f: s.baseline_hashes[f] for f in overlay},
                                        "files": dict(overlay), "patch": patch}))
        return h

    def _call(self, phase: str, system: str, user: dict[str, Any], seed: int) -> dict[str, Any]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": canonical_json(user)}]
        size = self.counter.request(messages)
        if size + self.cfg.output_reserve > self.cfg.context_tokens:
            return {"too_large": size}
        t0 = time.monotonic()
        with self.client.in_phase(phase):
            response = self.client.complete_json(messages=messages, seed=seed)
        return {**response, "counted_request_tokens": size, "model_seconds": round(time.monotonic() - t0, 2)}

    def run(self, task: RepoTask, s: Setup, *, seed: int) -> dict[str, Any]:
        from r3e.providers.openai_compatible import OpenAICompatibleProviderViolation

        state = {f: (task.repo / f).read_text(errors="replace") for f in s.repo_files}
        baseline = dict(state)
        allowed = {f for f in s.repo_files if editable(f)}
        calls_before = self.client.total_calls
        evidence = {"result": s.baseline.tier, **s.baseline.evidence}
        history: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []
        infra = 0
        common = {"specification": task.spec, "visible_test_sources": task.harness,
                  "build_configuration": build_configuration(task), "lens_instruction": LENS}

        def call(phase, system, user, seed_):
            nonlocal infra
            while True:
                try:
                    return self._call(phase, system, user, seed_)
                except OpenAICompatibleProviderViolation as exc:  # only provider failures; anything else stops
                    diag = dict(getattr(exc, "diagnostics", {}) or {})
                    if diag.get("finish_reason") == "length":
                        return {"no_answer": True, "output_tokens": diag.get("output_tokens", 0)}
                    infra += 1
                    attempts.append({"infra_failure": True, "phase": phase, "error": type(exc).__name__})
                    if infra > self.cfg.max_infra_retries:
                        return {"inconclusive": True}

        def finish(outcome, rec=None):
            if rec is not None and len(rec) > 2:  # an attempt that made calls or checks before ending early
                attempts.append({**rec, "tier": rec.get("tier", outcome)})
            overlay = {f: t for f, t in state.items() if t != baseline[f]}
            return self._result(task, s, attempts, outcome, overlay, self.client.total_calls - calls_before)

        for k in range(self.cfg.budget_k):
            rec: dict[str, Any] = {"attempt": k, "route": s.route}
            visible = s.repo_files
            if s.route == "select":
                sel = call("localization", SYSTEM_SELECT, {
                    **common, "task": "select_files", "current_failure_evidence": evidence,
                    "previous_attempts": history[-3:], "repository_map": repository_map(state, self.counter),
                    "rtl_token_budget": self.cfg.rtl_budget,
                    "required_output_schema": {"files": [{"path": "closure path", "reason": "why it is needed"}]}},
                    seed * 100 + k)
                if sel.get("inconclusive") or sel.get("too_large"):
                    return finish("request_too_large" if sel.get("too_large") else "inconclusive", rec)
                rec["selection_tokens"] = {"input": sel.get("input_tokens", 0), "output": sel.get("output_tokens", 0),
                                           "counted_request": sel.get("counted_request_tokens"),
                                           "model_seconds": sel.get("model_seconds")}
                chosen, reason = self._check_selection(sel, state)
                rec["selection"] = chosen
                if reason:
                    rec.update(tier="invalid_selection", feedback=reason)
                    attempts.append(rec)
                    history.append({"attempt": k, "result": "invalid_selection", "reason": reason})
                    continue
                visible = [c["path"] for c in chosen]
            shown = sum(self.counter.count(state[f]) for f in visible)
            if shown > self.cfg.rtl_budget:  # e.g. direct files grew past the budget: a context limit, not Blue
                rec["rtl_tokens_shown"] = shown
                return finish("rtl_context_limit", rec)
            rep = call("blue_inference", SYSTEM_REPAIR, {
                **common, "task": "repair", "files": {f: {"editable": f in allowed, "content": state[f]} for f in visible},
                "current_failure_evidence": evidence, "previous_attempts": history[-3:],
                "required_output_schema": {"edits": [{"file": "path", "find": "exact text, unique; empty to append",
                                                      "replace": "new text"}],
                                           "files": [{"file": "path", "content": "complete replacement"}],
                                           "edit": "concise description", "rule": "exactly one of edits or files"}},
                seed * 100 + 50 + k)
            if rep.get("inconclusive") or rep.get("too_large"):
                return finish("request_too_large" if rep.get("too_large") else "inconclusive", rec)
            rec["repair_tokens"] = {"input": rep.get("input_tokens", 0), "output": rep.get("output_tokens", 0),
                                    "counted_request": rep.get("counted_request_tokens"),
                                    "model_seconds": rep.get("model_seconds")}
            if rep.get("no_answer"):
                rec.update(tier="no_answer", feedback="ran out of output budget before answering")
                attempts.append(rec)
                history.append({"attempt": k, "result": "no_answer", "reason": rec["feedback"]})
                continue
            result = rep.get("result")
            result = result if isinstance(result, dict) else {}
            batch = result.get("edits") if "edits" in result else result.get("files")
            if ("edits" in result) == ("files" in result):
                new, why = None, "exactly one of edits or files is required"
            else:
                new, why = apply_batch(state, batch, allowed & set(visible))  # only files shown in this attempt
            rec["edit"] = str(result.get("edit") or "")[:400]
            if new is None:
                rec.update(tier="invalid_edit", feedback=why)
                attempts.append(rec)
                history.append({"attempt": k, "edit": rec["edit"], "result": "invalid_edit", "reason": why})
                continue
            state = new
            overlay = {f: t for f, t in state.items() if t != baseline[f]}
            rec["candidate_hash"] = self._save(task, s, overlay)  # archived before the build
            run = execute(task, overlay)
            if run.tier == "infra_error":  # tool or environment, not Blue: rerun once without a model call
                run = execute(task, overlay)
            rec.update(tier=run.tier, stage=run.stage, build_seconds=round(run.build_seconds, 2),
                       sim_seconds=round(run.sim_seconds, 2), changed_files=sorted(overlay))
            attempts.append(rec)
            if run.tier == "infra_error":
                return finish("inconclusive_infrastructure")
            evidence = {"result": run.tier, **{k2: v for k2, v in run.evidence.items()}}
            history.append({"attempt": k, "edit": rec["edit"], "result": run.tier,
                            "evidence": json.dumps(run.evidence)[: self.cfg.max_evidence_chars]})
            if run.tier == "visible_pass":
                return finish("fixed")
        return finish("not_fixed")

    def _check_selection(self, sel: Mapping[str, Any], state: Mapping[str, str]) -> tuple[list[dict[str, str]], str | None]:
        """Paths in the closure, unique, and within the budget counted on the files' current content."""
        if sel.get("no_answer"):
            return [], "ran out of output budget before selecting"
        result = sel.get("result")
        files = result.get("files") if isinstance(result, dict) else None
        if not isinstance(files, list) or not files:
            return [], "files must be a non-empty list"
        chosen, seen, total = [], set(), 0
        for f in files:
            if not isinstance(f, dict) or not isinstance(f.get("path"), str) or not str(f.get("reason") or "").strip():
                return [], "each selection needs a path and a reason"
            path = f["path"]
            if path not in state:
                return [], f"{path} is not in the visible build's RTL closure"
            if path in seen:
                return [], f"{path} selected twice"
            seen.add(path)
            total += self.counter.count(state[path])
            chosen.append({"path": path, "reason": str(f["reason"])[:300]})
        if total > self.cfg.rtl_budget:
            return chosen, f"selected files hold {total} tokens, over the budget of {self.cfg.rtl_budget}"
        return chosen, None

    def _result(self, task, s, attempts, outcome, overlay, calls) -> dict[str, Any]:
        return {"task_id": task.task["task_id"], "route": s.route, "rtl_tokens": s.rtl_tokens,
                "closure": list(s.closure), "outcome": outcome, "attempts": attempts,
                "calls": calls,  # from the ledger: every call made during this encounter
                "final_candidate_hash": self._save(task, s, overlay),
                "inputs_read": list(task.read_files)}
