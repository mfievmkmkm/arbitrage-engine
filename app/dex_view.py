def render(state,acceptance_failed=()):return "⛓ DEX\nState: %s\nLIVE: LOCKED\nAcceptance: %s"%(state,"OK" if not acceptance_failed else ", ".join(acceptance_failed))
