# Case Study 3: what the literature says (reference for writing Section 4.3)

Purpose: a single place to check, before writing a sentence about the science, what each
source actually claims, in its own words, and what our case may and may not say about it.
Compiled 2026-11-05 from full-text reads (unless marked otherwise) by two research passes.
Quotes are verbatim. Page numbers refer to the copy read (listed under each entry). "Derived"
marks numbers we computed; the source does not state them.

**Rules for using this file**
- Cite only what is quoted here as **FULL TEXT** or explicitly marked as safe. Entries marked
  **ABSTRACT ONLY** may be cited only for the abstract sentence given.
- Never write "0.06 % of events" or "69 % of events". Lenaers et al. count **points** (area),
  not events.
- Do not write "upstream tail" and attribute it to Lenaers et al. The phrase is Chen et al.'s
  summary of Lenaers. It does not appear in the Lenaers text.
- No prior backflow analysis at Re_τ ≈ 5200 was found (section 4). Frame our result as
  **extending a known question to a higher Reynolds number**, not as a first-ever result, unless
  Lakshmi Srinivas et al. (2024) is checked and found not to report it.

---

## 1. The question has precedent

**Our question:** where, and how often, does the near-wall flow reverse (u < 0) at Re_τ ≈ 5200,
and do reverse-flow events sit beneath large-scale high-speed or low-speed regions?

This is the question Lenaers et al. (2012) asked and answered for channel DNS up to
Re_τ = 1000. Cardesa et al. (2019) extended the statistics of backflow regions to Re_τ = 2003.
Both report that backflow becomes more common as Re_τ grows. Lenaers et al. tie this to
amplitude modulation by large scales, which "becomes progressively stronger as the Reynolds
number increases". That makes Re_τ ≈ 5200 (Lee & Moser 2015, 2.6× the highest Re_τ studied) a
natural next point. Our data are one snapshot of u only; the papers used many fields and full
velocity (and vorticity) fields.

---

## 2. The core sources

### 2.1 Lenaers, Li, Brethouwer, Schlatter & Örlü (2012). FULL TEXT

**Citation.** P. Lenaers, Q. Li, G. Brethouwer, P. Schlatter and R. Örlü, "Rare backflow and
extreme wall-normal velocity fluctuations in near-wall turbulence," *Phys. Fluids* **24**,
035110 (2012), doi:10.1063/1.3696304.
Copy read: https://www.mech.kth.se/~geert/papers/lenaers.pdf (published AIP version, 19 pp.).

**Data.**
- Channel DNS with SIMSON, "a fully spectral in-house code" (§II, p. 035110-2).
- Re_τ = 178.8, 586.4, 1000.2. At Re_τ = 1000: domain 8π × 2 × 3π, grid 2560 × 385 × 1920,
  (Δx⁺, Δy⁺max, Δz⁺) = (10, 8, 5), **2 instantaneous fields** (Table I, p. -3).
- "the data of one realisation of a velocity field is sufficient to ensure convergence"
  (appendix, p. -17).

**Definition.**
- At the wall, backflow is negative wall shear stress, "τw = μ∂u/∂y|wall" (§III.A, p. -4).
- Above the wall, u < 0: "the percentage at every wall-normal position is calculated by taking
  the ratio of the area in the DNS with negative velocity and the total area in a wall-parallel
  plane" (p. -5).
- **No connected-region labelling.** The shape comes from an ensemble average around events in a
  75 × 45 × 75 viscous-unit box (§IV.A, p. -8).

**Numbers (quote these exactly).**
- Fraction: "At Reτ = 180, approximately 0.01% of velocities are negative near the wall. This
  ratio rises to 0.05% at Reτ = 590 and to 0.06% at Reτ = 1000." (§III.A, p. -5). Abstract:
  "0.06% at the wall at Reτ = 1000".
- Reach: "up to y⁺ ≈ 4.5 at Reτ = 180, y⁺ ≈ 6.5 at Reτ = 590, and y⁺ ≈ 8.5 at Reτ = 1000" (p. -5).
- Size: "approximately circular with an average diameter of approximately 20 viscous units"
  (p. -8). "This size proved to be independent of the Reynolds number, so the widening of the
  negative tail … can only be explained by a rise in the number of regions of backflow" (p. -4).
