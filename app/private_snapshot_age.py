from dataclasses import dataclass
@dataclass(frozen=True)
class SnapshotAge:
 trusted:bool
 reason:str
def check(now,snapshot_ts,max_age=3):
 if snapshot_ts is None:return SnapshotAge(False,"PRIVATE_SNAPSHOT_TIMESTAMP_MISSING")
 return SnapshotAge(now-snapshot_ts<=max_age,"OK" if now-snapshot_ts<=max_age else "PRIVATE_SNAPSHOT_STALE")
