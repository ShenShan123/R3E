#!/usr/bin/env python3
import argparse,json
from pathlib import Path
from formal_audits import audit_registry
p=argparse.ArgumentParser();p.add_argument("registry",type=Path);a=p.parse_args();x=audit_registry(a.registry);print(json.dumps(x,indent=2));raise SystemExit(x["verdict"]!="PASS")
