#!/usr/bin/env python3
"""Teaser image: composite ParaView's transparent-background screenshot onto #1d1d1d.

    python teaser_background.py IN.png OUT.png

The screenshot (timestep 44, saved from ParaView with a transparent background)
is composited as premultiplied alpha (ParaView blends colour over black and keeps
coverage in alpha): out = rgb + bg*(1-a). Over black this is the screenshot itself.
"""
import sys
import numpy as np
import matplotlib.image as mpimg

src, out = sys.argv[1:3]
BG = np.array([0x1d, 0x1d, 0x1d]) / 255
a = mpimg.imread(src)
rgb, al = a[..., :3], a[..., 3:4]
mpimg.imsave(out, np.clip(rgb + BG * (1 - al), 0, 1))
print(out)
