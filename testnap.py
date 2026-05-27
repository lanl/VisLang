# Save as test_napari.py
import napari
import numpy as np

print("Creating test data...")
data = np.random.random((50, 50, 50))

print("Creating viewer...")
viewer = napari.Viewer()
print("✓ Viewer created!")

print("Adding layer...")
viewer.add_image(data, name='Test')
print("✓ Layer added!")

print("Starting napari...")
napari.run()