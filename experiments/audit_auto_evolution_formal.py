#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'r3e'),str(ROOT/'r3e/semantic_repair_bench')]
from semantic_repair_bench.formal_protocol import hash_payload,load_promoted_registry,load_registry,policy_hash
def main():
 ap=argparse.ArgumentParser();ap.add_argument('run',type=Path);a=ap.parse_args();bad=[]
 for f in ('run_manifest.json','heartbeat.json','accumulation_result.json','round_metrics.jsonl'):
  if not (a.run/f).is_file():bad.append({'missing_file':f})
 rows=[json.loads(x) for x in (a.run/'round_metrics.jsonl').read_text().splitlines() if x.strip()] if (a.run/'round_metrics.jsonl').exists() else []
 m=json.loads((a.run/'run_manifest.json').read_text()) if (a.run/'run_manifest.json').exists() else {}
 if m.get('status')!='complete':bad.append({'status':m.get('status')})
 if len(rows)!=4:bad.append({'round_rows':len(rows),'expected':4})
 for i,r in enumerate(rows):
  if r.get('leakage_pass') is not True:bad.append({'round':i,'leakage_pass':r.get('leakage_pass')})
  if r.get('frozen_external_n')!=13:bad.append({'round':i,'frozen_external_n':r.get('frozen_external_n'),'expected':13})
 if m.get('frozen_external_n')!=13:bad.append({'manifest_external_n':m.get('frozen_external_n'),'expected':13})
 if m.get('promotion_validation_n')!=20:bad.append({'manifest_promotion_n':m.get('promotion_validation_n'),'expected':20})
 if m.get('arm')=='Fixed-Blue' and m.get('distillation_enabled') is not False:bad.append({'fixed_blue_distillation':True})
 if m.get('arm')!='Auto-Promote' and any((r.get('activated_count') or 0)>0 for r in rows):bad.append({'unauthorized_activation':True})
 templates_path=a.run/'work/distilled_pattern_templates.json'
 templates=json.loads(templates_path.read_text()) if templates_path.exists() else []
 if m.get('arm')=='Fixed-Blue' and templates:bad.append({'fixed_blue_candidate_count':len(templates)})
 for i,t in enumerate(templates):
  body=dict(t);actual=body.pop('candidate_hash',None)
  expected=hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
  if actual!=expected:bad.append({'candidate':i,'candidate_hash_mismatch':True})
 decisions={}
 for p in sorted((a.run/'work/promotion_validation').glob('*/promotion_validation_report.json')):
  report=json.loads(p.read_text())
  for d in report.get('decisions',[]):
   body=dict(d);actual=body.pop('decision_hash',None)
   expected=hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
   if actual!=expected:bad.append({'decision_file':str(p),'decision_hash_mismatch':True})
   if actual:decisions[actual]=d
 formal_registry=a.run/'formal_registry.json';formal_decisions=a.run/'formal_decisions.json'
 if formal_decisions.exists():
  for d in json.loads(formal_decisions.read_text()):decisions[d['decision_hash']]=d
 promoted_path=a.run/'work/promoted_pattern_templates.json'
 promoted=json.loads(promoted_path.read_text()) if promoted_path.exists() else []
 for t in promoted:
  if t.get('promotion_decision_hash') not in decisions:bad.append({'template_id':t.get('template_id'),'missing_promotion_decision':True})
 try:
  reg=load_registry(formal_registry,formal_mode=True);fds=json.loads(formal_decisions.read_text());fdmap={x['decision_hash']:x for x in fds};active,ph=load_promoted_registry(formal_registry,fdmap)
  rh=hash_payload(reg)
  if m.get('registry_sha256_after')!=rh:bad.append({'registry_manifest_hash_mismatch':True})
  if rows and rows[-1].get('registry_hash')!=rh:bad.append({'registry_round_hash_mismatch':True})
  if rows and rows[-1].get('policy_hash')!=ph:bad.append({'policy_hash_mismatch':True})
  if m.get('arm')!='Auto-Promote' and active:bad.append({'unauthorized_registry_artifacts':len(active)})
 except Exception as e:bad.append({'formal_registry_error':str(e)})
 out={'verdict':'PASS' if not bad else 'FAIL','arm':m.get('arm'),'seed':m.get('seed'),'rounds':len(rows),'bad':bad};print(json.dumps(out,indent=2));raise SystemExit(out['verdict']!='PASS')
if __name__=='__main__':main()
