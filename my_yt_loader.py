# my_yt_loader.py
"""
Pure data loader for cosmology HDF5 files into yt.
No side effects, no printing - just data loading.
"""

import yt
import h5py
import numpy as np
from pathlib import Path
from astropy.cosmology import FlatLambdaCDM
import astropy.units as u


def load_my_cosmology_data(filename):
    """
    Load custom HDF5 cosmology format into yt with proper cosmological parameters.
    
    Parameters
    ----------
    filename : str or Path
        Path to HDF5 file (e.g., NVB_C009_l10n512_S12345T692_z42.hdf5)
    
    Returns
    -------
    ds : yt Dataset
        Dataset with cosmological parameters properly set in ds.parameters
    """
    
    filename = Path(filename)
    
    # Extract redshift from filename
    redshift = _extract_redshift_from_filename(filename)
    
    # Load HDF5 data and metadata
    with h5py.File(filename, 'r') as f:
        box_size, cosmology_params = _read_metadata(f)
        data = _read_field_data(f)
        shape = data[('gas', 'temperature')].shape
    
    # Set up cosmology
    h0 = cosmology_params.get('hubble_constant', 0.7)
    omega_m = cosmology_params.get('omega_matter', 0.3)
    omega_l = cosmology_params.get('omega_lambda', 0.7)
    
    # Calculate cosmological time
    cosmo = FlatLambdaCDM(H0=h0*100, Om0=omega_m)
    age_universe = cosmo.age(redshift).to(u.Gyr).value
    lookback_time = cosmo.lookback_time(redshift).to(u.Gyr).value
    
    # Create yt dataset
    bbox = np.array([[0, box_size]] * 3)
    ds = yt.load_uniform_grid(
        data, 
        shape,
        length_unit=(box_size, 'Mpc'),
        bbox=bbox,
        nprocs=1,
        periodicity=(True, True, True)
    )
    
    # Set cosmological parameters on dataset
    _set_cosmology_parameters(ds, redshift, age_universe, h0, omega_m, omega_l)
    
    # Store additional derived quantities
    scale_factor = 1.0 / (1.0 + redshift)
    hubble_z = h0 * np.sqrt(omega_m * (1+redshift)**3 + omega_l)
    
    ds.parameters.update({
        'current_redshift': redshift,
        'current_time_gyr': age_universe,
        'lookback_time_gyr': lookback_time,
        'hubble_constant': h0,
        'omega_matter': omega_m,
        'omega_lambda': omega_l,
        'box_size': box_size,
        'cosmological_simulation': 1,
        'scale_factor': scale_factor,
        'hubble_parameter_z': hubble_z,
    })
    
    return ds


def _extract_redshift_from_filename(filename):
    """Extract redshift from filename like NVB_C009_l10n512_S12345T692_z42.hdf5"""
    parts = filename.stem.split('_')
    for part in parts:
        if part.startswith('z'):
            try:
                return float(part[1:])
            except ValueError:
                pass
    return 0.0


def _read_metadata(hdf5_file):
    """Read box size and cosmology parameters from HDF5 file"""
    box_size = 10.0  # Default
    if 'domain' in hdf5_file and hasattr(hdf5_file['domain'], 'attrs'):
        box_size = hdf5_file['domain'].attrs.get('box_size', 10.0)
    
    cosmology_params = {}
    if 'universe' in hdf5_file and hasattr(hdf5_file['universe'], 'attrs'):
        cosmology_params = {
            'hubble_constant': hdf5_file['universe'].attrs.get('hubble_constant', 0.7),
            'omega_matter': hdf5_file['universe'].attrs.get('omega_matter', 0.3),
            'omega_lambda': hdf5_file['universe'].attrs.get('omega_lambda', 0.7),
        }
    
    return box_size, cosmology_params


def _read_field_data(hdf5_file):
    """Load all field data from HDF5 file"""
    return {
        ('gas', 'temperature'): hdf5_file['native_fields/temperature'][:],
        ('gas', 'density'): hdf5_file['native_fields/baryon_density'][:],
        ('gas', 'velocity_x'): hdf5_file['native_fields/velocity_x'][:],
        ('gas', 'velocity_y'): hdf5_file['native_fields/velocity_y'][:],
        ('gas', 'velocity_z'): hdf5_file['native_fields/velocity_z'][:],
        ('stream', 'dark_matter_density'): hdf5_file['native_fields/dark_matter_density'][:],
    }


def _set_cosmology_parameters(ds, redshift, age_universe, h0, omega_m, omega_l):
    """Set cosmological parameters on yt dataset"""
    ds.cosmological_simulation = 1
    ds.current_redshift = redshift
    ds.current_time = ds.quan(age_universe, 'Gyr')
    ds.hubble_constant = h0
    ds.omega_matter = omega_m
    ds.omega_lambda = omega_l