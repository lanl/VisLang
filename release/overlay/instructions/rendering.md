# Rendering

`render` is headless. Sieve builds a k3d scene, embeds the data in a page, and
serves that page from a background HTTP server inside the MCP process. The
browser does all the drawing in WebGL, so rendering works on compute nodes
that have no display or GPU.

## Before you render

The whole selected array is sent to the browser. A 256³ float32 field is about
67 MB, and several fields multiply that. For any source you have not rendered
before:

1. Call `estimate_render_cost(path)`. It reads metadata only, and reports the
   browser payload, the disk read, and a recommendation.
2. Turn the recommendation into forms. The report phrases it in an older
   internal notation, so do not paste it into a spec:
   - `subset(info, dimensions={'grid': c})` means about `c` cells per axis.
     Write `subsample(node, s)`, where `s` is the largest axis length divided by
     `c`, rounded up. Alternatively, crop with `region` to the part they care
     about.
   - `subset(info, dimensions={'particles': f})` means keep a fraction `f`.
     Write `subsample(node, f)`.
   - `render(info)` means it fits. Render as is.
3. Mention the trade-off to the scientist: "I've strided by 4 to keep the first
   view responsive; we can zoom in with `region`."

For point data, a subsample reduces what the browser receives. It may not
reduce the disk read, and the report says when that is the case.

`compress` makes nothing cheaper here: the viewer gets the decompressed array.

## What renders

- A dataset with any 3-D field renders as a volume, on a log10(|v|+1) scale.
- Point data renders as a point cloud plus a density volume. The positions
  come from the variables `inspect` detected (override them with `positions=`).
- Only one dataset per render. A time series or named boxes are rejected:
  select one step with `timesteps(s, N, N)`, or `save` and open the result in
  ParaView.
- Running the spec again replaces the page in place. The URL stays the same.

## Controlling the look

Set it in the spec, where the scientist can see it:

- `cmap="green"` is a glowing green ramp. Any matplotlib name also works, and
  `None` uses the defaults.
- `opacity=[t0, a0, t1, a1, ...]` replaces k3d's opacity transfer function.

## Opening the viewer

The server listens on `127.0.0.1:9123` by default. `VISLANG_RENDER_HOST` and
`VISLANG_RENDER_PORT` change the address. Give the scientist the printed URL.

If the MCP server runs on a remote node, the scientist forwards the port from
their own laptop:

```console
ssh -L 9123:localhost:9123 <user>@<node> -J <user>@<login-host>
```

and opens `http://localhost:9123/`. Give them the command. You cannot open the
tunnel for them.

## When it goes wrong

- **A blank or frozen page** almost always means the payload is too large.
  Subsample further.
- **Port already in use:** another viewer holds the port. Set
  `VISLANG_RENDER_PORT`, or stop the stale process with the scientist's
  agreement.
