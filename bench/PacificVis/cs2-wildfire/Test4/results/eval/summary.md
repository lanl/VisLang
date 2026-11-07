# Case Study 2, Test4: numbers from the evaluation pass

- Sieve overview front vs evaluation stride 8: {'timesteps': 88, 'max_abs_diff_m': 0, 'presence_mismatch': 0}
- crossing (step): full 17763, Sieve s8 18020, every 5th 17811 (bracket (13000, 18000))
- interval speeds, full: {'8000-17000': 21.75, '17000-22000': 8.43, '22000-45000': 2.7, '45000-91000': 1.91}
- interval speeds, Sieve s8: {'8000-17000': 21.22, '17000-22000': 10.44, '22000-45000': 2.76, '45000-91000': 2.04}
- levels below 12 m: overview [0, 8], full [0, 1, 2, 3, 4, 5, 6, 7, 8]
- ambient u upwind (not used: near-ground flow at the upwind edge is weak): {'level': 6, 'height_m': 9.996, 'mean_all': -0.08, 'mean_window': -0.07}

## Stride sweep

- stride 1: {'fire_timesteps_found': 83, 'fire_timesteps_full': 83, 'false_fire': 0, 'front_err_median_m': 0.0, 'front_err_mean_m': 0.0, 'front_err_p90_m': 0.0, 'front_err_max_m': 0.0, 'grid_spacing_m': 2, 'within_one_spacing_pct': 100.0, 'crossing_step': 17763, 'points_per_timestep': 18300000, 'raw_bytes_series': 32647200000}
- stride 2: {'fire_timesteps_found': 83, 'fire_timesteps_full': 83, 'false_fire': 0, 'front_err_median_m': 0.57, 'front_err_mean_m': 0.69, 'front_err_p90_m': 1.46, 'front_err_max_m': 1.6, 'grid_spacing_m': 4, 'within_one_spacing_pct': 100.0, 'crossing_step': 17764, 'points_per_timestep': 2325000, 'raw_bytes_series': 4147800000}
- stride 4: {'fire_timesteps_found': 83, 'fire_timesteps_full': 83, 'false_fire': 0, 'front_err_median_m': 1.44, 'front_err_mean_m': 2.04, 'front_err_p90_m': 4.2, 'front_err_max_m': 8.64, 'grid_spacing_m': 8, 'within_one_spacing_pct': 98.8, 'crossing_step': 18009, 'points_per_timestep': 300000, 'raw_bytes_series': 535200000}
- stride 8: {'fire_timesteps_found': 83, 'fire_timesteps_full': 83, 'false_fire': 0, 'front_err_median_m': 4.35, 'front_err_mean_m': 6.24, 'front_err_p90_m': 11.35, 'front_err_max_m': 36.58, 'grid_spacing_m': 16, 'within_one_spacing_pct': 91.6, 'crossing_step': 18020, 'points_per_timestep': 37800, 'raw_bytes_series': 67435200}
- stride 16: {'fire_timesteps_found': 69, 'fire_timesteps_full': 83, 'false_fire': 0, 'front_err_median_m': 18.59, 'front_err_mean_m': 38.48, 'front_err_p90_m': 88.47, 'front_err_max_m': 275.58, 'grid_spacing_m': 32, 'within_one_spacing_pct': 65.2, 'crossing_step': 19906, 'points_per_timestep': 4864, 'raw_bytes_series': 8677376}

## u ahead of the front (strip)

- 0-10m_ahead_levels1-4: first negative #37000, negative 6/37
- 0-60m_ahead_levels1-4: first negative #34000, negative 11/37
- 60-120m_ahead_levels1-4: first negative #21000, negative 25/37
- 0-10m_ahead_level0: first negative #19000, negative 27/37
- 0-60m_ahead_aloft_13.8-18.2m: first negative #22000, negative 23/37

## Lee ground: {'strip_9000-20000': {'negative': 1, 'of': 12, 'mean_u': 0.63}, 'flanks_9000-20000': {'negative': 7, 'of': 12, 'mean_u': 0.19}, 'strip_21000-45000': {'negative': 25, 'of': 25, 'mean_u': -0.77}, 'flanks_21000-45000': {'negative': 0, 'of': 25, 'mean_u': 1.24}}

