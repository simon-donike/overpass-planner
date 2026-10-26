import L from 'leaflet';
import 'leaflet-draw';
import 'leaflet/dist/leaflet.css';
import 'leaflet-draw/dist/leaflet.draw.css';
import type { GeoJsonObject } from 'geojson';
import type { Result, SatelliteInfo } from './types';

export function createMap(onChange: (roi: unknown, label: string) => void) {
  const icon = L.divIcon({className: 'roi-marker', html: '<span></span>', iconSize: [18, 18], iconAnchor: [9, 9]});
  L.Marker.prototype.options.icon = icon;
  const map = L.map('map', {worldCopyJump: true}).setView([34, 1], 3);
  let failures = 0;
  L.tileLayer(import.meta.env.VITE_TILE_URL || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: import.meta.env.VITE_TILE_ATTRIBUTION || '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).on('tileerror', () => {
    if (++failures > 2) document.getElementById('map-hint')!.textContent = 'Basemap unavailable · drawing and predictions still work';
  }).addTo(map);
  const regions = L.featureGroup().addTo(map);
  const footprints = L.featureGroup().addTo(map);
  map.addControl(new L.Control.Draw({position: 'topleft', draw: {
    polyline: false, circle: false, circlemarker: false, marker: {icon},
    polygon: {allowIntersection: false, showArea: true}, rectangle: {},
  }, edit: {featureGroup: regions}}));
  const editor = new L.EditToolbar.Edit(map, {featureGroup: regions});
  let editing = false;
  let drawing: {disable(): void; enable(): void} | undefined;
  const edited = () => onChange(regions.getLayers().length ? regions.toGeoJSON() : null, regions.getLayers().length ? 'Drawn region · WGS84' : 'No region selected');
  function stop() {
    drawing?.disable();
    if (editing) { editor.disable(); editing = false; }
    document.getElementById('edit-roi')!.textContent = 'Edit shape';
  }
  map.on(L.Draw.Event.CREATED, (event: unknown) => {
    regions.clearLayers(); regions.addLayer((event as {layer: L.Layer}).layer); edited();
  });
  map.on(L.Draw.Event.EDITED, edited);
  map.on(L.Draw.Event.DELETED, edited);
  return {
    show(input: unknown, label: string, fit = true, notify = true) {
      const layer = L.geoJSON(input as GeoJsonObject, {
        style: {color: '#187c79', weight: 2, fillOpacity: 0.15},
        pointToLayer: (_feature, ll) => L.marker(ll, {icon}),
      });
      if (!layer.getBounds().isValid()) throw new Error('The region contains no supported map geometry.');
      stop(); regions.clearLayers(); layer.eachLayer(child => regions.addLayer(child));
      if (fit) map.fitBounds(layer.getBounds(), {padding: [45, 45], maxZoom: 11});
      if (notify) onChange(input, label);
    },
    draw(shape: string) {
      stop();
      drawing = shape === 'point' ? new L.Draw.Marker(map, {icon}) : shape === 'rectangle' ? new L.Draw.Rectangle(map) : new L.Draw.Polygon(map, {allowIntersection: false});
      drawing.enable();
    },
    edit() {
      if (!regions.getLayers().length) throw new Error('Select a region before editing.');
      if (editing) { editor.save(); stop(); edited(); }
      else { stop(); editor.enable(); editing = true; document.getElementById('edit-roi')!.textContent = 'Save shape'; }
    },
    save() { if (editing) { editor.save(); stop(); edited(); } },
    clear() { stop(); regions.clearLayers(); edited(); },
    results(result: Result, visible: number[], selected: number | undefined, catalogue: Record<string, SatelliteInfo>, select: (i: number) => void) {
      footprints.clearLayers();
      for (const index of visible) {
        const pass = result.document.passes[index];
        const layer = L.geoJSON(pass.geometry, {style: {color: catalogue[pass.satellite]?.colour || '#187c79', weight: selected === index ? 4 : 1.5, fillOpacity: selected === index ? 0.35 : 0.1}});
        layer.bindTooltip(`${pass.satellite} · ${pass.overpass_utc}`, {sticky: true}).on('click', () => select(index));
        footprints.addLayer(layer);
      }
    },
    focus(result: Result, index: number) {
      const layer = L.geoJSON(result.document.passes[index].geometry);
      if (layer.getBounds().isValid()) map.fitBounds(layer.getBounds(), {padding: [30, 30], maxZoom: 10});
    },
  };
}
