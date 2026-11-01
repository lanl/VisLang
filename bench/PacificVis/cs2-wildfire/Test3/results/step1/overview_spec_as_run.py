src = source('ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/wildfire-sl/mountain_headcurve80/')
view = fields(src, ['rhof_1', 'theta', 'u', 'v', 'w'])
view = subsample(view, 8)
save(view, '/Users/agoosh11/Projects/VisLang/.vislang/paraview/20261031-175301-ba2a2a/004-source-overview/out.vts')