- Trend: no law fitted. The parallel log-scale curves indicate "a constant scaling factor"
  (p. -6).

**Relation to large scales (the result our question compares with).**
- Method (§IV.D, p. -12):
  - "an instantaneous velocity field at Reτ = 1000";
  - backflow = "points with negative wall-shear stress";
  - large scales = streamwise velocity "at y⁺ ≈ 100", filtered with "a Gaussian low-pass filter";
  - width "larger than the size of the near-wall streaks, but smaller than the size of the
    large-scale structures". **No numerical width is given.**
- Result (p. -13): "**Most of the rare events occur below the large-scale motions with positive
  sign: 69% of the points with backflow** and 77% of the points with extreme wall-normal velocity
  are located underneath a positive large-scale motion."
- Explanation (p. -13): "even though the average u is positive in a positive large-scale
  structure, the fluctuations are large enough to ensure that most of the backflow also occurs
  within positive large-scale motions. In negative large-scale motions, the fluctuations do not
  need to be as large to realise backflow, which explains the remaining 31%".
- Locally: "Four regions of negative velocity are clearly visible and located in low-speed
  streaks" (p. -8). "backflow occurs predominantly in low-speed streaks" (p. -11). **Position:
  inside a low-speed streak, under a high-speed large-scale motion.**
- Reynolds-number argument: "The amplitude modulation effect becomes progressively stronger as
  the Reynolds number increases … This can explain why both kinds of rare events occur more
  often as the Reynolds number increases." (p. -13)

**Vortices.**
- "an inclined vortex lies on top of this region with an angle of about 45° in the xz-plane"
  (p. -8).
- These are "extremely strong versions of the oblique vortices discussed by Schoppa and Hussain"
  (p. -9).
- (We cannot test this: u only.)

**Resolution.**
- "higher resolution leads to slightly lower percentages of backflow" (p. -6).
- "the resolution of the 590m simulation is sufficient to resolve all phenomena" (p. -9); 590m is
  (10, 7, 5), close to ours (12.7, —, 6.4).
- Spatial averaging lowers the measured fraction: "There is a clear reduction of the percentage
  of backflow with increasing length of the sensing element" (p. -6).

**What we may say.**
- "At Re_τ = 1000, 0.06 % of the wall area has negative wall shear stress, and 69 % of the
  backflow points lie beneath positive large-scale motions at y⁺ ≈ 100 [Lenaers et al. 2012]."
- "Backflow regions are about 20 viscous units across [Lenaers et al. 2012; Cardesa et al. 2019]."

### 2.2 Cardesa, Monty, Soria & Chong (2019). FULL TEXT

**Citation.** J. I. Cardesa, J. P. Monty, J. Soria and M. S. Chong, "The structure and
dynamics of backflow in turbulent channels," *J. Fluid Mech.* **880**, R3 (2019),
doi:10.1017/jfm.2019.774. Open access.

**Data.**
- UPM channel DNS:
  - Re550: Re_τ 547, Δx⁺ 13, Δz⁺ 6.7, 142 fields;
  - Re950: Re_τ 934, 11, 5.7, 35 fields;
  - Re2000: Re_τ 2003, 12, 6.1, 60 fields;
  - plus a time-resolved Re950TR (2966 fields) (Table 1, p. R3-3).
- These spacings are **before dealiasing**. They "compute u on the dealiased grid obtained by
  zero-padding in Fourier space following the 3/2 rule", so their detection grid is 2/3 of these
  spacings (§2).

**Definition.**
- "We cluster the regions of the flow that fulfil u < 0 into labelled objects … connected grid
  points … The clustering algorithm probed all **26 neighbouring cells** for connections and
  accounted for the domain periodicity." (§2, p. R3-3)
- Size is measured by the rectangular bounding box.

**Numbers.**
- Attachment: "All objects found are attached to the wall in cases Re550, Re950 and Re950TR,
  while 99.96 % of objects in Re2000 are attached" (§4, p. R3-4).
- Mean bounding box ⟨Dx⁺⟩, ⟨Dz⁺⟩, ⟨Dy⁺⟩ (Table 2, p. R3-5):
  - Re550: 19.51, 19.92, 1.05
  - Re950: 19.98, 20.57, 1.09
  - Re2000: 19.06, 18.54, 1.12

  "Conditioned to Dy⁺ > 2, ⟨Dx⁺⟩ and ⟨Dz⁺⟩ are found to remain close to 33 and 41, respectively".
  The objects "resemble flattened separation bubbles" (p. R3-5).
