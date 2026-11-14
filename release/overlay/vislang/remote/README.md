# Working with remote data

If you point `source` at an `ssh://` path, the rest of the spec stays the same.
Sieve sends the request to the machine that holds the data, does the narrowing
there, and brings back only the result. Requests you have already made are
cached, so asking again, or asking for a little more, fetches only what is
new.

A few things to know:

- **The remote machine needs its own copy of Sieve.** Tell Sieve where to find
  it in `.vislang/hosts.json`.
- **Passwords stay private.** For hosts that need an interactive login,
  `sieve connect <host>` opens a password dialog on your screen. The password
  goes straight to `ssh`, never to Sieve or to an AI agent.
- **Sieve never books HPC time for you.** On a Slurm cluster, it works inside an
  allocation you already hold, and proposes the `salloc` command when there
  isn't one.
- **Narrow your request.** A spec that asks for a whole remote file copies the
  whole file.

Setup and details: [`instructions/remote.md`](../../instructions/remote.md)
