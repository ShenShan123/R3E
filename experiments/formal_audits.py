"""Shared read-only audits for formal R3E artifacts."""
from __future__ import annotations
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "r3e"))
from semantic_repair_bench.formal_protocol import FORMAL_RESULT_FIELDS, FormalProtocolViolation, load_registry

RUN_FIELDS = {"run_id","git_commit","dirty_tree","protocol_version","experiment_name","arm","seed","case_manifest_sha256","config_sha256","parent_policy_sha256","registry_sha256_before","registry_sha256_after","oracle_version","model_name","temperature","timeout","candidate_budget","start_time","end_time","status"}

def audit_run(path: Path):
    missing=[]; bad=[]
    manifest_path = path / "run_manifest.json"
    results_path = path / "case_results.jsonl"
    if not manifest_path.is_file():
        missing.append("run_manifest.json")
        m = {}
    else:
        m=json.loads(manifest_path.read_text()); missing += sorted(RUN_FIELDS-m.keys())
    if not results_path.is_file():
        missing.append("case_results.jsonl")
        rows=[]
    else:
        rows=[json.loads(l) for l in results_path.read_text().splitlines() if l.strip()]
    for i,r in enumerate(rows):
        x=FORMAL_RESULT_FIELDS-r.keys()
        if x: bad.append({"row":i,"missing":sorted(x)})
        if r.get("candidate_index", -1) >= 0:
            empty = [k for k in ("prompt_hash", "response_hash") if not r.get(k)]
            if empty: bad.append({"row":i,"empty_hashes":empty})
        reason = str(r.get("failure_reason") or "").lower()
        if any(x in reason for x in ("openai package not installed", "dependency preflight failed")):
            bad.append({"row":i,"environment_failure":r.get("failure_reason")})
        if any(x in reason for x in ("insufficient_quota", "quota is not enough", "invalid api key", "authentication", "error code: 401", "error code: 403")):
            bad.append({"row":i,"provider_failure":r.get("failure_reason")})
    return {"verdict":"PASS" if not missing and not bad else "FAIL","missing_fields":missing,"bad_rows":bad,"rows":len(rows)}

def audit_registry(path: Path):
    try: r=load_registry(path,formal_mode=True); return {"verdict":"PASS","artifacts":len(r["artifacts"]),"manual_artifact_loads":0,"legacy_loads":0}
    except (FormalProtocolViolation,Exception) as e: return {"verdict":"FAIL","error":str(e)}

def audit_no_manual(path: Path): return audit_registry(path)
