save(
    subsample(
        fields(
            source("ssh://darwin/projects/autonomousvis/ashrestha/data/"),
            ["x", "y", "z", "temperature"],
        ),
        2,
    ),
    "/Users/ashrestha/Projects/VisLang/saved_results",
)
