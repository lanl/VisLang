import numpy as np
import copy


def compress(
    dataset_info,
    variables,
    error_bound,
    mode='auto'
):
    """
    Compress HDF5 variables using SPERR (float) or Zstd (int) via hdf5plugin.
    Uses an in-memory HDF5 file — no disk I/O.

    Args:
        dataset_info: DatasetInfo object with loaded HDF5 data
        variables:    List of variable names to compress
        error_bound:  Loss tolerance
                        'absolute': max |original - compressed|
                        'relative': max |original - compressed| / |original|
                        'precision': 16 or 32 for float16/float32 casting
        mode:         'auto' | 'absolute' | 'relative' | 'precision'

    Returns:
        New DatasetInfo with:
          .data[var]             — decompressed numpy arrays (ready to use)
          .compressed_bytes[var] — raw in-memory HDF5 bytes (ready to write to disk)
          .compression_info      — ratios, errors, methods per variable
    """
    if dataset_info.filetype != 'HDF5':
        raise ValueError(f"compress() only supports HDF5 datasets, got {dataset_info.filetype}")

    if not dataset_info.loaded:
        raise ValueError("Data must be loaded before compression. Call load() first.")

    if not isinstance(variables, list):
        variables = list(variables)

    invalid = set(variables) - set(dataset_info.data.keys())
    if invalid:
        raise ValueError(f"Variables not found in dataset: {invalid}")

    compressed_info = copy.deepcopy(dataset_info)
    compressed_info.compressed_bytes = {}
    compressed_info.compression_info = {
        'compressed': True,
        'variables': {},
        'total_original_size_mb': 0,
        'total_compressed_size_mb': 0,
    }

    for var in variables:
        original_data = dataset_info.data[var]

        if not np.issubdtype(original_data.dtype, np.number):
            print(f"Skipping non-numeric variable: {var}")
            continue

        var_mode = _decide_error_mode(original_data) if mode == 'auto' else mode
        if mode == 'auto':
            print(f"{var}: auto-selected '{var_mode}' mode")

        compressed_data, compressed_bytes, metadata = _compress_variable(
            original_data, error_bound, var_mode, var
        )

        compressed_info.data[var] = compressed_data
        if compressed_bytes is not None:
            compressed_info.compressed_bytes[var] = compressed_bytes

        compressed_info.compression_info['variables'][var] = metadata
        compressed_info.compression_info['total_original_size_mb'] += metadata['original_size_mb']
        compressed_info.compression_info['total_compressed_size_mb'] += metadata['compressed_size_mb']

    info = compressed_info.compression_info
    if info['variables']:
        total_ratio = info['total_original_size_mb'] / max(info['total_compressed_size_mb'], 1e-10)
        info['total_compression_ratio'] = total_ratio
        print(f"\nCompression complete — {len(info['variables'])} variable(s), "
              f"{total_ratio:.2f}x overall  "
              f"({info['total_original_size_mb']:.1f} MB → {info['total_compressed_size_mb']:.1f} MB)")

    return compressed_info