- Density nA, "the mean number of backflow structures per million wall square units":
  1.39 (Re550), 1.71 (Re950), 1.77 (Re950TR), 1.97 (Re2000) (Table 1). No law fitted.
- Probability against height: only in Fig. 1(a), no numbers in the text. "Up to y⁺ = 4, all
  three cases support the trend of a higher proportion of flow reversal as the Reynolds number
  increases." (§3)
- Convection: "u⁺m = 9.4 ± 0.07 … the mean flow at y⁺ ≈ 12" (§5.2).
- Lifetimes "distributed similarly to log-normal"; **no mean lifetime is stated** (a secondary
  source's "≈ 7 wall units" is not in the paper).
- Rarely interact: split/merged volume fractions "2.7 % and 0.7 %" (§5.1).

**Relation to large scales.**
- **Not** classified as under high- or low-speed large-scale u.
- Instead, conditional spanwise vorticity: the signature "extends in the streamwise direction for
  at least 300 wall units, and they result from a complex interaction between regions of high
  and low spanwise vorticity far beyond the viscous sublayer" (abstract).
- "some statistical aspects of these near-wall events do not scale in viscous units; they are
  dependent on the Reτ-dependent dynamics further away from the wall" (abstract).
- Open: "It is very probable that the trend persists above this height, but we cannot remain
  conclusive" (§3).

**Resolution.**
- "small objects will be affected by the aspect ratio of the grid" (§4).
- 23–32 % of objects are a single cell long in x (Table 2, column a).

**What we may say.**
- "Backflow regions are wall-attached, about 20 viscous units across, and their number per wall
  area grows with Re_τ (to Re_τ = 2003) [Cardesa et al. 2019]."
- Do **not** cite Cardesa for the high/low-speed question.

### 2.3 Lee & Moser (2015), the data. FULL TEXT (arXiv v2)

**Citation.** M. Lee and R. D. Moser, "Direct numerical simulation of turbulent channel flow up
to Re_τ ≈ 5200," *J. Fluid Mech.* **774**, 395–415 (2015), doi:10.1017/jfm.2015.268
(arXiv:1410.7809).

- "A direct numerical simulation of incompressible channel flow at Reτ = 5186 has been
  performed" (abstract).
- Lx = 8πδ, Lz = 3πδ. Fourier-Galerkin in x and z, seventh-order B-spline collocation in y.
  "Nx = 10240 and Nz = 7680 … ∆x⁺ = 12.7 and ∆z⁺ = 6.4". First knot at ∆y⁺w = 0.498; centreline
  knot spacing 10.3. Ny = 1536. Re_b = 1.25 × 10⁵. Constant mass flux (§2, Table 1).
- "A distinct separation of scales exists between the large outer-layer structures and small
  inner-layer structures" (abstract). Spectral peaks "at kxδ = 40, y⁺ = 13 and one at kxδ = 1,
  y⁺ = 400" (§3.4).
- **The paper does not discuss backflow, reverse flow or negative wall shear** (full-text search).
- From the authors' profile files (turbulence.oden.utexas.edu):
  - peak ⟨u′u′⟩⁺ = 9.1428 at y⁺ = 15.745 (derived: u′rms⁺ ≈ 3.02);
  - U⁺ = 26.575 near the centreline.
  - **Our own check on the file agrees:** measured centre u/U_b = 1.103 (U_c⁺ ≈ 26.6), and peak
    std 0.127 U_b ≈ 3.06 u_τ at z = 24 (y⁺ 14.8). See `Test1/results/step0/`.

### 2.4 JHTDB (the distributor). Web pages read

- **Facts.**
  - "11 frames of data with 3 velocity components and pressure are stored in the database. The
    frames are apart from each other for around 0.7 flow-through time."
  - "data is stored at the wavemode resolution, i.e. Nx ×Ny ×Nz = 10240×1536×7680".
  - ν = 8 × 10⁻⁶, u_τ = 4.14872 × 10⁻², U_b = 1, "Centerline velocity: Uc ∼ 1.10",
    Re_τ = 5185.897 (Channel5200 page and README-CHANNEL5200.pdf).
- **No dataset DOI is given.** The doi:10.7281/T1PV6HJV in the author's draft plan could not be
  verified; do not use it.
- JHTDB's citing page asks for:
  - Li et al. (2008), *J. Turbulence* 9, No. 31;
  - Perlman et al. (SC07, 2007);
  - for channel data, Graham et al. (2016), *J. Turbulence* 17(2), 181–215,
    doi:10.1080/14685248.2015.1088656;
  - for Re_τ = 5200 data, also Lee & Moser (2015).

  For presentations: "Data obtained from the JHTDB at http://turbulence.pha.jhu.edu". Licence:
  ODC-By. README-CHANNEL5200.pdf has acknowledgement text.
- **Open SciVis Datasets** (where our file came from): its page could not be reached, so its
  description of the file (which variable, axis order) is unverified. Our step 0 verified the
  quantity and axes from the data and the JHTDB grid. Acknowledge the Open SciVis Datasets
  (Klacansky) as the distributor of the file we used.

---

## 3. Supporting sources

### 3.1 Mathis, Hutchins & Marusic (2009). FULL TEXT

*J. Fluid Mech.* **628**, 311–337, doi:10.1017/S0022112009006946. Boundary layers; wall-normal
coordinate written z.
- "the small-scale structures are subject to a high degree of amplitude modulation seemingly
  originating from the much larger scales that inhabit the log region … the amplitude modulation
  effect becomes progressively stronger as the Reynolds number increases" (abstract).
- Large scales: "The cutoff λx/δ = 1 appears from the map to be a reasonable location to
  separate the large- (above) and small-scale (below) components" (§3.2, p. 317).
- Sign: for a negative large-scale excursion "the amplitude of the small-scale fluctuations u⁺S
  is significantly reduced … for a positive large-scale excursion, the opposite scenario is true,
  and the amplitude of the small-scale fluctuations is increased" (pp. 317–318).
- Outer site: "a definition of the log region as 100 < z⁺ < 0.15Reτ, giving z⁺M = 3.9Reτ^1/2"
  (p. 333). Derived: 281 at Re_τ = 5186 (grid level z = 147).

### 3.2 Hutchins & Marusic (2007). ABSTRACT ONLY

*J. Fluid Mech.* **579**, 1–28, doi:10.1017/S0022112006003946.
- "very long meandering positive and negative streamwise velocity fluctuations, that we term
  'superstructures' … The length of these regions can commonly exceed 20δ."
- "the superstructure maintains a presence or footprint in the near-wall region, seeming to
  modulate or influence the near-wall cycle."
- The "z/δ = 0.06, λx = 6δ" outer-peak numbers are from a different paper (*Phil. Trans. R. Soc.
  A* 365, 2007). Don't attribute them to JFM 579.

### 3.3 Chen, Chung & Wan (2023). FULL TEXT

"Backflow structures in turbulent pipe flows at low to moderate Reynolds numbers," *J. Fluid
Mech.* **966**, A38, doi:10.1017/jfm.2023.461. Pipe Re_τ 180–1000, channel 180 and 500. Backflow
is "U < 0".
- "The backflow is caused by the joining of large-scale high- and low-speed structures in the
  vicinity of the wall and is formed at the tail tip of the low-speed structure" (abstract).
- "The backflow forms at the tail tip of a large-scale low-speed structure beneath a high-speed
  structure swept towards the wall" (A38-23).
- "average lifespan of 8 wall units"; convected at "approximately 10 wall units" (abstract).
- "at Reτ = 1000 … there will be over three backflow regions occurring in a squared area of δ²
  on the wall" (A38-10).

**Nuance for our question.** Chen et al. place backflow where high- and low-speed large scales
meet. Lenaers et al. place it under high-speed large scales. A block-level, single-height
classification (ours) sees the high-speed side only if the structure above is high-speed at the
reference height. State our classification exactly and avoid "contradicts".

### 3.4 Other measurements of how often (for comparison only)

- **Willert et al. (2018)**, *Exp. Therm. Fluid Sci.* **91**, 320–328,
  doi:10.1016/j.expthermflusci.2017.10.033. Read: arXiv v1 (1507.00609) only. Boundary layer,
  Re_τ = 1000 and 2700: "The probability of occurrence lies in the range of 0.01% to 0.1%";
  events "about 30-50 wall units in length and 5 wall units in height". It cites Hu et al.:
  "0.003% at Reτ = 90 increasing to 0.085% at Reτ = 1440".
- **Brücker (2015)**, *Phys. Fluids* **27**, 031705, doi:10.1063/1.4916768. FULL TEXT. Boundary
  layer, Re_τ ≈ 940, micropillars: backflow "at about 0.05% of the recorded images and are always
  linked to CPs" (critical points of wall shear stress).
- **Guerrero, Lambert & Chin (2020)**, *J. Fluid Mech.* **904**, A18, doi:10.1017/jfm.2020.689.
  ABSTRACT ONLY. Pipe, Re_τ ≈ 1000: "extreme events are clustered below large-scale structures of
  positive streamwise momentum that interact with near-wall low-speed streaks". (The third
  author is Chin, not Chernyshenko.)

