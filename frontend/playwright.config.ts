import { defineConfig, devices } from '@playwright/test'
export default defineConfig({
  testDir:'./tests', fullyParallel:false, workers:1, timeout:90000,
  expect:{timeout:15000}, reporter:'list', outputDir:'../runtime/frontend-test-results',
  use:{baseURL:process.env.UI_URL||'http://127.0.0.1:5188',trace:'retain-on-failure',screenshot:'only-on-failure',...devices['Desktop Chrome'],channel:'chrome'},
})
