#!/usr/bin/env python3
import argparse,json
from pathlib import Path
from formal_audits import audit_run
p=argparse.ArgumentParser();p.add_argument("run",type=Path);a=p.parse_args();x=audit_run(a.run);print(json.dumps(x,indent=2));raise SystemExit(x["verdict"]!="PASS")