---

## 4. Has anyone looked at backflow at Re_τ ≈ 5200?

- **Not found.** Lee & Moser do not discuss it. The highest-Re channel backflow statistics found
  are Cardesa et al. at Re_τ = 2003. The pipe DNS at Re_τ ≈ 5200 (Yao, Rezaeiravesh, Schlatter &
  Hussain, JFM 2023, arXiv 2207.06289; full text searched) has no backflow analysis.
- **To check before claiming novelty.** A. Lakshmi Srinivas, J. Zhang & R. Hu, "Parametrizing
  the probability density function of wall-shear stress in turbulent channel flows," *Phys. Rev.
  Fluids* **9**, 124604 (2024). It studies wall-shear-stress PDFs for Re_τ 180–5200; only the
  abstract was accessible. If its PDF reaches negative values, it reports the probability of
  negative wall shear stress at 5200, and our "how often" number would be a comparison, not a
  first.
- **Not found / do not cite:** a "Tsinghua group (Xu?)" channel paper on large-scale modulation
  of extreme events (a search snippet; could not be identified). Pan & Kwon (2018), *J. Phys.:
  Conf. Ser.* 1001, 012004 (search summary only: boundary layer, Re_τ = 1500, extreme *positive*
  wall shear).

---

## 5. How our measurements map onto theirs

