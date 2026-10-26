import L from 'leaflet';
import 'leaflet-draw';
import 'leaflet/dist/leaflet.css';
import 'leaflet-draw/dist/leaflet.draw.css';
import type { FeatureCollection, GeoJsonObject } from 'geojson';
import type { Result, SatelliteInfo, SatellitePosition } from './types';

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
  const liveMarkers = new Map<string, L.Marker>();
  let liveRows: SatellitePosition[] = [], colours: Record<string, string> = {};
  let passData: FeatureCollection = {type: 'FeatureCollection', features: []};
  let selectPass: (index: number) => void = () => {};
  let globe: Awaited<ReturnType<typeof import('./globe').createGlobe>> | undefined;
  let loading: Promise<void> | undefined, useGlobe = false;
  const flat = document.getElementById('map')!, round = document.getElementById('globe')!;
  const status = document.getElementById('globe-status')!;
  const flatButton = document.getElementById('view-2d')!, roundButton = document.getElementById('view-3d')!;
  function sync() { globe?.data(regions.toGeoJSON() as FeatureCollection, passData); }
  function globeFailure(message: string) {
    switchFlat(); globe?.destroy(); globe = undefined; loading = undefined;
    status.hidden = false; status.textContent = `3D globe unavailable: ${message} You can keep using the 2D map.`;
  }
  function switchFlat() {
    if (useGlobe && globe) { const c = globe.center(); map.setView([c.lat, c.lng], Math.max(2, c.zoom + 1), {animate: false}); }
    useGlobe = false;
    flat.hidden = false; round.hidden = true; status.hidden = true;
    flatButton.setAttribute('aria-pressed', 'true'); roundButton.setAttribute('aria-pressed', 'false');
    document.getElementById('globe-home')!.hidden = true;
    map.invalidateSize();
  }
  flatButton.onclick = switchFlat;
  roundButton.onclick = async () => {
    if (!useGlobe && globe) { const c = map.getCenter(); globe.move(c.lng, c.lat, map.getZoom() - 1); }
    save(); stop(); useGlobe = true; round.hidden = false; flat.hidden = true;
    status.hidden = false; status.textContent = 'Loading the 3D globe…';
    roundButton.setAttribute('aria-pressed', 'true'); flatButton.setAttribute('aria-pressed', 'false');
    try {
      if (!globe) {
        loading ??= import('./globe').then(async module => {
          globe = await module.createGlobe(index => selectPass(index), globeFailure);
          sync(); globe.positions(liveRows, colours);
          if (regions.getBounds().isValid()) globe.fit(bounds(regions.getBounds()));
        });
        await loading;
      }
      if (!useGlobe) return;
      globe!.resize(); document.getElementById('globe-home')!.hidden = false;
      status.textContent = 'Drag to rotate · +/− to zoom · drawing and editing open the 2D map. OSM tiles stop at ±85° latitude.';
    } catch (error) { globeFailure((error as Error).message); }
  };
  document.getElementById('globe-home')!.onclick = () => globe?.wholeEarth();
  const bounds = (b: L.LatLngBounds): [[number, number], [number, number]] => [[b.getWest(), b.getSouth()], [b.getEast(), b.getNorth()]];
  map.addControl(new L.Control.Draw({position: 'topleft', draw: {
    polyline: false, circle: false, circlemarker: false, marker: {icon},
    polygon: {allowIntersection: false, showArea: true}, rectangle: {},
  }, edit: {featureGroup: regions}}));
  const editor = new L.EditToolbar.Edit(map, {featureGroup: regions});
  let editing = false;
  let drawing: {disable(): void; enable(): void} | undefined;
  const edited = () => { sync(); onChange(regions.getLayers().length ? regions.toGeoJSON() : null, regions.getLayers().length ? 'Drawn region · WGS84' : 'No region selected'); };
  function save() { if (editing) { editor.save(); stop(); edited(); } }
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
        style: {color: '#2563b9', weight: 2, fillOpacity: 0.15},
        pointToLayer: (_feature, ll) => L.marker(ll, {icon}),
      });
      if (!layer.getBounds().isValid()) throw new Error('The region contains no supported map geometry.');
      stop(); regions.clearLayers(); layer.eachLayer(child => regions.addLayer(child));
      if (fit) map.fitBounds(layer.getBounds(), {padding: [45, 45], maxZoom: 11});
      sync(); if (fit) globe?.fit(bounds(layer.getBounds()));
      if (notify) onChange(input, label);
    },
    draw(shape: string) {
      if (useGlobe) switchFlat();
      stop();
      drawing = shape === 'point' ? new L.Draw.Marker(map, {icon}) : shape === 'rectangle' ? new L.Draw.Rectangle(map) : new L.Draw.Polygon(map, {allowIntersection: false});
      drawing.enable();
    },
    edit() {
      if (useGlobe) switchFlat();
      if (!regions.getLayers().length) throw new Error('Select a region before editing.');
      if (editing) { editor.save(); stop(); edited(); }
      else { stop(); editor.enable(); editing = true; document.getElementById('edit-roi')!.textContent = 'Save shape'; }
    },
    save,
    clear() { stop(); regions.clearLayers(); edited(); },
    results(result: Result, visible: number[], selected: number | undefined, catalogue: Record<string, SatelliteInfo>, select: (i: number) => void) {
      selectPass = select;
      passData = {type: 'FeatureCollection', features: visible.map(index => ({type: 'Feature', geometry: result.document.passes[index].geometry, properties: {index, colour: catalogue[result.document.passes[index].satellite]?.colour || '#2563b9', selected: selected === index}}))};
      sync();
      footprints.clearLayers();
      for (const index of visible) {
        const pass = result.document.passes[index];
        const layer = L.geoJSON(pass.geometry, {style: {color: catalogue[pass.satellite]?.colour || '#2563b9', weight: selected === index ? 4 : 1.5, fillOpacity: selected === index ? 0.35 : 0.1}});
        layer.bindTooltip(`${pass.satellite} · ${pass.overpass_utc}`, {sticky: true}).on('click', () => select(index));
        footprints.addLayer(layer);
      }
    },
    focus(result: Result, index: number) {
      const layer = L.geoJSON(result.document.passes[index].geometry);
      if (layer.getBounds().isValid()) { map.fitBounds(layer.getBounds(), {padding: [30, 30], maxZoom: 10}); globe?.fit(bounds(layer.getBounds())); }
    },
    positions(rows: SatellitePosition[], catalogue: Record<string, SatelliteInfo>) {
      liveRows = rows; colours = Object.fromEntries(Object.entries(catalogue).map(([name, info]) => [name, info.colour]));
      const names = new Set(rows.map(p => p.satellite));
      for (const [name, marker] of liveMarkers) if (!names.has(name)) { marker.remove(); liveMarkers.delete(name); }
      for (const row of rows) {
        let marker = liveMarkers.get(row.satellite);
        const description = `${row.satellite} · ${row.alt_km.toFixed(0)} km altitude · ${row.direction} · ${row.freshness} elements`;
        if (!marker) {
          const element = document.createElement('span'); element.className = 'live-marker';
          element.style.setProperty('--satellite-colour', colours[row.satellite] || '#2563b9');
          marker = L.marker([row.lat, row.lon], {icon: L.divIcon({className: 'live-marker-wrap', html: element, iconSize: [18, 18], iconAnchor: [9, 9]})}).addTo(map);
          liveMarkers.set(row.satellite, marker);
        }
        marker.setLatLng([row.lat, row.lon]);
        const content = document.createElement('span'); content.textContent = description;
        marker.unbindTooltip().bindTooltip(content); marker.getElement()?.setAttribute('aria-label', description);
      }
      globe?.positions(rows, colours);
    },
    locate(position: SatellitePosition) {
      map.setView([position.lat, position.lon], 4, {animate: false});
      globe?.move(position.lon, position.lat, 1);
    },
  };
}
