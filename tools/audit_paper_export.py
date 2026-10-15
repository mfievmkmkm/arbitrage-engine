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
        for name in FILES:
            if name not in archive.namelist():
                continue
            rows = csv.DictReader(io.StringIO(archive.read(name).decode("utf-8-sig")))
            reasons, actions = Counter(), Counter()
            for row in rows:
                reasons[row.get("reason") or "UNSPECIFIED"] += 1
                actions[row.get("action") or "UNSPECIFIED"] += 1
            result["decisions"][name] = {
                "count": sum(reasons.values()), "possibly_capped": sum(reasons.values()) == 5000,
                "reasons": dict(reasons.most_common()), "actions": dict(actions)
            }
    return result

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python tools/audit_paper_export.py export.zip")
    print(json.dumps(analyze(sys.argv[1]), ensure_ascii=False, indent=2))