| Their quantity | Theirs | Ours (CS3) | Comparable? |
|---|---|---|---|
| Fraction of wall area with backflow | Lenaers: 0.01 / 0.05 / 0.06 % at Re_τ 180 / 590 / 1000 (τ_w < 0 at the wall; u < 0 near it). Willert: 0.01–0.1 % (Re_τ 1000, 2700). Hu et al. via Willert: 0.085 % at 1440 | (a) **Ground-truth area:** exact fraction of cells with u < 0 at z = 1 (y⁺ 0.07) and per level up to y⁺ ≈ 68, over 84.9 million wall units². (b) **Whole walls:** the stride overview samples the z = 1 plane at every 16th cell (307,200 samples per wall), an unbiased estimate of the same fraction | Yes, with u < 0 at y⁺ = 0.07 standing in for τ_w < 0. Single snapshot. Our grid is close to Lenaers' adequate one; Lenaers notes finer grids give "slightly lower" fractions |
| Wall-normal reach | Lenaers: y⁺ ≈ 8.5 at Re_τ 1000 | Highest level with any u < 0 in the ground-truth area (levels to y⁺ 68) | Yes, within one area |
| Region size | ≈ 20 viscous units across (both); Dy⁺ ≈ 1 (Cardesa) | Bounding boxes of 26-connected regions in the ground-truth area | Partly: our x spacing (12.7) is 1.5× Cardesa's detection grid, so the smallest regions merge or vanish. Sizes biased up, counts biased down |
| Regions per area | Cardesa nA: 1.39–1.97 per million wall units² (Re_τ 547–2003) | Count of 26-connected regions in 84.9 million wall units² | Same caveat as above; report as a lower-resolution count |
| Backflow beneath high-speed large scales | Lenaers: 69 % of backflow **points** at the wall lie under positive large-scale u at y⁺ ≈ 100 (Gaussian low-pass, width unstated), one field at Re_τ 1000 | (a) **Whole walls, blocks:** fraction of backflow blocks (from the min overview) under u′_L > 0, with u′_L from the plane nearest y⁺ ≈ 100 (z = 81 / 1454), against the fraction of wall area with u′_L > 0. (b) **Ground-truth area, points:** fraction of backflow cells at z = 1 under u′_L > 0, the same measure as Lenaers (few large structures in this area: indicative only) | (b) same measure, small area; (a) a block-level analogue over the whole wall. Name the difference in the text |

