"""python -m tools.strategy_lab_replay --help. Offline, Paper only."""
import argparse
import asyncio
import json
from pathlib import Path
from app.strategy_catalog import CATALOG
from app.strategy_lab import Lab
from app.strategy_math import D, Invalid
from app.strategy_lab_examples import snapshot


async def replay(path, db_path, *, mode='observe', enabled=(), values=None, inventory=None):
    # File contents cannot change mode/capital. Operator arguments select them.
    clock = [0]
    lab = Lab(db_path, clock=lambda:clock[0])
    await lab.init()
    clock[0] = D(lab.state['last_ts'])
    await lab.configure(mode=mode, enabled=list(enabled), values=values, inventory=inventory)
    results = []
    with Path(path).open(encoding='utf-8') as source:
        for number, line in enumerate(source, 1):
            if len(line) > 500000:
                raise Invalid('REPLAY_LINE_TOO_LARGE')
            if not line.strip():
                continue
            event = json.loads(line)
            clock[0] = D(event['ts'])
            try:
                result = await lab.process(event, now=clock[0])
            except (ValueError, KeyError, TypeError) as exc:
                raise Invalid(f'REPLAY_LINE_{number}:{exc}') from exc
            results.append(dict(line=number, **result))
    return lab, results


def main():
    parser = argparse.ArgumentParser(description='Изолированный Paper/replay всех стратегий; реальных заявок нет.')
    parser.add_argument('--input', type=Path)
    parser.add_argument('--db', type=Path, default=Path('strategy-lab.sqlite3'))
    parser.add_argument('--mode', choices=('observe','auto'), default='observe')
    parser.add_argument('--enable', nargs='*', choices=list(CATALOG), default=[])
    parser.add_argument('--inventory', type=Path, help='JSON: venue, asset_id, quantity, price; виртуальные запасы')
    parser.add_argument('--policy',type=Path,help='JSON с параметрами риска/horizon, валюта задаётся явно')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--example', choices=list(CATALOG), help='Вывести синтетический шаблон JSONL, не рынок')
    args = parser.parse_args()
    if args.example:
        content=json.dumps(dict(id='example-1',type='snapshot',ts=1000,snapshot=snapshot(args.example)),ensure_ascii=False)+'\n'
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(content,encoding='utf-8')
            print('Синтетический шаблон записан; это не рыночный сигнал.')
        else:
            print(content,end='')
        return
    if not args.input or not args.output:
        parser.error('--input и --output обязательны для replay')

    async def run():
        args.db.parent.mkdir(parents=True, exist_ok=True)
        inventory = json.loads(args.inventory.read_text(encoding='utf-8')) if args.inventory else None
        values=json.loads(args.policy.read_text(encoding='utf-8')) if args.policy else None
        lab, results = await replay(args.input, args.db, mode=args.mode, enabled=args.enable, inventory=inventory,values=values)
        report = await lab.export()
        report['results'] = results
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Paper: {len(results)} событий; закрыто {lab.state["closed_count"]}; NET {lab.state["realized"]}; открыто {len(lab.positions)}')
    asyncio.run(run())


if __name__ == '__main__':
    main()
