import vtk
import json


def loadParaViewColorMaps(filepath="ColorMaps.json"):
    """Load ParaView color maps from JSON file."""
    with open(filepath) as f:
        return json.load(f)


def getColorMapByName(colormaps, name):
    """Find a colormap by name from loaded ParaView colormaps."""
    for cm in colormaps:
        if cm.get("Name") == name:
            return cm
    available = [cm.get("Name") for cm in colormaps]
    raise ValueError(f"Unknown colormap '{name}'. Available: {available}")


def colorMapToTransferFunction(colormap):
    """
    Convert a ParaView colormap to VTK color transfer function.
    RGBPoints format: [scalar0, r0, g0, b0, scalar1, r1, g1, b1, ...]
    """
    ctf = vtk.vtkColorTransferFunction()
    points = colormap["RGBPoints"]
    for i in range(0, len(points), 4):
        scalar, r, g, b = points[i], points[i+1], points[i+2], points[i+3]
        ctf.AddRGBPoint(scalar, r, g, b)
    return ctf


def visualizeVolumeNrrd(config, colormaps=None):
    """
    Visualize a volume from an NRRD file with configurable transfer functions and lighting.

    config dict keys:
        file: str - path to NRRD file
        color: list of (value, (r, g, b)) tuples OR string name of ParaView colormap
        opacity: list of (value, opacity) tuples for opacity transfer function
        lighting: dict with ambient, diffuse, specular values
        cameraAdjustments: dict with elevation, azimuth values
    colormaps: optional preloaded colormaps from loadParaViewColorMaps()
    """
    # Load volume using VTK's NRRD reader
    reader = vtk.vtkNrrdReader()
    reader.SetFileName(config["file"])
    reader.Update()
    print('Loaded volume with dimensions:', reader.GetOutput().GetDimensions())

    # Build color transfer function
    color_config = config["color"]
    if isinstance(color_config, str):
        # Load colormap by name
        if colormaps is None:
            colormaps = loadParaViewColorMaps()
        colormap = getColorMapByName(colormaps, color_config)
        colorTransferFunction = colorMapToTransferFunction(colormap)
    else:
        # Use explicit color points
        colorTransferFunction = vtk.vtkColorTransferFunction()
        for value, rgb in color_config:
            colorTransferFunction.AddRGBPoint(value, *rgb)

    # Build opacity transfer function
    opacityFunction = vtk.vtkPiecewiseFunction()
    for value, opacity in config["opacity"]:
        opacityFunction.AddPoint(value, opacity)

    # Volume properties
    volumeProperty = vtk.vtkVolumeProperty()
    volumeProperty.SetColor(colorTransferFunction)
    volumeProperty.SetScalarOpacity(opacityFunction)
    volumeProperty.SetInterpolationTypeToLinear()
    volumeProperty.ShadeOn()

    lighting = config.get("lighting", {})
    volumeProperty.SetAmbient(lighting.get("ambient", 0.2))
    volumeProperty.SetDiffuse(lighting.get("diffuse", 0.9))
    volumeProperty.SetSpecular(lighting.get("specular", 0.2))

    # Mapper
    volumeMapper = vtk.vtkGPUVolumeRayCastMapper()
    volumeMapper.SetInputConnection(reader.GetOutputPort())

    # Volume
    volume = vtk.vtkVolume()
    volume.SetMapper(volumeMapper)
    volume.SetProperty(volumeProperty)

    # Renderer and window
    renderer = vtk.vtkRenderer()
    renderer.AddVolume(volume)
    renderer.SetBackground(0.1, 0.1, 0.1)

    renderWindow = vtk.vtkRenderWindow()
    renderWindow.AddRenderer(renderer)
    renderWindow.SetSize(800, 800)

    # Camera adjustments
    renderer.ResetCamera()
    cameraAdjustments = config.get("cameraAdjustments", {})
    if "elevation" in cameraAdjustments:
        renderer.GetActiveCamera().Elevation(cameraAdjustments["elevation"])
    if "azimuth" in cameraAdjustments:
        renderer.GetActiveCamera().Azimuth(cameraAdjustments["azimuth"])
    renderer.GetActiveCamera().OrthogonalizeViewUp()

    # Interactive render window
    renderWindowInteractor = vtk.vtkRenderWindowInteractor()
    renderWindowInteractor.SetRenderWindow(renderWindow)

    # Use trackball camera style for intuitive mouse interaction
    style = vtk.vtkInteractorStyleTrackballCamera()
    renderWindowInteractor.SetInteractorStyle(style)

    # Start the interactive visualization
    renderWindow.Render()
    renderWindowInteractor.Start()

# Print out the color map options:
colormaps = loadParaViewColorMaps()
available_colormaps = [cm.get("Name") for cm in colormaps]
print("Available ParaView Color Maps:")
for name in available_colormaps:
    print(" - ", name)  

visualizeVolumeNrrd({
    "file": "skull.nhdr",
    "color": "Grayscale",
    "opacity": [
        (0, 0.0),
        (50, 0.0),
        (80, 0.05),
        (120, 0.3),
        (255, 0.8)
    ],
    "lighting": {
        "ambient": 0.2,
        "diffuse": 0.7,
        "specular": 0.3,
    },
    "cameraAdjustments": {
        "elevation": 90,
        "azimuth": 180,
    }
})