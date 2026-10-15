"""Read-only diagnostics for compact audit ZIP. No exchange/network calls."""
import csv, io, json, sys, zipfile
from collections import Counter
from pathlib import Path

FILES = ("decisions.csv", "dex_paired_decisions.csv", "dex_channel_decisions.csv", "dex_bybit_decisions.csv", "dex_virtual_decisions.csv")

def analyze(path):
    result = {"source": Path(path).name, "decisions": {}, "limitations": [
        "Some decision exports are capped at 5000 records.",
        "CSV alone cannot prove scanner correctness or profitable execution."
    ]}
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read('manifest.json')) if 'manifest.json' in archive.namelist() else {}
        tables = manifest.get('tables', {})
        coverage = {title.lower().replace(' ', '_') + '.csv': info for title, info in tables.items()}
        for name in FILES:
            if name not in archive.namelist():
                continue
            rows = csv.DictReader(io.StringIO(archive.read(name).decode("utf-8-sig")))
            reasons, actions = Counter(), Counter()
            for row in rows:
                reasons[row.get("reason") or "UNSPECIFIED"] += 1
                actions[row.get("action") or "UNSPECIFIED"] += 1
            count = sum(reasons.values())
            info = coverage.get(name, {})
            result["decisions"][name] = {
                "count": count, "possibly_capped": info.get('truncated', count == 5000),
                "total_rows": info.get('total_rows'),
                "reasons": dict(reasons.most_common()), "actions": dict(actions)
            }
        if 'scan_cycles.csv' in archive.namelist():
            states, counters = Counter(), Counter()
            latest = None
            count = 0
            for row in csv.DictReader(io.StringIO(archive.read('scan_cycles.csv').decode('utf-8-sig'))):
                count += 1
                states[row.get('status') or 'UNSPECIFIED'] += 1
                summary = json.loads(row['payload'])
                counters.update(summary.get('counters', {}))
                if latest is None or summary['ts'] > latest['ts']:
                    latest = summary
            result['scan_cycles'] = dict(count=count, statuses=dict(states),
                                        sample_counters=dict(counters), latest=latest,
                                        coverage=coverage.get('scan_cycles.csv', {}))
        else:
            result['scan_cycles'] = dict(available=False,
                limitation='No scan-cycle journal in this export; zero opportunities cannot establish that scanning worked.')
    return result

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python tools/audit_paper_export.py export.zip")
    print(json.dumps(analyze(sys.argv[1]), ensure_ascii=False, indent=2))
