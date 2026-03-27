#!/usr/bin/env python3
"""Visualize VTK XML Structured Grid (.vts) files.

Usage examples:
  python scripts/visualize_vts.py backcurve40_output.25000.vts
  python scripts/visualize_vts.py headcurve40_output.30000.vts --list-arrays
  python scripts/visualize_vts.py backcurve40_output.25000.vts --scalar temp
  python scripts/visualize_vts.py backcurve40_output.25000.vts --vector vel --glyph
  python scripts/visualize_vts.py backcurve40_output.25000.vts --scalar temp --screenshot out.png
"""

from __future__ import annotations

import argparse
import ctypes.util
import os
import sys
from pathlib import Path

import vtk


def _headless_backend_available() -> tuple[bool, str]:
    """Return whether this host has a plausible software/offscreen GL backend."""
    osmesa = ctypes.util.find_library("OSMesa")
    swrast_paths = [
        "/usr/lib64/dri/swrast_dri.so",
        "/usr/lib/x86_64-linux-gnu/dri/swrast_dri.so",
        "/usr/lib/dri/swrast_dri.so",
    ]
    swrast = any(Path(p).exists() for p in swrast_paths)

    if osmesa or swrast:
        return True, ""

    return (
        False,
        "No software OpenGL backend found (missing OSMesa and swrast_dri.so).",
    )


def _array_names(field_data: vtk.vtkFieldData) -> list[str]:
    names: list[str] = []
    for i in range(field_data.GetNumberOfArrays()):
        arr = field_data.GetArray(i)
        if arr is None:
            continue
        name = arr.GetName()
        if name:
            names.append(name)
    return names


def _print_dataset_summary(grid: vtk.vtkStructuredGrid) -> None:
    print(f"Points: {grid.GetNumberOfPoints()}")
    print(f"Cells: {grid.GetNumberOfCells()}")

    point_names = _array_names(grid.GetPointData())
    cell_names = _array_names(grid.GetCellData())

    print("Point arrays:")
    if point_names:
        for name in point_names:
            print(f"  - {name}")
    else:
        print("  (none)")

    print("Cell arrays:")
    if cell_names:
        for name in cell_names:
            print(f"  - {name}")
    else:
        print("  (none)")


def _configure_scalar_coloring(
    mapper: vtk.vtkDataSetMapper,
    grid: vtk.vtkStructuredGrid,
    scalar_name: str,
) -> None:
    point_data = grid.GetPointData()
    cell_data = grid.GetCellData()

    point_array = point_data.GetArray(scalar_name)
    cell_array = cell_data.GetArray(scalar_name)

    if point_array is not None:
        mapper.SetScalarModeToUsePointFieldData()
        mapper.SelectColorArray(scalar_name)
        mapper.SetScalarVisibility(True)
        mapper.SetUseLookupTableScalarRange(True)
        mapper.SetScalarRange(point_array.GetRange())
        return

    if cell_array is not None:
        mapper.SetScalarModeToUseCellFieldData()
        mapper.SelectColorArray(scalar_name)
        mapper.SetScalarVisibility(True)
        mapper.SetUseLookupTableScalarRange(True)
        mapper.SetScalarRange(cell_array.GetRange())
        return

    raise ValueError(
        f"Scalar array '{scalar_name}' was not found in point or cell arrays."
    )


def _make_glyph_actor(grid: vtk.vtkStructuredGrid, vector_name: str) -> vtk.vtkActor:
    arrow = vtk.vtkArrowSource()

    mask = vtk.vtkMaskPoints()
    mask.SetInputData(grid)
    mask.SetOnRatio(100)
    mask.RandomModeOn()

    glyph = vtk.vtkGlyph3D()
    glyph.SetInputConnection(mask.GetOutputPort())
    glyph.SetSourceConnection(arrow.GetOutputPort())
    glyph.SetVectorModeToUseVector()
    glyph.SetScaleModeToScaleByVector()
    glyph.OrientOn()
    glyph.SetScaleFactor(0.5)
    glyph.SetInputArrayToProcess(
        1,
        0,
        0,
        vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS,
        vector_name,
    )

    glyph_mapper = vtk.vtkPolyDataMapper()
    glyph_mapper.SetInputConnection(glyph.GetOutputPort())
    glyph_mapper.ScalarVisibilityOff()

    glyph_actor = vtk.vtkActor()
    glyph_actor.SetMapper(glyph_mapper)
    glyph_actor.GetProperty().SetColor(0.2, 0.2, 0.2)
    return glyph_actor


