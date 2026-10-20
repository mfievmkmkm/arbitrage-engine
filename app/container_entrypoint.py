"""Select the Railway virtual-only launcher; preserve ordinary container startup."""

import os
import runpy


if __name__ == "__main__":
    if os.getenv("RAILWAY_ENVIRONMENT_ID"):
        from .storage_status import report
        report()
        runpy.run_module("app.railway_paper_server", run_name="__main__")
    else:
        runpy.run_module("app.main", run_name="__main__")
