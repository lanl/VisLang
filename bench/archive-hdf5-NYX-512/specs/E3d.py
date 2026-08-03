# E3d: render() over a timeseries (remote folder)
render(subsample(fields(timesteps(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series"), 0, 2), ["native_fields/temperature"]), 4))
