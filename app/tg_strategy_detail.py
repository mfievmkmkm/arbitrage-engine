def render(name,state,stats=None):
 stats=stats or {};return (f"<b>{name}</b>\n<code>STRATEGY CONTROL</code>\n\n"
 f"SCAN   {'🟢' if state.get('scan') else '⚫'}\nPAPER  {'🟢' if state.get('paper') else '⚫'}\nREAL   {'🟢' if state.get('real') else '🔒'}\n\n"
 f"Observations  <b>{stats.get('observations',0)}</b>\nAvg edge      <b>{stats.get('avg_edge',0):+.3f}%</b>\nBest edge     <b>{stats.get('best_edge',0):+.3f}%</b>\n\n"
 f"Promotion: <code>{state.get('promotion','LOCKED')}</code>")
