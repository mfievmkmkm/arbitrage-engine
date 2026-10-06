def render(commit,stage5_green,strategies):return "🚀 RELEASE\nCommit: %s\nStage5 CI: %s\nStrategies: %s\nAUTO: LOCKED"%(commit,"GREEN" if stage5_green else "UNKNOWN",", ".join(strategies))
