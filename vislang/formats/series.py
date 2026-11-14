"""A timeseries' `#N` labels in a few words.

    describe_series([1000, 2000, …, 88000])
        -> "88 timesteps: #1000 to #88000, every 1000"
    describe_series([0, 500, …, 50000, 51000, …, 70000] minus 12500, 33000)
        -> "120 timesteps: #0 to #50000 every 500, then #51000 to #70000 every 1000
            missing #12500, #33000"

The labels are split into runs of a constant step; a single label missing from a
run is reported as missing rather than breaking the run; runs shorter than three
are listed one by one. Shared by `inspect` and the ParaView panel, so both
describe a series the same way. Standard library only: the ParaView plugin
loads this file on its own.
"""

_MAX_TOKENS = 8


def _runs(labels):
    """[(first, last, step, count)] — maximal runs of a constant step."""
    runs, i, n = [], 0, len(labels)
    while i < n:
        if i + 2 < n and labels[i + 1] - labels[i] == labels[i + 2] - labels[i + 1]:
            d = labels[i + 1] - labels[i]
            j = i + 2
            while j + 1 < n and labels[j + 1] - labels[j] == d:
                j += 1
            runs.append([labels[i], labels[j], d, j - i + 1])
            i = j + 1
        else:
            runs.append([labels[i], labels[i], 0, 1])
            i += 1
    return runs


def _bridge(runs):
    """Join two runs of one step separated by a single missing label."""
    out, missing = [], []
    for r in runs:
        if out:
            p = out[-1]
            d = p[2] or r[2]
            same = d and r[2] in (0, d) and p[2] in (0, d)
            if same and r[0] - p[1] == 2 * d and (p[3] > 1 or r[3] > 1):
                missing.append(p[1] + d)
                p[1], p[2], p[3] = r[1], d, p[3] + r[3]
                continue
            if same and r[0] - p[1] == d and p[2] == d and r[3] == 1:
                p[1], p[3] = r[1], p[3] + 1            # a straggler continuing the run
                continue
        out.append(list(r))
    return out, missing


def _tokens(runs):
    """("run", text) for a run of three or more; ("one", "#v") for each label
    of a shorter one."""
    out = []
    for first, last, step, count in runs:
        if count >= 3:
            out.append(("run", f"#{first} to #{last}" + ("" if step == 1
                                                         else f" every {step}")))
        else:
            out += [("one", f"#{v}") for v in ((first,) if count == 1 else (first, last))]
    return out


def describe_series(labels, noun="timesteps"):
    """The labels in at most three short lines (see the module docstring)."""
    labels = sorted(set(int(v) for v in labels))
    n = len(labels)
    if n == 0:
        return f"no {noun}"
    if n == 1:
        return f"1 {noun[:-1] if noun.endswith('s') else noun}: #{labels[0]}"
    runs, missing = _bridge(_runs(labels))
    toks = _tokens(runs)
    if len(toks) > 1 and n <= _MAX_TOKENS:
        # A few labels that are not one run read best as themselves.
        return f"{n} {noun}: " + ", ".join(f"#{v}" for v in labels)
    if len(toks) == 1:
        return f"{n} {noun}: " + toks[0][1].replace(" every", ", every") + \
            ("\nmissing " + ", ".join(f"#{v}" for v in missing) if missing else "")
    shown, more = toks[:_MAX_TOKENS], len(toks) - _MAX_TOKENS
    text = shown[0][1]
    for (k0, _), (k1, t1) in zip(shown, shown[1:]):
        text += (", then " if k0 == k1 == "run" else ", ") + t1
    head = f"{n} {noun}: {text}" + (f", … ({more} more)" if more > 0 else "")
    if missing:
        head += "\nmissing " + ", ".join(f"#{v}" for v in missing[:8]) + \
            (f", … ({len(missing) - 8} more)" if len(missing) > 8 else "")
    return head
