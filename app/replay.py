from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True)
class ReplayParams:
    target: float
    trailing: float
    max_seconds: int


def simulate(entry, marks, p):
    best = 0.0
    last = 0.0
    reason = "CENSORED"
    exit_seconds = None
    for ts, net, spread in marks:
        exit_seconds = ts
        last = net
        best = max(best, net)
        conv = 1 - spread / entry if entry > 0 else 0
        if conv >= p.target and net > 0:
            reason = "TARGET"
            break
        if best > 0 and net <= best * (1 - p.trailing):
            reason = "TRAILING"
            break
        if ts >= p.max_seconds:
            reason = "TIME_STOP"
            break
    return {
        "net": None if reason == "CENSORED" else last,
        "best": best,
        "reason": reason,
        "exit_seconds": None if reason == "CENSORED" else exit_seconds,
    }


def parameter_sets():
    return [
        ReplayParams(a, b, c)
        for a in (0.5, 0.6, 0.7, 0.8, 0.9)
        for b in (0.1, 0.15, 0.2, 0.25)
        for c in (180, 300, 600, 1200)
    ]


def metrics(trades, p):
    nets = [simulate(e, m, p)["net"] for e, m in trades]
    if not nets or any(n is None for n in nets):
        return None
    equity = peak = dd = 0.0
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    for n in nets:
        equity += n
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    pf = sum(wins) / abs(sum(losses)) if losses else (float("inf") if wins else 0.0)
    return {
        "trades": len(nets),
        "net": sum(nets),
        "avg": sum(nets) / len(nets),
        "median": median(nets),
        "win_rate": len(wins) / len(nets) * 100,
        "profit_factor": pf,
        "max_drawdown": dd,
    }


def portfolio_replay(trades):
    rows = []
    for p in parameter_sets():
        m = metrics(trades, p)
        if m:
            rows.append(
                {
                    "target": p.target,
                    "trailing": p.trailing,
                    "seconds": p.max_seconds,
                    **m,
                }
            )
    return sorted(rows, key=lambda x: (x["net"], -x["max_drawdown"]), reverse=True)


def walk_forward(trades, train_ratio=0.7, min_trades=10):
    if len(trades) < min_trades:
        return {"status": "insufficient_data", "sample_size": len(trades)}
    if not all(
        isinstance(x, dict)
        and {"opened_at", "closed_at", "marks", "entry_spread", "id"} <= x.keys()
        for x in trades
    ):
        return {
            "status": "window_metadata_required",
            "sample_size": len(trades),
            "release_authorized": False,
        }
    from .spot_future_history_replay import evaluate

    return evaluate(trades, strategy="futures_futures", train_ratio=train_ratio)


def ai_report(rows):
    if not rows:
        return {
            "status": "insufficient_data",
            "message": "Нет закрытых сделок с временным рядом.",
        }
    b = rows[0]
    return {
        "status": "research_only",
        "sample_size": b["trades"],
        "best_candidate": b,
        "warning": "Параметры не меняются автоматически.",
    }
