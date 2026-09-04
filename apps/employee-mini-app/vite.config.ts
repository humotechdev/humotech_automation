import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    // Telegram открывает Mini App только по HTTPS, поэтому в разработке
    // между ним и Vite всегда стоит туннель, а Vite по умолчанию отклоняет
    // незнакомый Host. Список хостов задаётся снаружи и в репозитории
    // не хранится: адрес туннеля новый при каждом запуске.
    allowedHosts: process.env.VITE_ALLOWED_HOSTS
      ? process.env.VITE_ALLOWED_HOSTS.split(',').map((host) => host.trim())
      : undefined,
    // Адрес backend задаётся VITE_API_URL. Локально удобнее проксировать:
    // тогда origin один и CORS в разработке не участвует вовсе.
    proxy: {
      '/api': {
        target: process.env.VITE_BACKEND_ORIGIN ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.test.{ts,tsx}'],
  },
});
