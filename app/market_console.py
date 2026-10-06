def merged(runtime, limit=12):
    out = []
    for name, rows in runtime.latest.items():
        for x in rows:
            y = dict(x) if isinstance(x, dict) else dict(x.__dict__)
            if not any(k in y for k in ("hypothetical_edge", "net", "carry_pct")):
                continue
            edge = y.get("hypothetical_edge", y.get("net", y.get("carry_pct", 0)))
            out.append((float(edge), name, y))
    return sorted(out, key=lambda x: x[0], reverse=True)[:limit]


def render(runtime):
    rows = merged(runtime)
    out = ["<b>MARKET CONSOLE</b>", "<code>VERIFIED / PAPER OPPORTUNITIES</code>", ""]
    if not rows:
        return "\n".join(out + ["Нет текущих возможностей."])
    for i, (edge, name, x) in enumerate(rows, 1):
        out.append(
            f"<b>{i:02d}</b>  <code>{name}</code>  {x.get('symbol') or x.get('base','—')}\nNET/EDGE <b>{edge:+.3f}%</b>  ·  {x.get('exchange') or x.get('buy') or x.get('long_venue','')}"
        )
    return "\n\n".join(out)