**Large-scale filter (ours, fixed before measuring).** Lenaers gives no width. Mathis separates
large from small at λx = δ. Ours:
- a 2-D low-pass of u′ on the plane nearest y⁺ ≈ 100, keeping wavelengths λx > δ and λz > δ/4
  (spectral cut-off, sharp);
- sensitivity with λz > δ/8 and λz > δ/2;
- Mathis' outer site (y⁺ ≈ 281, z = 147) as a second reference height.

### 5b. What we measured (2026-11-05; sources in `CASE_STUDY_BRIEF.md` §8)

| Their quantity | Theirs | Ours, Re_τ 5186 |
|---|---|---|
| Fraction of wall area with backflow | Lenaers 0.06 % (Re_τ 1000); Willert 0.01–0.1 % | **0.0967 %** whole lower wall (stride, 95 % CI 0.0863–0.1083 %); 0.1265 % in the ground-truth area |
| Wall-normal reach | Lenaers y⁺ ≈ 8.5 | **y⁺ 9.67** (z = 18), ground-truth area |
| Region size | ≈ 20 viscous units across | bounding boxes **25.2 × 24.0** (biased up by the grid) |
| Regions per area | Cardesa 1.39–1.97 per million wall units² | **2.45** (26-connected; 2.66 with 6-connectivity) |
| Backflow beneath positive large-scale u at y⁺ ≈ 100 | Lenaers 69 % of points | **Pre-registered filter (λx > δ):** 54.0 % of area points, 55.3 % / 54.4 % of blocks (lower / upper wall); chance ≈ 50 %. **Post hoc, cut-off in viscous units (λx⁺ > 1000, Lenaers' δ):** 69.0 % of area points, 63.9 % / 62.9 % of blocks |

Safe wording for the last row: "With the large scales defined as at Re_τ 1000 in viscous units, the
fraction matches Lenaers' 69 %. With the cut-off at our δ, the association is weak at y⁺ ≈ 100 and
moderate further out (60–62 % of blocks at y⁺ 281 and 519)." The post-hoc results must be labelled
as such.

---

## 6. Sentences safe to use in Section 4.3 (adapt wording, keep the facts)

- "Near the wall, the streamwise velocity occasionally reverses. At Re_τ = 1000, 0.06 % of the
  wall area has negative wall shear stress [Lenaers et al. 2012], and the regions of reverse flow
  are wall-attached, about 20 viscous units across, and more numerous per unit area at higher
  Re_τ [Cardesa et al. 2019]."
- "Lenaers et al. found 69 % of backflow points beneath positive large-scale motions at
  y⁺ ≈ 100, which they relate to amplitude modulation of the near-wall cycle by the outer large
  scales [Mathis et al. 2009], an effect that strengthens with Re_τ."
- "Backflow statistics have been reported up to Re_τ = 2003 [Cardesa et al. 2019]; the Re_τ ≈ 5200
  simulation of Lee & Moser (2015) allows the question to be asked at 2.6 times that Reynolds
  number." (The Lakshmi Srinivas et al. check was dropped on 2026-11-05: no novelty is claimed, so
  the sentence stands without any "first".)
- "A region of reverse flow about 20 viscous units across covers about four cells of the Re_τ ≈
  5200 grid in the wall-parallel plane (Δx⁺ = 12.7, Δz⁺ = 6.4); a stride of 16 keeps one cell in
  256." (Derived; `Test1/results/step0/yplus_check.txt`.)

## 7. Not to claim

- That u < 0 events are vortices, or anything about their dynamics (one snapshot, u only).
- That our grid resolves the smallest backflow regions that Cardesa's dealiased grid resolves.
- That Lenaers counted events (they counted points), or used a stated filter width (they didn't).
- A dataset DOI for JHTDB channel5200 (none given).
- Novelty at Re_τ ≈ 5200 (none is claimed; decided 2026-11-05).
