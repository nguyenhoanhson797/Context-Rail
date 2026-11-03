import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/* Resolved from this file, never from the cwd: `npm --prefix web run build` from the repo root must resolve the
   Python package's static folder, not a path under whatever directory the build was started from. */
const HERE = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: HERE,
  plugins: [tailwindcss()],
  esbuild: { jsx: 'automatic' },
  server: { port: 5173 },
  /* The bundle lands in the Python package, which serves it; the folder holds only the bundle, so emptying it is safe. */
  build: { outDir: path.resolve(HERE, '../src/context_rail/ui/static'), emptyOutDir: true },
});