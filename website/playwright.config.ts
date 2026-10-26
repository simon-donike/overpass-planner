import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 240_000,
  use: {
    actionTimeout: 15_000,
    baseURL: process.env.SITE_TEST_URL || 'http://127.0.0.1:4173',
    headless: true,
    launchOptions: process.env.EO_CHROMIUM_PATH ? {executablePath: process.env.EO_CHROMIUM_PATH} : {},
  },
  expect: { timeout: 10_000 },
  workers: 1,
  webServer: process.env.SITE_TEST_URL ? undefined : {
    command: 'npm run dev -- --port 4173 --strictPort',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: !process.env.CI,
  },
});
