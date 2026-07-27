data = source("ssh://darwin/projects/autonomousvis/ashrestha/data/")

for step in (1, 4):
    save(
        subsample(
            fields(timesteps(data, step, step), ["x", "y", "z", "temperature"]),
            2,
        ),
        "/Users/ashrestha/Projects/VisLang/saved_results",
    )
