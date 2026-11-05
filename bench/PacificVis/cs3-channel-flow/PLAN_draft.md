# Case Study 3: Turbulent Channel Flow (Re_τ ≈ 5200) — author's draft plan (2026-11-03)

Kept verbatim as the starting point. Where it disagrees with what Sieve does today, see
`CS3_PROMPT.md`, "What Sieve does today vs. what the draft assumes".

## Part 1. Draft comments (from the .tex)

```latex
% Compute SSIM of reduced Data

% Data reduction (sampling) and attribute thresholding
% 4K^3 > 246^3 or 128^3 
% where x > 200 (200 would be a good value for that simulation)

% find different things by using different means of aggregation (max min mean) and we want to store that information.. that this data was created by doing a max aggegation like this.. and provenance record of Sieve can be recorded in the hdf5 data (with netcdf4) directly. 

% The scientist wants to find the most intense vortex cores in a 966 GB snapshot so they can study them at full resolution. A coarse overview is how they decide where to look. Vortex cores are a few cells across, so the usual way to make an overview (a stride, or a mean as in most multiresolution pyramids) misses them or averages them out. The overview then sends the scientist to the wrong place, or nowhere.

% This shows that the reduction operator is a visualization decision, not only a storage decision. The same idea appears in visualization-oriented aggregation work such as M4 for time series (Jugel et al., VLDB 2014)

% The case study then demonstrates three Sieve properties, each mapping to a paper contribution:

% The operator is stated in the spec. coarsen(src, 16, how="min") says exactly what the overview preserves (language).
% The cost of a better overview is shown before you pay it. A stride reads about 3.8 GB on the server; a coarsen reads all 966 GB. Both transfer the same 236 MB. sieve estimate makes this trade visible (runtime).
% The reduced data records what it is. This is where HDF5 comes in (provenance).

% Case Study 3 shows that a reduced view is only as useful as what its reduction preserves, and that Sieve makes that choice explicit, priced, and permanently attached to the data

% The CF conventions use this attribute to record that a value is, for example, a minimum over cells. If Sieve writes it in netCDF-4 output (e.g. x: y: z: minimum), tools that know nothing about Sieve can still read the meaning. Sieve's full provenance record sits alongside it.

% The full-resolution region needs it too. A 512³ chunk cut from a raw file loses where it came from. The provenance record keeps its index offset in the original grid, so it can be placed back in the domain.

% Data file format Conversion to NPZ / hdf5 with Provenance
```

---

## Part 2. Plan and narrative

### The narrative

**The pitch.** In data too large to move, a scientist uses a coarse overview to decide where to look. When the feature of interest is smaller than the reduction factor, the operator that builds the overview determines whether the feature is visible at all.

Case Study 3 follows a scientist looking for the most intense vortical events in a 966 GB channel-flow snapshot. The default overview (a stride) misses them, the standard pyramid level (a mean) blurs them away, and a block minimum keeps them. Sieve makes the operator an explicit part of the request, shows its cost before running it, reduces the data next to where it lives, reuses earlier results as the scientist narrows in, and records in the output file which operator produced it.

**The scientific question.** *Where are the most intense low-pressure (vortical) events in the Re_τ ≈ 5200 channel, and are they attached to the wall or spread through the outer flow?*

### Sources backing the question

Unlike the wildfire case, this question has direct precedent in the literature.

- **The question itself.** del Álamo, Jiménez, Zandonade & Moser (2006) analyzed clusters of intense vortices in channel-flow DNS at Re_τ up to 1900. They paid particular attention to tall clusters reaching from the logarithmic layer down to the near-wall region (below y⁺ = 20). Later work summarizes their finding: the wall-attached clusters are self-similar and statistically dominant in the logarithmic layer. Moser is a co-author on both this paper and the dataset.
- **Why this dataset.** Lee & Moser (2015) extend the Reynolds number range well beyond 1900, on a 10240 × 1536 × 7680 grid (streamwise × wall-normal × spanwise). The size of the data is what makes the question hard to ask at this Reynolds number, and that is Sieve's opening.
- **Pressure at this Re is itself of interest.** Panton, Lee & Moser (2017) studied pressure fluctuations using this DNS, with data up to Re_τ ≈ 5200.
- **Pressure minima as vortex markers, with the caveat.** Robinson (1991) defined vortical structures as low-pressure regions: in rotating motion, pressure tends to a local minimum on the rotation axis to balance the centrifugal force. Jeong & Hussain (1995) show the limits of a pure pressure-minimum criterion and propose λ₂ instead.

