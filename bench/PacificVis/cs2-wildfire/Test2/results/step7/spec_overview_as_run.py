src = source('ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/wildfire-sl/mountain_headcurve80/')
view = fields(src, ['u', 'v', 'w', 'theta', 'rhof_1'])
view = subsample(view, 8)
save(view, '/Users/agoosh11/Projects/VisLang/.vislang/paraview/20261031-013204-652ebb/002-source-overview/out.vts')
