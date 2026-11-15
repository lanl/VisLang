# The Sieve language

This is where the forms you write in a spec are defined: `source`, `fields`,
`region`, `subsample`, `threshold`, `timesteps`, `compress`, `save` and
`render`.

Writing a form doesn't run anything. It describes one step of the result you
want. A spec is a short chain of these descriptions ending in `save` or
`render`, and only that last step makes Sieve do any work. Before it does,
Sieve checks the whole chain against your data, so a misspelled field or an
out-of-range region is caught in milliseconds.

The same description can be sent to a remote machine as plain data, so the
work happens next to your files.

- What each form does: [`instructions/dsl-reference.md`](../../instructions/dsl-reference.md)
- Worked specs: [`examples/`](../../examples/)
