import h5py

def explore_hdf5(f, prefix=''):
    """Recursively explore HDF5 structure"""
    for key in f.keys():
        item = f[key]
        if isinstance(item, h5py.Group):
            print(f"{prefix}📁 {key}/")
            explore_hdf5(item, prefix + "  ")
        else:
            print(f"{prefix}📄 {key}: shape={item.shape}, dtype={item.dtype}")

# Open and explore
with h5py.File('/Users/ashrestha/Documents/cosmologyData/512/NVB_C009_l10n512_S12345T692_z42.hdf5', 'r') as f:
    explore_hdf5(f)