def render(m,p):
 t,o=m["train"],m["oos"];return f"""<b>REPLAY // VALIDATION</b>
<code>TRAIN vs OUT-OF-SAMPLE</code>

TRAIN
N {t['n']} · NET {t['net']:+.3f} · PF {t['pf']:.2f} · DD {t['max_dd']:.3f}

OOS
N {o['n']} · NET <b>{o['net']:+.3f}</b> · PF <b>{o['pf']:.2f}</b> · DD <b>{o['max_dd']:.3f}</b>

Promotion  <b>{'ELIGIBLE' if p['eligible'] else 'LOCKED'}</b>
Reasons    <code>{', '.join(p['reasons']) if p['reasons'] else '—'}</code>"""
