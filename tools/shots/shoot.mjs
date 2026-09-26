/**
 * Снимки страниц CRM в точной области просмотра и сравнение с эталоном.
 *
 * Зачем headless, а не окно браузера: физическое окно на этой машине
 * 1920×842, и область просмотра 1672×941 в нём не помещается по высоте.
 * Headless задаёт область просмотра сам, размер окна операционной
 * системы на него не влияет вовсе.
 *
 * Масштаб строго 1: при `deviceScaleFactor: 2` снимок вышел бы 3344
 * пикселя шириной и совпал бы с эталоном только после уменьшения, то
 * есть сравнивалось бы не то, что видит человек.
 *
 * Вход. Сессия нужна настоящая: страницы CRM без неё показывают форму
 * входа. Учётные данные берутся из окружения и нигде не печатаются —
 * ни в журнал, ни в имена файлов.
 */

import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright-core';
import { PNG } from 'pngjs';
import pixelmatch from 'pixelmatch';

// Размер эталона по умолчанию; другой — для проверки экранов пользователей.
const WIDTH = Number(process.env.WIDTH ?? 1672);
const HEIGHT = Number(process.env.HEIGHT ?? 941);

const BASE = process.env.CRM_URL ?? 'http://humotech_crm_e2e:5174';
const OUT = process.env.OUT_DIR ?? '/work/out';
const EMAIL = process.env.HUMOTECH_EMAIL ?? '';
const PASSWORD = process.env.HUMOTECH_PASSWORD ?? '';

/** Что снимаем: имя файла, адрес и чего дождаться на странице. */
const PAGES = (process.env.PAGES ?? 'employees').split(',').map((one) => one.trim());

const TARGETS = {
  employees: {
    url: '/employees?view=cards',
    ready: '.person',
    reference: 'employees-reference-1672x941.png',
  },
  attendance: {
    url: '/attendance',
    ready: '.people tbody tr',
    reference: 'attendance-reference-1672x941.png',
  },
  analytics: {
    url: '/analytics',
    ready: '.an-cell',
    reference: 'analytics-reference-1672x941.png',
  },
  offices: {
    url: '/offices',
    ready: '.map__area',
    reference: 'offices-reference-1672x941.png',
  },
  requests: {
    url: '/requests',
    ready: '.rq-row',
    reference: 'requests-reference-1672x941.png',
  },
  questions: {
    url: process.env.QUESTIONS_URL ?? '/questions',
    ready: '.qs-msg',
    reference: 'questions-reference-1672x941.png',
  },
  reports: {
    url: '/reports',
    ready: '.rp-kind',
    reference: 'reports-reference-1672x941.png',
  },
  hire: {
    url: '/employees/new',
    ready: 'form',
    // Длинная форма: снимок после прокрутки вниз проверяет, что меню
    // остаётся на месте.
    scroll: true,
  },
  employee: {
    url: null, // подставляется первым сотрудником списка
    ready: '.ea-svg',
    reference: 'employee-reference-1672x941.png',
  },
};

async function main() {
  await fs.mkdir(OUT, { recursive: true });

  const browser = await chromium.launch({
    executablePath: '/usr/bin/chromium-browser',
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--font-render-hinting=none'],
  });
  const context = await browser.newContext({
    viewport: { width: WIDTH, height: HEIGHT },
    deviceScaleFactor: 1,
    locale: 'ru-RU',
    timezoneId: 'Asia/Tashkent',
    // Анимации выключены: снимок не должен зависеть от того, в какой
    // момент перехода он сделан.
    reducedMotion: 'reduce',
  });
  const page = await context.newPage();

  await signIn(page);

  for (const name of PAGES) {
    const target = TARGETS[name];
    if (!target) {
      console.log(`нет такой страницы: ${name}`);
      continue;
    }
    const url = target.url ?? (await firstEmployee(page));
    await page.goto(`${BASE}${url}`, { waitUntil: 'domcontentloaded' });
    await settle(page, target.ready);
    if (target.scroll) {
      await page.evaluate(() => {
        const work = document.querySelector('.work');
        if (work) work.scrollTop = work.scrollHeight;
        window.scrollTo(0, document.documentElement.scrollHeight);
      });
      await page.waitForTimeout(400);
    }

    if (process.env.MEASURE) {
      const boxes = await page.evaluate((selectors) => {
        const out = {};
        for (const one of selectors.split(',')) {
          const el = document.querySelector(one.trim());
          if (!el) { out[one.trim()] = null; continue; }
          const b = el.getBoundingClientRect();
          out[one.trim()] = [Math.round(b.x), Math.round(b.y),
                             Math.round(b.width), Math.round(b.height)];
        }
        out['_scroll'] = [document.documentElement.scrollWidth,
                          document.documentElement.scrollHeight];
        return out;
      }, process.env.MEASURE);
      console.log(JSON.stringify(boxes, null, 1));
    }

    // Выражение из окружения выполняется на странице, результат печатается:
    // разметка и вычисленные стили элемента без ручного браузера.
    if (process.env.PROBE) {
      console.log(JSON.stringify(await page.evaluate(process.env.PROBE)));
    }

    const actual = path.join(OUT, `${name}-actual-1672x941.png`);
    await page.screenshot({ path: actual });
    console.log(`снято: ${path.basename(actual)}`);

    await compare(name, actual, target.reference);
  }

  await browser.close();
}

