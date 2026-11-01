| route | step | timesteps seen (overview) | over the wire | read on the server | lines of code written | wire bytes vs Sieve total |
|---|---|---|---|---|---|---|
| Sieve (ParaView) | overview: all timesteps, stride 8, 5 fields | 88 | 0.05 GB (47,138,910 B) | 96.62 GB | 0 | — |
| Sieve (ParaView) | Detail: box, full resolution, 38 timesteps, 4 fields |  | 0.36 GB (364,936,204 B) | 41.72 GB | 0 | — |
| Sieve (ParaView) | total | 88 | 0.41 GB (412,075,114 B) | 138.35 GB | 0 | — |
| Two-pass download | pass 1: every 5th file whole (18 files) | 18 | 19.76 GB (19,764,052,290 B) | 19.76 GB | 38 | 48.0× |
| Two-pass download | pass 2: window files not held (31 files), then cut |  | 34.04 GB (34,038,090,055 B) | 34.04 GB | 51 | 82.6× |
| Two-pass download | total (49 files) | 18 | 53.80 GB (53,802,142,345 B) | 53.80 GB | 89 | 130.6× |
| Download everything | all 88 files whole, then the same scripts | 88 | 96.62 GB (96,624,255,640 B) | 96.62 GB | 73 | 234.5× |
| Work on the server (mention only) | scripts run on gpu-server over all 88 files and the 38 window files; copy back the 38 cuts | 88 | 0.61 GB (606,297,192 B) | 138.35 GB | 72 | 1.5× |

Sieve's probes (a fixed 4 MiB bandwidth probe before each fetch): 8,388,608 B, not in the wire column.
Pass 1 files: #1000, #8000, #13000, #18000, #23000, #28000, #33000, #38000, #43000, #48000, #54000, #59000, #64000, #69000, #74000, #79000, #84000, #89000
Pass 2 files: #4000, #9000, #10000, #11000, #12000, #14000, #15000, #16000, #17000, #19000, #20000, #21000, #22000, #24000, #25000, #26000, #27000, #29000, #30000, #31000, #32000, #34000, #35000, #36000, #37000, #39000, #40000, #41000, #42000, #44000, #45000
Lines of code: non-blank, non-comment lines of the route's own data-handling scripts (the 74-line front/wind analysis, scripts/cs2_front.py, is the same in every route). Sieve's specs (4 + 5 lines) are written by the ParaView plugin, not by the user.