**Gap to state plainly.** del Álamo et al. identified vortices from velocity gradients, but our file contains only pressure. Present pressure minima as a first-pass locator of candidate regions, not as vortex identification. If the velocity files are also available, fetching velocity for the chosen region and computing λ₂ there would be a natural extension.

Cite the data as Lee & Moser (2015) plus the JHTDB dataset DOI (https://doi.org/10.7281/T1PV6HJV).

### Which intro claims CS3 can demonstrate

| Claim | Where it appears in CS3 | What to measure |
|---|---|---|
| Cost estimation | `estimate` on stride vs. `coarsen`: same output size, ~250× different server read | Predicted vs. actual bytes read, bytes transferred, time |
| Remote reduction | 966 GB reduced to 236 MB on the server; the file never moves | Bytes transferred vs. the 966 GB download |
| Semantic caching | Threshold applied to the cached overview; replay from provenance | Bytes moved (zero), time, cache hit |
| Provenance | netCDF-4/HDF5 output records operator, source, and region offset | Show the attributes; recover and re-run the spec |

**Fusion is weak in CS3; don't force it.** A scientist naturally wants the full minimum overview as a map, then applies thresholds to it, so fusing `threshold` into `coarsen` isn't a natural step here. Let the cosmology case (fields + region + timesteps + threshold in one spec, as in Fig. 1) carry the fusion claim.

### The steps

0. **Inspect.** Declare shape and dtype for the raw file. The published grid puts wall-normal at 1536. The filename lists 10240 × 7680 × 1536, which suggests the on-disk order is x, z, y. Confirm this before computing any read estimates.
1. **Cheap overview.** Run `sieve estimate` on `subsample(src, 16)`: about 3.8 GB read on the server, 236 MB transferred. Then run it.
2. **Ground truth.** Fetch a full-resolution block at the wall, 512 × 512 × 256 (about 0.5 GB). It shows many sharp pressure minima that the stride overview doesn't.
3. **Price the better overview.** `sieve estimate` on `coarsen(src, 16, how="min")` shows the same 236 MB transfer but a 966 GB server read. The scientist accepts the cost, and the coarsening runs next to the data. Run `how="mean"` as well, as the paper's comparison.
4. **Narrow down using the cache.** `threshold(coarsen(src, 16, how="min"), "p < T1")` is served from the cached overview: the cached result is a prefix of the new request, so Sieve applies only the remaining threshold and nothing moves. If too many candidate blocks remain, tightening to `T2 < T1` is also served from cache, because a looser threshold's result contains the stricter one's. This is the semantic caching claim in its most natural form.
5. **Extract and save.** Choose the region where the candidates cluster, run `region(...)` at full resolution, and save as netCDF-4 or HDF5 with the provenance record, a CF `cell_methods` attribute on the overviews, and the region's position as geometry.
6. **Reproduce from the file.** Recover the spec from the saved file's provenance and run it again. The cache serves it, and zero bytes move.

### Why save as HDF5 / netCDF-4

- **The three overviews are indistinguishable as files.** Same shape (640 × 480 × 96), same dtype, same 236 MB, but different meanings. A block-minimum pressure field is biased low compared with any pointwise pressure. Someone who opens it later and sets a colormap range or isovalue from full-resolution statistics will misread it. A raw file can't say "I am a 16³ block minimum"; an HDF5 or netCDF-4 file can.
- **The output becomes more self-describing than the source.** The original raw file has no metadata at all.
- **CF `cell_methods` is the standard for this.** Writing e.g. `x: y: z: minimum` in netCDF-4 output lets tools that know nothing about Sieve read the meaning. Sieve's full provenance record sits alongside it.
- **The full-resolution region needs its position.** A chunk cut from a raw file restarts its indices at zero and loses where it came from. That matters here because distance from the wall *is* the scientific question.
  - The provenance record makes the offset *recoverable*, but ParaView won't *use* it; ParaView positions data from geometry (image origin and spacing, or coordinate arrays).
  - So Sieve should also write the position as geometry: an origin for `.vti`, coordinate arrays for HDF5/netCDF-4 (ideally the stretched wall-normal grid from Lee & Moser).
  - Paper phrasing, if Sieve writes the geometry: *"Sieve's output keeps the region's position in the source grid, both as geometry that visualization tools use directly and in the provenance record that reproduces it."* If it writes only the provenance attribute, say only that the offset is recoverable from the record.

### On SSIM

SSIM is worth computing, but it may rank the reductions in the opposite order from feature preservation. Compare each overview with the full-resolution block (e.g. on wall-parallel slices). A block mean will probably score highest, since it best preserves the overall appearance, while it loses most of the cores.

If the measurement confirms this, it is a stronger result than SSIM alone: **the standard image-fidelity metric would pick the wrong overview for this task.** That supports the claim that the reduction operator is a visualization decision. Report SSIM next to core recall rather than on its own.

The block minimum's recall is 100% by construction (if any cell in a block is below the threshold, so is the block's minimum). For min, report what the guarantee costs instead: localization only to within a 16-cell block, and values biased low. Fix the definition of "core" before measuring, e.g. local minima below −k·p_rms in the ground-truth block.

