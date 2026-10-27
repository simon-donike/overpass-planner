import { Map, NavigationControl, Marker, Popup, setWorkerCount, setWorkerUrl, type GeoJSONSource } from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import 'maplibre-gl/dist/maplibre-gl.css';
import type { FeatureCollection } from 'geojson';
import type { SatellitePosition } from './types';

export async function createGlobe(select: (index: number) => void, failure: (message: string) => void) {
  setWorkerCount(1);
  setWorkerUrl(workerUrl);
  const map = new Map({
    container: 'globe', center: [1, 20], zoom: 0.9, maxZoom: 18,
    canvasContextAttributes: {antialias: true},
    style: {
      version: 8, projection: {type: 'globe'},
      sources: {osm: {type: 'raster', tileSize: 256, maxzoom: 19,
        tiles: [import.meta.env.VITE_TILE_URL || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
        attribution: import.meta.env.VITE_TILE_ATTRIBUTION || '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}},
      layers: [{id: 'earth', type: 'background', paint: {'background-color': '#d9e8fb'}}, {id: 'osm', type: 'raster', source: 'osm'}],
      sky: {'sky-color': '#10264d', 'horizon-color': '#7caff2', 'fog-color': '#d9e8fb', 'atmosphere-blend': 0.7},
    },
  });
  map.addControl(new NavigationControl(), 'top-right');
  map.getCanvas().setAttribute('aria-label', '3D globe. Arrow keys pan; plus and minus zoom.');
  await new Promise<void>((resolve, reject) => {
    // The style is entirely local; missing raster tiles never prevent drawing.
    map.once('style.load', () => resolve());
    map.once('error', event => { if (!map.isStyleLoaded()) reject(event.error); });
    map.once('webglcontextlost', () => reject(new Error('The graphics context was lost. Retry the globe when ready.')));
  }).catch(error => { map.remove(); throw error; });
  map.on('webglcontextlost', () => failure('The graphics context was lost. Retry the globe when ready.'));
  for (const id of ['roi', 'passes', 'tracks', 'night']) map.addSource(id, {type: 'geojson', data: empty()});
  map.addLayer({id: 'night', source: 'night', type: 'fill', paint: {'fill-color': '#10264d', 'fill-opacity': 0.32}});
  map.addLayer({id: 'track-past', source: 'tracks', type: 'line', filter: ['==', ['get', 'part'], 'past'], layout: {'line-cap': 'round'}, paint: {'line-color': ['get', 'colour'], 'line-width': 2, 'line-dasharray': [1, 3], 'line-opacity': 0.85}});
  map.addLayer({id: 'track-future', source: 'tracks', type: 'line', filter: ['==', ['get', 'part'], 'future'], paint: {'line-color': ['get', 'colour'], 'line-width': 2, 'line-opacity': 0.85}});
  map.addLayer({id: 'passes-fill', source: 'passes', type: 'fill', paint: {'fill-color': ['get', 'colour'], 'fill-opacity': ['case', ['get', 'selected'], 0.35, 0.1]}});
  map.addLayer({id: 'passes-line', source: 'passes', type: 'line', paint: {'line-color': ['get', 'colour'], 'line-width': ['case', ['get', 'selected'], 4, 1.5]}});
  map.addLayer({id: 'roi-fill', source: 'roi', type: 'fill', paint: {'fill-color': '#2563b9', 'fill-opacity': 0.15}});
  map.addLayer({id: 'roi-line', source: 'roi', type: 'line', paint: {'line-color': '#2563b9', 'line-width': 2}});
  map.addLayer({id: 'roi-point', source: 'roi', type: 'circle', filter: ['==', '$type', 'Point'], paint: {'circle-radius': 7, 'circle-color': '#2563b9', 'circle-stroke-color': '#fff', 'circle-stroke-width': 2}});
  map.on('click', 'passes-fill', event => { const index = event.features?.[0]?.properties.index; if (index !== undefined) select(Number(index)); });
  map.on('mouseenter', 'passes-fill', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', 'passes-fill', () => { map.getCanvas().style.cursor = ''; });
  const markers = new globalThis.Map<string, Marker>();
  return {
    resize: () => map.resize(),
    center: () => ({lng: map.getCenter().lng, lat: map.getCenter().lat, zoom: map.getZoom()}),
    move(lng: number, lat: number, zoom: number) { map.jumpTo({center: [lng, lat], zoom, bearing: 0, pitch: 0}); },
    wholeEarth() { map.jumpTo({zoom: 0.9, bearing: 0, pitch: 0}); },
    fit(bounds: [[number, number], [number, number]]) { map.fitBounds(bounds, {padding: 45, maxZoom: 10, duration: 0}); },
    data(roi: FeatureCollection, passes: FeatureCollection) {
      void (map.getSource('roi') as GeoJSONSource).setData(roi);
      void (map.getSource('passes') as GeoJSONSource).setData(passes);
    },
    tracks(data: FeatureCollection) { void (map.getSource('tracks') as GeoJSONSource).setData(data); },
    night(data: FeatureCollection) { void (map.getSource('night') as GeoJSONSource).setData(data); },
    positions(rows: SatellitePosition[], colours: Record<string, string>, details = true) {
      const names = new Set(rows.map(p => p.satellite));
      for (const [name, marker] of markers) if (!names.has(name)) { marker.remove(); markers.delete(name); }
      for (const row of rows) {
        let marker = markers.get(row.satellite);
        const description = `${row.satellite} · ${row.alt_km.toFixed(0)} km altitude · ${row.direction} · ${row.freshness} elements`;
        if (!marker) {
          const button = document.createElement('button'); button.type = 'button'; button.className = 'live-marker';
          button.style.setProperty('--satellite-colour', colours[row.satellite] || '#2563b9');
          marker = new Marker({element: button, opacityWhenCovered: 0}).setLngLat([row.lon, row.lat]).setPopup(new Popup().setText(description)).addTo(map);
          markers.set(row.satellite, marker);
        }
        marker.setLngLat([row.lon, row.lat]);
        if (details) {
          marker.getElement().setAttribute('aria-label', description); marker.getElement().title = description;
          marker.getPopup()?.setText(description);
        }
      }
    },
    destroy() { map.remove(); },
  };
}
function empty(): FeatureCollection { return {type: 'FeatureCollection', features: []}; }