def _compress_variable(data, error_bound, mode, var_name):
    """
    Compress one variable. Returns (decompressed_array, compressed_bytes, metadata).
    compressed_bytes is None for precision mode (no HDF5 involved).
    Uses an in-memory HDF5 file (driver='core', backing_store=False).
    """
    import h5py
    import hdf5plugin

    original_size = data.nbytes / (1024 ** 2)
    metadata = {
        'original_dtype': str(data.dtype),
        'mode': mode,
        'error_bound': error_bound,
        'original_size_mb': original_size,
        'method': None,
    }

    # Precision mode: dtype casting only, no HDF5 needed
    if mode == 'precision':
        if error_bound == 32:
            compressed = data.astype(np.float32)
            metadata['compressed_dtype'] = 'float32'
        elif error_bound == 16:
            compressed = data.astype(np.float16)
            metadata['compressed_dtype'] = 'float16'
        else:
            raise ValueError(f"precision mode requires error_bound 16 or 32, got {error_bound}")
        metadata['method'] = 'dtype_reduction'
        metadata['compressed_size_mb'] = compressed.nbytes / (1024 ** 2)
        metadata['compression_ratio'] = original_size / metadata['compressed_size_mb']
        return compressed, None, metadata

    # Choose filter
    if np.issubdtype(data.dtype, np.floating):
        if mode == 'absolute':
            filter_kwargs = hdf5plugin.Sperr(absolute=float(error_bound))
        elif mode == 'relative':
            filter_kwargs = hdf5plugin.Sperr(
                peak_signal_to_noise_ratio=float(_error_bound_to_psnr(error_bound))
            )
        else:
            raise ValueError(f"Invalid mode for SPERR: {mode}")
        metadata['method'] = 'SPERR'
    else:
        filter_kwargs = hdf5plugin.Zstd(clevel=5)
        metadata['method'] = 'Zstd'

    # In-memory HDF5 round-trip — no disk I/O
    with h5py.File('_', 'w', driver='core', backing_store=False) as f:
        f.create_dataset('data', data=data, **filter_kwargs)
        compressed_bytes = bytes(f.id.get_file_image())
        compressed_data = f['data'][:]

    metadata['compressed_dtype'] = str(compressed_data.dtype)
    metadata['compressed_size_mb'] = len(compressed_bytes) / (1024 ** 2)
    metadata['compression_ratio'] = original_size / max(metadata['compressed_size_mb'], 1e-10)

    abs_error = np.abs(data.astype(np.float64) - compressed_data.astype(np.float64))
    metadata['max_absolute_error'] = float(np.max(abs_error))
    if mode == 'relative':
        metadata['max_relative_error'] = float(
            np.max(abs_error / (np.abs(data.astype(np.float64)) + 1e-10))
        )

    print(f"   {var_name}: {metadata['compression_ratio']:.1f}x  "
          f"(max error: {metadata['max_absolute_error']:.2e})")

    return compressed_data, compressed_bytes, metadata


def _decide_error_mode(data):
    data_min, data_max = np.min(data), np.max(data)
    data_abs = np.abs(data)

    if data_min < 0 and data_max > 0:
        return 'absolute'
    if np.any(data_abs < 1e-10):
        return 'absolute'

    data_range = data_max - data_min
    data_magnitude = np.mean(data_abs)
    if data_magnitude > 0:
        if data_range / data_magnitude < 10:
            return 'absolute'
        if data_range / data_magnitude > 100:
            return 'relative'

    return 'relative'


def _error_bound_to_psnr(relative_error_bound):
    return 20 * np.log10(1.0 / relative_error_bound)


def print_compression_summary(dataset_info):
    if not hasattr(dataset_info, 'compression_info') or not dataset_info.compression_info.get('compressed'):
        print("Dataset is not compressed")
        return

    info = dataset_info.compression_info
    print("\n" + "=" * 70)
    print("COMPRESSION SUMMARY")
    print("=" * 70)
    print(f"\nOverall:")
    print(f"  Ratio:          {info.get('total_compression_ratio', 0):.2f}x")
    print(f"  Original size:  {info['total_original_size_mb']:.2f} MB")
    print(f"  Compressed:     {info['total_compressed_size_mb']:.2f} MB")
    saved_pct = 100 * (1 - info['total_compressed_size_mb'] / max(info['total_original_size_mb'], 1e-10))
    print(f"  Saved:          {info['total_original_size_mb'] - info['total_compressed_size_mb']:.2f} MB ({saved_pct:.1f}%)")

    print(f"\nPer-variable:")
    print(f"  {'Variable':<30} {'Mode':<10} {'Method':<8} {'Ratio':<8} {'Max Error'}")
    print(f"  {'-'*30} {'-'*10} {'-'*8} {'-'*8} {'-'*12}")
    for var, meta in info['variables'].items():
        err = f"{meta.get('max_absolute_error', 0):.2e}" if 'max_absolute_error' in meta else "N/A"
        print(f"  {var:<30} {meta['mode']:<10} {meta['method']:<8} "
              f"{meta['compression_ratio']:<8.1f} {err}")
    print("=" * 70)
