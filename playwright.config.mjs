// Browser tests (headless Chromium, WebGL2 through SwiftShader): npm run test:browser
// Locally on claud: `source /workspace/projects/kipr-tools/bin/pw-env` first (libraries + PW_CHROMIUM_ARGS).
import { defineConfig } from '@playwright/test';

const PORT = Number(process.env.PW_PORT || 8417);   // PW_PORT: run several checkouts side by side
const args = (process.env.PW_CHROMIUM_ARGS || '--use-angle=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist')
  .split(' ').filter(Boolean);

export default defineConfig({
  testDir: 'test',
  testMatch: ['browser/**/*.spec.mjs', 'vendor/**/*.spec.mjs', 'impedance-ui/**/*.spec.mjs'],
  timeout: 120_000,
  fullyParallel: true,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}/`,
    viewport: { width: 1000, height: 700 },
    launchOptions: { args },
  },
  webServer: { command: `node scripts/serve.mjs ${PORT}`, url: `http://127.0.0.1:${PORT}/package.json`, reuseExistingServer: !process.env.CI },
});
