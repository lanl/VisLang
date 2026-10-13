# Planning and running a spec

This part of Sieve turns your spec into as little work as possible.

For each `save` or `render`, it:

1. reads the source's schema, without reading any data;
2. checks your spec against that schema;
3. combines crops, strides and field selection into a single read;
4. estimates the cost, and waits for your approval if the run is over budget;
5. reads the data and hands the result to `save` or `render`.

The order you write steps in sets what they mean: `threshold` then `subsample`
samples the cells that passed the threshold, while the reverse order applies
the threshold to a sample. Sieve keeps that meaning and still chooses its own
way to read the data.

A spec with no `save` or `render` stops after the estimate. Sieve shows you the
plan and reads nothing, so it is a cheap way to try an idea.

- Writing specs: [`instructions/authoring-specs.md`](../../instructions/authoring-specs.md)
- The `estimate` and `execute` commands: the main [README](../../README.md)
