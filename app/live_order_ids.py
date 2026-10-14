def entry(trade_id,leg):return f"{trade_id}:entry:{leg}"
def entry_recovery(trade_id,venue,side):return f"{trade_id}:entry-recovery:{venue}:{side}"
def exit(trade_id,leg):return f"{trade_id}:exit:{leg}"
def exit_recovery(trade_id,venue,side):return f"{trade_id}:exit-recovery:{venue}:{side}"
