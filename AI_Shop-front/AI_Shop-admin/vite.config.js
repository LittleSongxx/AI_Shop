import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '/admin/',
  plugins: [
    vue(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    },
  },
  server: {
    historyApiFallback: true,
    hmr: true,
    host: '0.0.0.0',
    port: 6002,
    proxy: {
      // 开发态 /api → Gateway /admin-api（与生产 baseURL 对齐）
      "/admin-api": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
      "/api": {
        target: "http://localhost:8080",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '/admin-api'),
      },
      "/ws": {
        target: "ws://localhost:8080",
        changeOrigin: true,
        ws: true,
      }
    }
  },
  build: {
    chunkSizeWarningLimit: 400,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;
          if (id.includes('/element-plus/') || id.includes('/@element-plus/')) return 'vendor-element';
          if (id.includes('/echarts/') || id.includes('/zrender/')) return 'vendor-charts';
          if (id.includes('/md-editor-v3/')) return 'vendor-editor-ui';
          if (id.includes('/highlight.js/')) return 'vendor-highlight';
          if (id.includes('/markdown-it')) return 'vendor-markdown';
          if (id.includes('/katex/')) return 'vendor-katex';
          if (id.includes('/cropper')) return 'vendor-media';
          if (
            id.includes('/@codemirror/') ||
            id.includes('/@lezer/') ||
            id.includes('/codemirror/')
          ) {
            const match = id.match(/node_modules[\\/]((?:@[^\\/]+[\\/])?[^\\/]+)/);
            const packageName = match?.[1]?.replaceAll('/', '-') || 'core';
            return `vendor-codemirror-${packageName.replace(/^@/, '')}`;
          }
          if (id.includes('/axios/')) return 'vendor-http';
          return undefined;
        },
        entryFileNames: 'assets/[hash].js',
        chunkFileNames: 'assets/[hash].js',
        assetFileNames: (assetInfo) => {
          const extType = assetInfo.name.split('.').pop();
          if (extType === 'css') {
            return 'assets/[hash].[ext]';
          }
          return 'assets/[hash].[ext]';
        },
      },
    }
  }
})
