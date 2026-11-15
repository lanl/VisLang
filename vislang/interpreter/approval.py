"""What a run has been approved to do beyond its gates.

A run is held — nothing read, nothing moved — when its priced plan is over
budget, when the Slurm allocation it would step into is missing, or when the
route it planned cannot run and the only way left is to copy whole files. Each
hold names one decision, so each needs its own approval:

    budget              the priced plan may exceed the budget (`confirm=True`)
    without_allocation  go on with no Slurm allocation: the reduce cannot run
                        next to the data, so it falls back to a whole-file
                        fetch — which is priced and gated on its own
    fetch               a whole-file fetch the run FELL BACK to may exceed the
                        budget (`allow_fetch=True`)

Approving one never approves another. In particular `confirm=True` approves the
plan that was priced, not a fallback the run reaches afterwards: confirming a
12 GB reduce must not let a 2 TB copy of the source through when the reduce
turns out not to run.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Approval:
    budget: bool = False
    without_allocation: bool = False
    fetch: bool = False

    @classmethod
    def of(cls, value):
        """An Approval from what a caller passed: an Approval as-is, or the
        legacy `confirm` boolean (budget only)."""
        if isinstance(value, cls):
            return value
        return cls(budget=bool(value))
