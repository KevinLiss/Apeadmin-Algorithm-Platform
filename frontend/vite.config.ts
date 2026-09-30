import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    base: '/admin/',
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
        // vue3-verify package.json exports has a broken style.css mapping;
        // alias the css path directly to the real file
        'vue3-verify/dist/vue3-verify.css': fileURLToPath(
          new URL('./node_modules/vue3-verify/dist/vue3-verify.css', import.meta.url)
        ),
      },
    },
    server: {
      host: '127.0.0.1',
      port: 5173,
      proxy: {
        '/api': {
          // 127.0.0.1 而非 localhost：沙箱/hosts 环境下 localhost 间歇性
          // ENOTFOUND，导致代理请求失败、页面下拉数据拉不到（2026-09-30）
          target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
        '/apehub-web': {
          target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
