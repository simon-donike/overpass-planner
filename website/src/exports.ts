import type { Result } from './types';

export function encodeCsv(records: string[][]): string {
  return records.map(row => row.map(value => /[",\r\n]/.test(value) ? '"' + value.replaceAll('"', '""') + '"' : value).join(',')).join('\r\n') + '\r\n';
}

export function exportText(result: Result, format: string, indices?: number[]) {
  const passes = indices ? indices.map(i => result.document.passes[i]) : result.document.passes;
  if (format === 'csv') return {text: indices ? encodeCsv([result.csv_header, ...indices.map(i => result.csv_rows[i])]) : result.csv, mime: 'text/csv;charset=utf-8', extension: 'csv'};
  if (format === 'geojson') return {
    text: JSON.stringify({type: 'FeatureCollection', features: passes.map(pass => {
      const {geometry, ...properties} = pass;
      return {type: 'Feature', geometry, properties};
    })}, null, 2), mime: 'application/geo+json', extension: 'geojson',
  };
  return {text: JSON.stringify({...result.document, passes, count: passes.length}, null, 2), mime: 'application/json', extension: 'json'};
}

export function downloadResult(result: Result, format: string, indices?: number[]) {
  const output = exportText(result, format, indices);
  const url = URL.createObjectURL(new Blob([output.text], {type: output.mime}));
  const link = document.createElement('a');
  link.href = url;
  link.download = `overpasses${indices ? '-filtered' : ''}.${output.extension}`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
