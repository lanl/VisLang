# Rendering (headless)

The compute nodes have **no usable OpenGL/X** ("bad X server connection"). This
shapes the entire rendering design.

## Use k3d for volumes, not trame/vtk.js
- The trame/PyVista `render_server.py` ships geometry to vtk.js, which needs VTK
  to export the scene server-side — that needs a GL window the node lacks.
  Result on these nodes: a **blank screen**. Do not use it for volumes here.
- **k3d** does WebGL ray-marching entirely in the browser from a raw numpy
  array — zero server-side GL. This is the working path and the default for 3-D
  fields.

## How it is wired
- `my_render.render_volume(info, cmap=None, opacity=None)` builds a k3d volume
  (`log10(|arr|+1)` scaling), serializes a self-contained snapshot
  (`plot.get_snapshot()`), and publishes it to a **persistent background HTTP
  server** on `VISLANG_RENDER_PORT` (default `127.0.0.1:8080`).
- The `render` form lowers (via the interpreter in `planner.py`) to
  `my_render.render`, which routes any dataset with a 3-D field to
  `my_render.render_volume`; particle/point data still uses `scene.build_scene` +
  `render_server` (kept, but not headless-capable yet).
- The viewer lives in the long-running MCP process, so it stays up across
  renders; re-rendering replaces the page in place.

## Controlling the look — from the spec
- `cmap='green'` (custom glowing-green ramp), or any matplotlib name, or `None`.
- `opacity=<k3d flat [t,a,…]>` to override the transfer function.
- Stride big grids with `subsample(node, N)` (or crop with `region(node, …)`)
  before rendering — k3d ships the whole array to the browser (256³ float32 ≈
  67 MB), so downsample for responsiveness. (`compress()` won't help here — render
  uses the full-res decompressed array.)

## Viewing from a laptop (SSH tunnel)
The server binds `127.0.0.1:8080` on the compute node. Forward it **from the
laptop** (not from the node):
```
ssh -L 8080:localhost:8080 <user>@<node> -J <user>@<login-host>
```
then open http://localhost:8080/.

## Gotchas
- Editing `vislang/output/render.py` / `vislang/server/mcp_server.py` does NOT hot-reload the running MCP
  server — reconnect it (`/mcp`) to load changes.
- Only one process can hold port 8080; kill stray viewers before re-rendering.
