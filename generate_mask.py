import json
from PIL import Image, ImageDraw
import numpy as np

# Load GeoJSON
with open('sdr-dashboard/src/region.json', 'r') as f:
    geojson = json.load(f)

# Image dimensions (power of 2 for GPU)
WIDTH, HEIGHT = 1024, 1024

# Create black image
img = Image.new('L', (WIDTH, HEIGHT), 0)
draw = ImageDraw.Draw(img)

# Extract bounds
min_lon, max_lon, min_lat, max_lat = 180, -180, 90, -90

def process_polygon(coords, scale_x, scale_y, offset_x, offset_y):
    """Convert lon/lat to pixel coordinates and draw"""
    points = []
    for lon, lat in coords:
        px = int((lon - min_lon) * scale_x + offset_x)
        py = int((lat - min_lat) * scale_y + offset_y)
        points.append((px, py))
    if len(points) > 2:
        draw.polygon(points, fill=255)

# Find bounds first
for feature in geojson['features']:
    geom = feature['geometry']
    if geom['type'] == 'Polygon':
        for ring in geom['coordinates']:
            for lon, lat in ring:
                min_lon = min(min_lon, lon)
                max_lon = max(max_lon, lon)
                min_lat = min(min_lat, lat)
                max_lat = max(max_lat, lat)
    elif geom['type'] == 'MultiPolygon':
        for poly in geom['coordinates']:
            for ring in poly:
                for lon, lat in ring:
                    min_lon = min(min_lon, lon)
                    max_lon = max(max_lon, lon)
                    min_lat = min(min_lat, lat)
                    max_lat = max(max_lat, lat)

# Add padding
padding = 0.05
lon_span = max_lon - min_lon
lat_span = max_lat - min_lat
min_lon -= lon_span * padding
max_lon += lon_span * padding
min_lat -= lat_span * padding
max_lat += lat_span * padding

# Scale factors
scale_x = WIDTH / (max_lon - min_lon)
scale_y = HEIGHT / (max_lat - min_lat)
offset_x = 0
offset_y = 0

# Draw all polygons
for feature in geojson['features']:
    geom = feature['geometry']
    if geom['type'] == 'Polygon':
        for ring in geom['coordinates']:
            process_polygon(ring, scale_x, scale_y, offset_x, offset_y)
    elif geom['type'] == 'MultiPolygon':
        for poly in geom['coordinates']:
            for ring in poly:
                process_polygon(ring, scale_x, scale_y, offset_x, offset_y)

# Save
img.save('sdr-dashboard/public/region_mask.png')
print(f"Mask saved to sdr-dashboard/public/region_mask.png ({WIDTH}x{HEIGHT})")
