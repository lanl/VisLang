save(
    subsample(
        fields(
            timesteps(
                source("ssh://darwin/projects/autonomousvis/ashrestha/data"),
                6,
                8,
            ),
            ["x", "y", "z", "temperature", "density"],
        ),
        2,
    ),
    "/Users/ashrestha/Projects/VisLang/saved_results",
)