def _save_screenshot(render_window: vtk.vtkRenderWindow, path: Path) -> None:
    window_to_image = vtk.vtkWindowToImageFilter()
    window_to_image.SetInput(render_window)
    window_to_image.Update()

    writer = vtk.vtkPNGWriter()
    writer.SetFileName(str(path))
    writer.SetInputConnection(window_to_image.GetOutputPort())
    writer.Write()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Visualize .vts in VTK")
    parser.add_argument("file", type=Path, help="Path to .vts file")
    parser.add_argument(
        "--list-arrays",
        action="store_true",
        help="Print available point/cell arrays and exit",
    )
    parser.add_argument(
        "--scalar",
        type=str,
        default=None,
        help="Scalar array name for color mapping",
    )
    parser.add_argument(
        "--vector",
        type=str,
        default=None,
        help="Vector point array name for glyphs",
    )
    parser.add_argument(
        "--glyph",
        action="store_true",
        help="Overlay vector glyphs (requires --vector)",
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        default=None,
        help="Optional PNG path to save current view",
    )
    parser.add_argument(
        "--window-size",
        type=int,
        nargs=2,
        metavar=("W", "H"),
        default=[1280, 800],
        help="Render window size in pixels",
    )
    parser.add_argument(
        "--offscreen",
        action="store_true",
        help="Force offscreen rendering (no GUI window)",
    )
    parser.add_argument(
        "--no-interact",
        action="store_true",
        help="Render once and exit without starting interactive window",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    auto_headless = not has_display
    offscreen = args.offscreen or auto_headless
    no_interact = args.no_interact or offscreen or args.screenshot is not None

    if not args.file.exists():
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        return 2
    if args.glyph and not args.vector:
        print("Error: --glyph requires --vector <name>", file=sys.stderr)
        return 2

    reader = vtk.vtkXMLStructuredGridReader()
    reader.SetFileName(str(args.file))
    reader.Update()
    grid = reader.GetOutput()

    if grid is None or grid.GetNumberOfPoints() == 0:
        print("Error: failed to load structured grid or dataset is empty.", file=sys.stderr)
        return 1

    _print_dataset_summary(grid)
    if args.list_arrays:
        return 0

    if auto_headless and args.screenshot is None and not args.offscreen:
        print(
            "No graphical display detected. Skipping render to avoid X/EGL errors.\n"
            "Use --screenshot out.png for offscreen output, or run on a machine with DISPLAY set.",
            file=sys.stderr,
        )
        return 0

    if offscreen and auto_headless:
        ok, reason = _headless_backend_available()
        if not ok:
            print(
                "Headless render is unavailable on this host.\n"
                f"Reason: {reason}\n"
                "Use --list-arrays here, or run rendering on a machine with X/GL support.\n"
                "If you control the system, install Mesa software rendering (swrast) or OSMesa.",
                file=sys.stderr,
            )
            return 3

    mapper = vtk.vtkDataSetMapper()
    mapper.SetInputData(grid)
    mapper.ScalarVisibilityOff()

    if args.scalar:
        try:
            _configure_scalar_coloring(mapper, grid, args.scalar)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    actor = vtk.vtkActor()
    actor.SetMapper(mapper)

    renderer = vtk.vtkRenderer()
    renderer.SetBackground(0.97, 0.97, 0.98)
    renderer.AddActor(actor)

    if args.glyph and args.vector:
        if grid.GetPointData().GetArray(args.vector) is None:
            print(
                f"Error: vector array '{args.vector}' was not found in point arrays.",
                file=sys.stderr,
            )
            return 2
        renderer.AddActor(_make_glyph_actor(grid, args.vector))

    render_window = vtk.vtkRenderWindow()
    render_window.AddRenderer(renderer)
    render_window.SetSize(args.window_size[0], args.window_size[1])
    if offscreen:
        render_window.SetOffScreenRendering(1)

    interactor = None
    if not no_interact:
        interactor = vtk.vtkRenderWindowInteractor()
        interactor.SetRenderWindow(render_window)

    renderer.ResetCamera()
    render_window.Render()

    if args.screenshot:
        _save_screenshot(render_window, args.screenshot)
        print(f"Saved screenshot: {args.screenshot}")

    if interactor is not None:
        interactor.Start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())