"""Run one baseline script on the server and record what it cost there: wall time of the
script's own work and the bytes the process fetched from storage (Linux read_bytes), measured
from just before the script starts to just after it ends. Not part of the counted baseline.

    python run_measured.py <script.py> <args...>   ->  prints one JSON line
"""
import json
import runpy
import sys
import time


def read_bytes():
    for line in open("/proc/self/io"):
        if line.startswith("read_bytes:"):
            return int(line.split(":")[1])


script, args = sys.argv[1], sys.argv[2:]
sys.argv = [script] + args
io0, t0 = read_bytes(), time.perf_counter()
runpy.run_path(script, run_name="__main__")
t1, io1 = time.perf_counter(), read_bytes()
print(json.dumps({"script": script, "args": args, "server_s": round(t1 - t0, 3),
                  "read_bytes": io1 - io0,
                  "finished": time.strftime("%Y-%m-%dT%H:%M:%S")}))
