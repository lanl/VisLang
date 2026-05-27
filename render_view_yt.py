import yt
from my_yt_loader import load_my_cosmology_data


def print_cosmology_info(ds):
    """Pretty print all cosmological information"""
    
    print("\n" + "="*70)
    print("🌌 COSMOLOGICAL PARAMETERS")
    print("="*70)
    print(f"{'Redshift (z):':<30} {ds.current_redshift}")
    print(f"{'Age of universe:':<30} {ds.parameters['current_time_gyr']:.4f} Gyr")
    print(f"{'Lookback time:':<30} {ds.parameters['lookback_time_gyr']:.4f} Gyr")
    print(f"{'Scale factor (a):':<30} {ds.parameters['scale_factor']:.6f}")
    print(f"{'Hubble constant (h):':<30} {ds.hubble_constant}")
    print(f"{'H(z) [in units of H0]:':<30} {ds.parameters['hubble_parameter_z']:.2f}")
    print(f"{'Omega matter:':<30} {ds.omega_matter}")
    print(f"{'Omega lambda:':<30} {ds.omega_lambda}")
    print(f"{'Cosmological sim:':<30} {bool(ds.cosmological_simulation)}")
    print("="*70)


def print_simulation_info(ds):
    """Print simulation box and resolution information"""
    
    print("\n" + "="*70)
    print("📦 SIMULATION PARAMETERS")
    print("="*70)
    print(f"{'Box size (comoving):':<30} {ds.domain_width}")
    print(f"{'Box size (physical):':<30} {ds.domain_width / (1 + ds.current_redshift)}")
    print(f"{'Resolution:':<30} {ds.domain_dimensions}")
    print(f"{'Grid spacing (comoving):':<30} {ds.domain_width[0] / ds.domain_dimensions[0]:.4f}")
    print(f"{'Grid spacing (physical):':<30} {ds.domain_width[0] / ds.domain_dimensions[0] / (1 + ds.current_redshift):.6f}")
    print(f"{'Total cells:':<30} {ds.domain_dimensions.prod():,}")
    print("="*70)


def print_field_info(ds):
    """Print available fields"""
    
    print("\n" + "="*70)
    print("🔬 AVAILABLE FIELDS")
    print("="*70)
    
    print("\nNative fields (loaded from file):")
    for field in ds.field_list:
        print(f"  • {field}")
    
    print(f"\nDerived fields (computed by yt): {len(ds.derived_field_list)} available")
    print("  (Some examples:)")
    for field in list(ds.derived_field_list)[:10]:
        print(f"  • {field}")
    print(f"  ... and {len(ds.derived_field_list) - 10} more")
    print("="*70)


def print_dataset_summary(ds):
    """Print basic dataset information"""
    
    print("\n" + "="*70)
    print("📊 DATASET SUMMARY")
    print("="*70)
    print(f"{'Type:':<30} {type(ds).__name__}")
    print(f"{'String representation:':<30} {ds}")
    print(f"{'Current time:':<30} {ds.current_time}")
    print("="*70)


def main():
        # Load data (pure function, no side effects)
    ds = load_my_cosmology_data("/Users/ashrestha/Documents/cosmologyData/512/NVB_C009_l10n512_S12345T692_z42.hdf5")
    
    # Now do all the printing/display
    print_dataset_summary(ds)
    print_cosmology_info(ds)
    print_simulation_info(ds)
    print_field_info(ds)

if __name__ == '__main__':
    main()