### Caveats to state in the paper

- **Stretched wall-normal grid.** "Do events cluster near the walls" is a statement in physical space. In index space, near-wall blocks are physically much thinner than those at the centre, so a picture in grid indices exaggerates the near-wall region. Options: attach the published wall-normal grid as coordinates when saving, and/or coarsen less along the wall-normal axis (e.g. 16 × 16 × 4, about 944 MB) if `coarsen` takes per-axis factors. Check which y⁺ range the first few coarse layers cover.
- **Two full reads.** Mean and min are separate requests, so the server reads 966 GB twice. Computing several aggregates in one pass is a fusion opportunity to name as future work.
- **Pressure minima as a vortex proxy.** Cite Robinson (1991) and Jeong & Hussain (1995); the latter discusses why a pressure minimum is an imperfect vortex criterion. Fine for a systems paper: it's the scientist's chosen feature, and Sieve's job is to preserve it.
- **Precision.** Output stays float64 because `compress` is out of scope. One line.

### Baselines

- Download the whole file: 966 GB.
- Scripts on the server: numpy over a memory map, block reduction by hand. Count lines and check their output against Sieve's, as for the wildfire case.

### Things to check in Sieve before writing

1. **Cache containment across `coarsen`.** Does the cache serve `threshold(coarsen(...))` from a cached `coarsen(...)`? The draft says the cache applies remaining reductions to a cached superset; confirm it works for this operator.
2. **`threshold` output.** Masked grid or list of cells? This affects how the candidate blocks look in ParaView.
3. **What the saved files contain.** Does Sieve write the region's offset as geometry, and `cell_methods` for the overviews? If not, add them or scale back the provenance claim.
4. **The note "4K³ → 246³ or 128³, where x > 200".** This doesn't match this dataset; it may be left over from an isotropic 4096³ idea. For pressure, set the threshold relative to the fluctuations (e.g. −k·p_rms, from the ground-truth block). Also check whether the stored pressure includes a mean component that varies with wall distance.
5. **Axis order.** Confirm once the download finishes. A wall-normal profile of pressure variance should be symmetric about the centreline and peak near both walls.

### References

- del Álamo, J. C., Jiménez, J., Zandonade, P. & Moser, R. D. (2006). Self-similar vortex clusters in the turbulent logarithmic region. *J. Fluid Mech.* 561, 329–358. doi:10.1017/S0022112006000814
- Lee, M. & Moser, R. D. (2015). Direct numerical simulation of turbulent channel flow up to Re_τ ≈ 5200. *J. Fluid Mech.* 774, 395–415.
- JHTDB, Turbulent Channel Flow at Re_τ = 5200. doi:10.7281/T1PV6HJV
- Panton, R. L., Lee, M. & Moser, R. D. (2017). Correlation of pressure fluctuations in turbulent wall layers. *Phys. Rev. Fluids* 2, 094604. doi:10.1103/PhysRevFluids.2.094604
- Jeong, J. & Hussain, F. (1995). On the identification of a vortex. *J. Fluid Mech.* 285, 69–94. doi:10.1017/S0022112095000462
- Robinson, S. K. (1991). *The kinetics of turbulent boundary layer structure.* PhD dissertation, Stanford University.
- Jugel, U., Jerzak, Z., Hackenbroich, G. & Markl, V. (2014). M4: A visualization-oriented time series data aggregation. *Proc. VLDB Endow.* 7(10).
