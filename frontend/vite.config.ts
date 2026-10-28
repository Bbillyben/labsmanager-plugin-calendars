import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react-swc'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist', emptyOutDir: true, manifest: 'manifest.json',
    rollupOptions: {
      preserveEntrySignatures: 'strict',
      input: fileURLToPath(new URL('./src/main.tsx', import.meta.url)),
      external: ['react', 'react/jsx-runtime', '@labsmanager/plugin-ui', '@labsmanager/plugin-calendar'],
      output: { entryFileNames: 'assets/main-[hash].js', assetFileNames: 'assets/[name]-[hash][extname]' },
    },
  },
})
