def render(states):
 rows=["🧭 STRATEGIES"]
 for name,s in states.items():rows.append(f"{name}: scan={'ON' if s.scan else 'OFF'} • paper={'ON' if s.paper else 'OFF'} • live={'ON' if s.live else 'LOCKED'}")
 return "\n".join(rows)
