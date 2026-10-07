#!/usr/bin/env python3
"""Read-only selection-protocol replay; no model client, simulator or ledger writes."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from r3e.loop.corpus import load_public_manifest
from r3e.loop.freeform_curriculum import (
    SELECTION_PROTOCOL_VERSION, SelectionGroundingViolation, normalize_selection_grounding,
)
from r3e.loop.red_design import EditBudget, assess_mutation_plan
from r3e.protocol.hashing import hash_payload


def replay(ledger: Path, manifests: list[Path]) -> dict:
    carriers = {}
    for path in manifests:
        designs, _ = load_public_manifest(path.resolve(), ROOT)
        carriers.update({c.carrier_id: c for c in designs})
    original = ledger.read_bytes()
    records = [json.loads(line) for line in original.decode().splitlines()]
    items = []
    for row in records:
        selection = row.get('selection_output')
        if not isinstance(selection, dict):
            continue
        cid = selection.get('carrier_id')
        if cid not in carriers:
            raise ValueError(f'cannot replay missing carrier {cid}')
        carrier = carriers[cid]
        recorded = next((c for c in row.get('candidate_designs', []) if c['carrier_id'] == cid), None)
        if recorded and (recorded['clean_rtl_hash'] != hash_payload(carrier.clean_rtl)
                         or recorded['spec_hash'] != hash_payload(carrier.spec)):
            raise ValueError(f'carrier RTL/specification drift: {cid}')
        offered = [t['weakness_id'] for t in row['weakness_view']['open_weak_points']]
        item = {'round': row['round'], 'slot': row.get('slot'), 'carrier_id': cid,
                'historical_reason': row['reason'], 'offered_weakness_ids': offered}
        try:
            normalized = normalize_selection_grounding(selection, carrier.clean_rtl, offered)
        except SelectionGroundingViolation as exc:
            item['grounding_result'] = exc.reason
        else:
            item.update(grounding_result='pass',
                        normalization=normalized['selection_normalization'],
                        anchors=len(normalized['applicability']['rtl_anchors']),
                        effective_skipped_weaknesses=normalized['skipped_weaknesses'],
                        ignored_annotation_count=len(normalized['ignored_skipped_weaknesses']))
            fraction = row.get('edit_constraints', {}).get('max_changed_fraction', 0.4)
            feedback = assess_mutation_plan(carrier, selection['mutation_plan'], EditBudget(fraction))
            item['planning_status'] = feedback['status']
            item['planning_reason'] = feedback.get('reason')
        items.append(item)
    assert ledger.read_bytes() == original, 'input ledger changed during replay'
    return {'protocol_version': SELECTION_PROTOCOL_VERSION,
            'input_ledger_sha256': hashlib.sha256(original).hexdigest(),
            'selectors': len(items), 'grounding_passes': sum(i['grounding_result'] == 'pass' for i in items),
            'historical_protocol_rejections_recovered': sum(i['grounding_result'] == 'pass' and
                i['historical_reason'] in {'unexplained_exploration', 'ungrounded_applicability'} for i in items),
            'transitions': dict(Counter(i['historical_reason'] + ' -> ' + i['grounding_result'] for i in items)),
            'model_calls': 0, 'simulation_runs': 0,
            'scope': 'Grounding and local plan checks only; not mutation admission or Blue evaluation.',
            'items': items}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--red-ledger', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, action='append', required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    inputs = {p.resolve() for p in [args.red_ledger, *args.manifest]}
    if args.report and (args.report.resolve() in inputs or args.report.suffix == '.jsonl'):
        parser.error('report must not replace an input or use a ledger .jsonl filename')
    report = replay(args.red_ledger, args.manifest)
    output = json.dumps(report, indent=2) + '\n'
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output)
    print(json.dumps({k: v for k, v in report.items() if k != 'items'}, indent=2))


if __name__ == '__main__':
    main()
