"""`sieve` against the simulated remote (tests/fakes_remote.py): the plugin's
requests cross the real process boundary and run the real CLI, planner,
executor and catalog; only ssh/rsync are faked. `u@fakehost:/abs/path` names
/abs/path on this machine. FAKE_NO_ALLOCATION=1 makes srun auto mode find no
Slurm allocation. Run through tests/paraview/fake_sieve (an executable)."""

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))

from fakes_remote import install_fake_remote, install_login_inspect  # noqa: E402

install_fake_remote()
install_login_inspect()
if os.environ.get("FAKE_NO_ALLOCATION") == "1":
    from vislang.remote import reduce
    reduce.allocation_status = lambda conn: {"present": False,
                                             "reason": "no RUNNING allocation"}

from vislang.server.cli import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
