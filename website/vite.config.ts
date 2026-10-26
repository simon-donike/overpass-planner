import { defineConfig } from 'vite';
import { rmSync } from 'node:fs';
import { resolve } from 'node:path';

export default defineConfig({
  base: process.env.PAGES_BASE_PATH || './',
  worker: { format: 'es' },
  build: { target: 'es2022' },
  plugins: [{name: 'exclude-historical-test-inputs', closeBundle() {
    rmSync(resolve('dist/test-data'), {recursive: true, force: true});
    rmSync(resolve('dist/data/orbital-elements.json'), {force: true});
  }}],
});
