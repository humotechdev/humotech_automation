import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': {
        target: process.env.VITE_BACKEND_ORIGIN ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
        configure: (proxy) => {
          // Стенд работает по production-настройкам, где cookie сессии
          // помечена `Secure`. Браузер молча выбрасывает такую cookie
          // на `http://localhost`, и вход выглядит как «200, а сессии
          // нет». Снимаем флаг только здесь: в сборку это не попадает,
          // backend не меняется.
          proxy.on('proxyRes', (proxyRes) => {
            const cookies = proxyRes.headers['set-cookie'];
            if (cookies) {
              proxyRes.headers['set-cookie'] = cookies.map((value) =>
                value.replace(/;\s*Secure/gi, ''),
              );
            }
          });
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
  },
});
