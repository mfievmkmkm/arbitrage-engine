import json
import os


class Stop:
    def __init__(self, path):
        self.path = str(path)
        self.stopped = True
        self.reason = "RESTART_DEFAULT_STOP"
        self.stop(self.reason)

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"stopped": self.stopped, "reason": self.reason}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def stop(self, reason="OPERATOR_STOP"):
        self.stopped = True
        self.reason = reason
        self.save()

    def resume(self, evidence=None):
        if evidence is None or not evidence.safe:
            return False
        self.stopped = False
        self.reason = ""
        self.save()
        return True

    def allow_new_entries(self):
        return not self.stopped
