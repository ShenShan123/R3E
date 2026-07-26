#!/usr/bin/env python3
"""Audit recovery rows and build a non-destructive merged statistical view."""
from __future__ import annotations
import argparse, hashlib, json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p:Path)->list[dict]:return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def write(p:Path,rows:list[dict])->None:
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True)+"\n" for x in rows))
def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument("--protocol",type=Path,required=True);a=ap.parse_args()
    pp=a.protocol.resolve();p=json.loads(pp.read_text());run=ROOT/p["run_root"]
    problems=[];aggregate={};recovery_hashes={}
    for benchmark,inventory in p["cases"].items():
        base=read(ROOT/p["base_canonical"][benchmark]["path"]); expected={x["case_id"]:x for x in inventory}
        if not inventory:
            recovered=[]
        else:
            cp=run/benchmark/"seed_101"/"canonical_candidates.jsonl"
            if not cp.is_file(): problems.append(f"{benchmark}: missing recovery canonical"); recovered=[]
            else: recovered=read(cp); recovery_hashes[benchmark]=sha(cp)
        if len(recovered)!=len(expected) or {x.get("case_id") for x in recovered}!=set(expected):
            problems.append(f"{benchmark}: recovery inventory mismatch")
        recovered_map={x["case_id"]:x for x in recovered}
        for cid,row in recovered_map.items():
            spec=expected[cid]
            if row.get("prompt_hash")!=spec["prompt_hash"]: problems.append(f"{benchmark}:{cid}: prompt drift")
            if row.get("model")!="deepseek-v4-pro" or row.get("seed")!=101 or row.get("candidate_index")!=0:
                problems.append(f"{benchmark}:{cid}: configuration drift")
            if str(row.get("status","")).startswith("api_"): problems.append(f"{benchmark}:{cid}: transport not recovered")
            for kind in ("prompt","response"):
                path=Path(row.get(f"{kind}_path", ""))
                if not path.is_file() or sha(path)!=row.get(f"{kind}_hash"): problems.append(f"{benchmark}:{cid}: {kind} hash")
            if row.get("patch_path"):
                path=Path(row["patch_path"])
                if not path.is_file() or sha(path)!=row.get("patch_hash"): problems.append(f"{benchmark}:{cid}: patch hash")
        merged=[]
        for row in base:
            if row["case_id"] in expected:
                if row.get("status")!="api_transport_error": problems.append(f"{benchmark}:{row['case_id']}: base not transport-only")
                if row["case_id"] in recovered_map: merged.append(recovered_map[row["case_id"]])
            else: merged.append(row)
        merged_path=run/"merged"/f"{benchmark}_seed101.canonical_candidates.jsonl";write(merged_path,merged)
        repaired=sum(bool(x.get("oracle_ok")) for x in merged); total=len(merged)
        aggregate[benchmark]={"repaired":repaired,"total":total,"percent":round(100*repaired/total,2),
                              "status_counts":dict(sorted(Counter(str(x.get("status")) for x in merged).items())),
                              "merged_canonical":str(merged_path.relative_to(ROOT)),"merged_sha256":sha(merged_path)}
    audit={"schema":"r3e-table2-direct-deepseek-v4-pro-transport-recovery-audit-v1",
           "created_at":datetime.now(timezone.utc).isoformat(),"verdict":"PASS" if not problems else "FAIL",
           "protocol_sha256":sha(pp),"problems":problems,"recovery_canonical_sha256":recovery_hashes,
           "aggregate":aggregate,"historical_artifacts_overwritten":False,
           "interpretation":"transport recovery supplies the first valid semantic response for five response-less calls"}
    (run/"MERGED_AGGREGATE.json").write_text(json.dumps(aggregate,indent=2,sort_keys=True)+"\n")
    (run/"RECOVERY_AUDIT.json").write_text(json.dumps(audit,indent=2,sort_keys=True)+"\n")
    print(json.dumps(audit,indent=2,sort_keys=True));return 0 if audit["verdict"]=="PASS" else 1
if __name__=="__main__":raise SystemExit(main())