/** Вход по форме. Пароль берётся из окружения и никуда не выводится. */
async function signIn(page) {
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);

  const form = await page.$('input[type="password"]');
  if (!form) return; // сессия уже есть

  if (!EMAIL || !PASSWORD) {
    throw new Error(
      'Нужна сессия: задайте HUMOTECH_EMAIL и HUMOTECH_PASSWORD в окружении. ' +
        'Без них снимки получатся со страницы входа.',
    );
  }
  // Поле логина у формы `type="text"`, а не `email`: туда вводят и
  // адрес, и логин. Ищется по автозаполнению — оно у него одно.
  await page.fill('input[autocomplete="username"]', EMAIL);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL((url) => !url.pathname.includes('login'), { timeout: 20000 });
  await page.waitForTimeout(1200);
}

async function firstEmployee(page) {
  const answer = await page.evaluate(async () => {
    const r = await fetch('/api/v1/employees?limit=1', { credentials: 'include' });
    const b = await r.json();
    return b.items?.[0]?.id ?? null;
  });
  return `/employees/${answer}?tab=attendance&period=month`;
}

/**
 * Дождаться, пока страница перестанет меняться.
 *
 * Мало `load`: данные приходят запросами, шрифты — отдельно, а снимок
 * до их прихода показал бы пустые панели и системный шрифт.
 */
async function settle(page, ready) {
  await page.waitForSelector(ready, { timeout: 30000 }).catch(() => {});
  await page.evaluate(() => document.fonts.ready);
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(700);
}

/** Наложение и разница. Оба изображения обязаны быть одного размера. */
async function compare(name, actualPath, referenceName) {
  if (!referenceName) return;
  const referencePath = path.join('/work/reference', referenceName);
  let reference;
  try {
    reference = PNG.sync.read(await fs.readFile(referencePath));
  } catch {
    console.log(`эталона нет: ${referenceName} — сравнение пропущено`);
    return;
  }
  const actual = PNG.sync.read(await fs.readFile(actualPath));
  if (reference.width !== actual.width || reference.height !== actual.height) {
    console.log(
      `размеры не совпали: эталон ${reference.width}×${reference.height}, ` +
        `снимок ${actual.width}×${actual.height}`,
    );
    return;
  }

  const diff = new PNG({ width: actual.width, height: actual.height });
  const changed = pixelmatch(
    reference.data, actual.data, diff.data, actual.width, actual.height,
    { threshold: 0.18, diffColor: [227, 72, 79] },
  );
  await fs.writeFile(
    path.join(OUT, `${name}-diff-1672x941.png`), PNG.sync.write(diff),
  );

  // Наложение: эталон под снимком, снимок поверх с половинной
  // непрозрачностью. Смещения видно сразу — двоением линий.
  const overlay = new PNG({ width: actual.width, height: actual.height });
  for (let at = 0; at < overlay.data.length; at += 4) {
    for (let channel = 0; channel < 3; channel += 1) {
      overlay.data[at + channel] = Math.round(
        (reference.data[at + channel] + actual.data[at + channel]) / 2,
      );
    }
    overlay.data[at + 3] = 255;
  }
  await fs.writeFile(
    path.join(OUT, `${name}-overlay-1672x941.png`), PNG.sync.write(overlay),
  );

  const share = ((changed / (actual.width * actual.height)) * 100).toFixed(2);
  console.log(`расхождение ${name}: ${changed} точек, ${share}%`);
}

main().catch((error) => {
  console.error(String(error.message ?? error));
  process.exit(1);
});
