"""Task-local progress snapshots for cancelled/failed PAPER checks."""
from contextvars import ContextVar

current = ContextVar('preflight_progress', default=None)


def stage(name, **facts):
    state=current.get()
    if state is not None:
        state.clear()
        state.update(stage=name,**facts)


def snapshot():
    return dict(current.get() or {})
