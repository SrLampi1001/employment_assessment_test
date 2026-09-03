import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))

export default defineConfig(({ mode }) => {
  // Source the dev server port from the project-root .env (the same file
  // docker-compose reads). Empty prefix loads every var, not just VITE_*;
  // Phase 0 only reads FRONTEND_PORT, so the wider exposure is harmless.
  // From Phase 2 onward, secrets live behind pydantic-settings on the
  // backend; client-visible vars must use the VITE_ prefix (Vite enforces
  // this at build time).
  const env = loadEnv(mode, resolve(here, '..'), '')

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': resolve(here, 'src'),
      },
    },
    server: {
      port: Number(env.FRONTEND_PORT) || 5173,
    },
  }
})
