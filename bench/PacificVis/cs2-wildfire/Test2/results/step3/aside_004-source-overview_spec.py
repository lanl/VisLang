src = source('ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/wildfire-sl/mountain_headcurve80/')
view = timesteps(src, [4000, 9000, 10000, 11000, 12000, 13000, 14000, 15000, 16000, 17000, 18000, 19000, 20000, 21000, 22000, 23000, 24000, 25000, 26000, 27000, 28000, 29000, 30000, 31000, 32000, 33000, 34000, 35000, 36000, 37000, 38000, 39000, 40000, 41000, 42000, 43000, 44000, 45000])
view = fields(view, ['u', 'v', 'w', 'theta'])
save(view, '/Users/agoosh11/Projects/VisLang/.vislang/paraview/20261031-013204-652ebb/004-source-overview/out.vts')
