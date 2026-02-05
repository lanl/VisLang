import vtk

# Load volume using VTK's NRRD reader
reader = vtk.vtkNrrdReader()
reader.SetFileName('skull.nhdr')
reader.Update()
print('Loaded volume with dimensions:', reader.GetOutput().GetDimensions())

# Opacity transfer function (tuned for bone-like structures)
opacityTransferFunction = vtk.vtkPiecewiseFunction()
opacityTransferFunction.AddPoint(0, 0.0)
opacityTransferFunction.AddPoint(50, 0.0)
opacityTransferFunction.AddPoint(80, 0.05)
opacityTransferFunction.AddPoint(120, 0.3)
opacityTransferFunction.AddPoint(255, 0.8)

# Color transfer function
colorTransferFunction = vtk.vtkColorTransferFunction()
colorTransferFunction.AddRGBPoint(0,   0.0, 0.0, 0.0)
colorTransferFunction.AddRGBPoint(80,  0.9, 0.7, 0.6)
colorTransferFunction.AddRGBPoint(120, 1.0, 0.9, 0.8)
colorTransferFunction.AddRGBPoint(255, 1.0, 1.0, 1.0)

# Volume properties
volumeProperty = vtk.vtkVolumeProperty()
volumeProperty.SetColor(colorTransferFunction)
volumeProperty.SetScalarOpacity(opacityTransferFunction)
volumeProperty.SetInterpolationTypeToLinear()
volumeProperty.ShadeOn()
volumeProperty.SetAmbient(0.2)
volumeProperty.SetDiffuse(0.9)
volumeProperty.SetSpecular(0.2)

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

# Optional: adjust camera - front-on view
renderer.ResetCamera()
renderer.GetActiveCamera().Elevation(90)
renderer.GetActiveCamera().Azimuth(180)
renderer.GetActiveCamera().OrthogonalizeViewUp()
renderer.GetActiveCamera().Azimuth(90)


# Off-screen render to PNG
renderWindow.OffScreenRenderingOn()
renderWindow.Render()

windowToImageFilter = vtk.vtkWindowToImageFilter()
windowToImageFilter.SetInput(renderWindow)
windowToImageFilter.Update()

writer = vtk.vtkPNGWriter()
out_file = 'skull-vis.png'
writer.SetFileName(out_file)
writer.SetInputConnection(windowToImageFilter.GetOutputPort())
writer.Write()

print('Saved volume render to', out_file)