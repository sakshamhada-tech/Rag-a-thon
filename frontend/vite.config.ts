import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const apiTarget = 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/runs': apiTarget,
      '/demos': apiTarget,
      '/config': apiTarget,
      '/health': apiTarget
    }
  },
  build: {
    sourcemap: false
  }
});
