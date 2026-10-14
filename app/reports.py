import json, math
from .spot_future_history_replay import build


async def build_replay_report(diary):
    report = await build(diary.path, strategy="futures_futures")
    rows = (
        [dict(report["parameters"], **report["train"]["metrics"])]
        if "parameters" in report
        else []
    )
    return rows, report


def compact_ai_json(report):
    def clean(x):
        if isinstance(x, float) and not math.isfinite(x):
            return None
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items()}
        if isinstance(x, (tuple, list)):
            return [clean(v) for v in x]
        return x

    return json.dumps(
        clean(report), ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
