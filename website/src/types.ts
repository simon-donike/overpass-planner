import type { Geometry } from 'geojson';

export interface SatelliteInfo {
  norad_id: number; mission: string; sensor: string; swath_km: number;
  colour: string; note: string; modes?: string[];
}
export interface PredictionRequest {
  roi: unknown; roi_name: string; tle_text: string; orbital_source: string;
  orbital_sources?: Record<string, string>;
  orbital_warnings?: {code: string; message: string}[];
  satellites: string[]; start_utc: string; days: number; timezone: string;
  imaging_only: boolean; min_coverage: number; min_sun_elevation: number; s1_mode: 'IW' | 'EW';
}
export interface Pass {
  satellite: string; mission: string; direction: string; roi_coverage_pct: number;
  overpass_utc: string; overpass_local: string; roi_entry_utc: string; roi_exit_utc: string;
  timezone: string; utc_offset: string; view_angle_deg: number; incidence_angle_deg: number | null;
  imaging_expected: boolean; geometry: Geometry;
  status: { imaging_label: string; acquisition_label: string; orbital_data: string;
    tle_age_days: number; warnings: { code: string; message: string }[] };
  [key: string]: unknown;
}
export interface PredictionDocument {
  schema_version: string; count: number; passes: Pass[]; assumptions: string[];
  request: { roi: Geometry; timezone: string; [key: string]: unknown };
  warnings: { code: string; message: string }[];
  provenance: { package_version: string; orbital_elements: unknown[] };
  [key: string]: unknown;
}
export interface Result {
  document: PredictionDocument; csv: string; csv_header: string[]; csv_rows: string[][]; elapsed_s: number;
}
export interface Progress {
  type: 'progress'; id: number; stage: string; label: string;
  completed: number; total: number; elapsed: number;
}
export type WorkerEvent = Progress | {type: 'ready'; catalogue: Record<string, SatelliteInfo>; version: string; id: number}
  | {type: 'result'; result: Result; id: number} | {type: 'error'; message: string; id: number};
