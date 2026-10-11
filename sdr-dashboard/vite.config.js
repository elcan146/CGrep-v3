import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [tailwindcss(), react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './'),
    },
  },
  assetsInclude: ['**/*.glb'],
  build: {
    chunkSizeWarningLimit: 1000,
    rollupOptions: {
      output: {
        entryFileNames: `assets/[name]-[hash]-${Date.now()}.js`,
        chunkFileNames: `assets/[name]-[hash]-${Date.now()}.js`,
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (id.includes('three')) return 'three';
            if (id.includes('framer-motion')) return 'framer-motion';
            if (id.includes('@react-three')) return 'r3f';
            if (id.includes('lucide-react')) return 'lucide';
            return 'vendor';
          }
        },
      },
    },
    rolldownOptions: {
      external: ['@tsparticles/react', '@tsparticles/slim', '@tsparticles/engine'],
    },
  },
})
