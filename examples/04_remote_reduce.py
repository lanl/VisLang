# The data is on an HPC filesystem, so send the question to it.
#
# An `ssh://` source changes nothing about how you write the spec. What changes
# is where the work happens: the interpreter serializes the narrowing prefix to
# JSON — data, never code — ships it to a reducer running next to the files, and
# pulls back only the reduced result.
#
# Two things worth knowing before running this:
#
#   * Cost is predicted from metadata first. Call `estimate_render_cost` on the
#     source, or run this spec with the sink removed, to see the plan and the
#     predicted payload before anything is read.
#
#   * Remote reduction steps into a Slurm allocation that is ALREADY HELD. It
#     never creates one. If the run reports no allocation, start one yourself
#     and re-run:
#
#         ssh <host> 'salloc --no-shell -J vislang -N 1 -p <partition> -t 2:00:00'
#
#     `-J vislang` must match VISLANG_SRUN_NAME, and `--no-shell` holds the
#     allocation so every timestep can reuse it.

src = source("ssh://myhost/scratch/project/run042/output/particles.gio")

cut = fields(src, ["x", "y", "z", "rho"])
cut = threshold(cut, "rho > 1e12")
cut = subsample(cut, 10)

save(cut, "/abs/path/out/dense_particles.gio")
