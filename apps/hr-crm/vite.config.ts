import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/** Адрес, который стенд считает доверенным источником запросов. */
const TRUSTED_ORIGIN =
  process.env.VITE_TRUSTED_ORIGIN ?? 'https://uncanny-superman-obedience.ngrok-free.dev';

export default defineConfig({
  plugins: [react()],
  /*
   * Прод-сборка: без карт исходников (они выдали бы весь код с
   * комментариями любому, кто откроет `/assets/*.js.map`). Прокси,
   * подмена `Origin` и снятие `Secure` ниже живут только в `server`,
   * то есть в dev-сервере, и в `dist/` не попадают.
   */
  build: {
    sourcemap: false,
  },
  server: {
    port: 5173,
    host: true,
    /*
     * Имена контейнеров стенда — для проверок, которые ходят к
     * dev-серверу изнутри сети Docker, а не через порт хоста.
     *
     * Vite с седьмой версии отвечает «Blocked request» на незнакомое
     * имя в заголовке `Host`, и такая проверка молча снимает не
     * страницу, а заглушку. Числовые адреса Vite пропускает сам,
     * поэтому доступ по IP из локальной сети это не меняет. Настройка
     * касается только dev-сервера: в сборку она не попадает.
     */
    allowedHosts: ['humotech_crm_e2e', 'humotech_crm_dev', 'localhost'],
    proxy: {
      '/api': {
        target: process.env.VITE_BACKEND_ORIGIN ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
        configure: (proxy) => {
          // Django проверяет заголовок `Origin` по списку
          // `CSRF_TRUSTED_ORIGINS`, и `http://localhost:5173` в нём нет
          // и быть не должно: список описывает адрес стенда, а не
          // машину разработчика. `changeOrigin` переписывает только
          // `Host`, поэтому изменяющие запросы отсюда получали
          // «CSRF Failed: Origin checking failed».
          //
          // Подменяем `Origin` и `Referer` на доверенный адрес. Это
          // настройка dev-сервера: в сборку она не попадает, backend
          // и его список доверенных адресов не меняются.
          proxy.on('proxyReq', (proxyReq, req) => {
            // Адрес клиента задаёт не клиент. Dev-сервер открыт в
            // локальную сеть, и присланный `X-Forwarded-For` доходил до
            // шлюза как есть: любой мог выбрать себе IP для лимита
            // попыток входа. Чужие заголовки снимаем и ставим адрес
            // сокета — единственное, что здесь известно наверняка.
            for (const name of ['x-forwarded-for', 'x-real-ip', 'forwarded',
              'x-forwarded-host', 'x-forwarded-proto', 'x-forwarded-port', 'x-client-ip']) {
              proxyReq.removeHeader(name);
            }
            const peer = (req.socket.remoteAddress ?? '').replace(/^::ffff:/, '');
            if (peer) proxyReq.setHeader('x-forwarded-for', peer);

            if (!proxyReq.getHeader('origin')) return;
            proxyReq.setHeader('origin', TRUSTED_ORIGIN);
            proxyReq.setHeader('referer', `${TRUSTED_ORIGIN}/`);
          });

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
