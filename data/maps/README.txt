Offline Maps Data Layout
========================

The maps module reads local OpenStreetMap raster tiles from one of these sources:

1. Raster tile folders
   data/maps/tiles/{z}/{x}/{y}.png
   data/maps/tiles/{z}/{x}/{y}.jpg
   data/maps/tiles/{z}/{x}/{y}.jpeg
   data/maps/tiles/{z}/{x}/{y}.webp

2. A single MBTiles archive
   data/maps/base.mbtiles

Notes
-----
- The viewer is fully offline. It never fetches remote tiles.
- Small regional extracts work best for now.
- Keep OpenStreetMap attribution with any data you distribute.
