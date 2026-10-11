"""Permanent delete, one item per step."""


def delete(step, cancellable):
    """Remove one file, link or empty folder. Never recursive, and never through a link."""
    step.source.delete(cancellable)
