"""Fixed-entry exit-only replay. Censored paths never count as new-policy closes."""
import argparse
import csv
import io
import json
import zipfile
from .dex_reference_policy import risk_plan, exit_reason


def replay(path):
    result = {'limitations': ['Original entries fixed; new entry/identity gates not reconstructed',
                              'No quotes exist after each original close',
                              'Original REST sampling cannot test new one-second exits',
                              'In-sample diagnostic only; no proof of profitability'], 'portfolios': {}}
    with zipfile.ZipFile(path) as archive:
        for mode in ('dex_channel', 'dex_bybit'):
            trades = [json.loads(r['payload']) for r in csv.DictReader(io.StringIO(archive.read(mode+'_trades.csv').decode()))]
            marks = {}
            for row in csv.DictReader(io.StringIO(archive.read(mode+'_marks.csv').decode())):
                marks.setdefault(int(row['position_id']), []).append(json.loads(row['payload']))
            closes, censored = [], []
            for p in trades:
                p['reference_policy'] = risk_plan(p['base_qty']*p['entry_price'], p['entry_edge'])
                p['best_net'] = 0
                for m in sorted(marks.get(p['id'], []), key=lambda x:x['ts']):
                    if m.get('funding_known'):
                        p['best_net'] = max(p['best_net'], m['net'])
                    reason = exit_reason(p, m, m.get('funding_known',False))
                    if reason:
                        closes.append(dict(id=p['id'],symbol=p['symbol'],reason=reason,net=m['net']))
                        break
                else:
                    censored.append(p['id'])
            result['portfolios'][mode] = dict(original_net=sum(p['net'] for p in trades),
                                              original_count=len(trades), simulated_closes=closes,
                                              censored_ids=censored, full_policy_net=None)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive')
    args=parser.parse_args()
    print(json.dumps(replay(args.archive),ensure_ascii=False,indent=2))
