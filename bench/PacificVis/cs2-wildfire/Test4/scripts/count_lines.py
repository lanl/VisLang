#!/usr/bin/env python3
"""Lines of code each route needs: non-blank lines that are not comments or
docstrings (Python) and not comments (shell, Sieve specs).

    python count_lines.py FILE...      -> one line per file, then the total
"""
import io
import sys
import tokenize


def py_lines(path):
    src = open(path).read()
    code = set()
    prev = tokenize.INDENT
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.STRING and prev in (tokenize.INDENT, tokenize.NEWLINE,
                                                   tokenize.DEDENT, tokenize.NL):
            prev = tok.type            # a docstring / bare string statement
            continue
        if tok.type not in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
                            tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER):
            code.update(range(tok.start[0], tok.end[0] + 1))
        prev = tok.type
    return len(code)


def sh_lines(path):
    return sum(1 for ln in open(path) if ln.strip() and not ln.strip().startswith("#"))


total = 0
for p in sys.argv[1:]:
    n = sh_lines(p) if p.endswith(".sh") else py_lines(p)
    total += n
    print(f"{n:4d}  {p}")
print(f"{total:4d}  total")
