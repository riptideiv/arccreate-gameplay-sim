"""Execution policy, separate from planner options and serialized plan semantics."""

import os
import warnings

# Enabled after focused near-side parity, worker determinism and full Arghena replay checks.
NATIVE_AUTO_ENABLED = True


def validate_execution(search_backend="auto", search_workers=0):
    if search_backend not in ("auto", "python", "native"):
        raise ValueError("search_backend must be auto, python, or native")
    if (
        isinstance(search_workers, bool)
        or not isinstance(search_workers, int)
        or search_workers < 0
    ):
        raise ValueError("search_workers must be a non-negative integer")
    return search_backend, search_workers


def worker_count(requested=0):
    validate_execution("auto", requested)
    available = getattr(os, "process_cpu_count", os.cpu_count)() or 1
    return requested or min(4, max(1, available - 1))


def plan(
    occupations,
    projection,
    options,
    multi,
    progress=None,
    *,
    search_backend="auto",
    search_workers=0,
    keep_checkpoints=False,
    checkpoint=None,
):
    from . import multi_finger

    validate_execution(search_backend, search_workers)
    backend, reason, native = "python", None, None
    if search_backend == "native" or (search_backend == "auto" and NATIVE_AUTO_ENABLED):
        try:
            from . import native_search as native
        except ImportError as error:
            reason = f"native extension unavailable: {error}"
        else:
            if not native.availability():
                reason = getattr(
                    native,
                    "unavailable_reason",
                    lambda: "native extension is not installed",
                )()
            else:
                reasons = native.unsupported_reasons(
                    occupations, projection, options, multi
                )
                if reasons:
                    reason = "; ".join(reasons)
                else:
                    backend = "native"
    elif search_backend == "auto":
        reason = "native auto rollout is pending validation"
    workers = worker_count(search_workers) if backend == "native" else 1

    def report(event):
        if progress:
            progress(
                {
                    **event,
                    "search_backend": backend,
                    "search_workers": workers,
                    "requested_backend": search_backend,
                    **({"fallback_reason": reason} if reason else {}),
                }
            )

    report({"phase": "backend", "completed": None, "total": None})
    if search_backend == "native" and backend == "python" and progress is None:
        warnings.warn(
            f"Native search unavailable; using Python: {reason}",
            RuntimeWarning,
            stacklevel=2,
        )
    if backend == "native":
        try:
            return native.plan_items(
                occupations,
                projection,
                options,
                multi,
                progress=report,
                workers=workers,
                keep_checkpoints=keep_checkpoints,
                checkpoint=checkpoint,
            )
        except native.NativeUnsupported as error:
            # Only explicit capability failures fall back. Numerical errors,
            # cancellation and no-path failures must never be hidden.
            backend, workers, reason = "python", 1, str(error)
            if progress is None:
                warnings.warn(
                    f"Native search unsupported; using Python: {reason}",
                    RuntimeWarning,
                    stacklevel=2,
                )
            report({"phase": "backend", "completed": None, "total": None})
    return multi_finger._plan_python(
        occupations,
        projection,
        options,
        multi,
        progress=report,
        keep_checkpoints=keep_checkpoints,
        checkpoint=checkpoint,
    )
