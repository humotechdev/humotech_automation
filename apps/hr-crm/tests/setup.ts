/** Общая подготовка тестов: чистое хранилище и снятые заглушки. */

import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';

import { forgetNavigation } from '../src/features/shell/memory';

/*
 * jsdom не раскладывает страницу, поэтому у элементов нет `scrollIntoView`
 * — не «не работает», а вовсе отсутствует, и вызов падает с TypeError.
 * Заглушка ставится один раз здесь: подпирать её проверкой в самом
 * компоненте значило бы писать в рабочем коде обход тестовой среды.
 */
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}
// Лента офисов листается программно — `scrollTo` в jsdom тоже нет.
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = function scrollTo() {};
}

/*
 * Того же рода пробел: jsdom не выдаёт адресов для файлов, а
 * предпросмотр фотографии строится именно так. Заглушка возвращает
 * узнаваемую строку — тест видит, что адрес получен, и не проверяет
 * содержимое картинки.
 */
if (!URL.createObjectURL) {
  URL.createObjectURL = () => 'blob:test';
  URL.revokeObjectURL = () => {};
}

/*
 * И ещё один: `ResizeObserver` в jsdom нет. Лента офисов и карта следят
 * за размером своих областей, и без заглушки страница «Офисы» падала
 * при первом же рендере — тесты видели пустой экран вместо данных.
 * Размеров jsdom всё равно не считает, поэтому заглушка ничего не делает.
 */
if (!('ResizeObserver' in globalThis)) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  // Кэш ответов, адреса разделов, прокрутка и отборы живут на уровне
  // модуля — как во вкладке браузера. Между тестами их надо забывать,
  // иначе следующий тест увидит данные предыдущего вместо загрузки.
  forgetNavigation();
  vi.restoreAllMocks();
  /*
   * `restoreAllMocks` возвращает на место подсмотренные методы, но НЕ
   * снимает подменённые глобальные объекты: `vi.stubGlobal('fetch', …)`
   * переживает тест и остаётся висеть до конца файла. А вместе с ним
   * остаётся и сам `vi.fn()`, который хранит аргументы КАЖДОГО вызова —
   * в наших тестах это тела всех запросов и ответов подряд.
   *
   * На двадцати двух файлах это давало рост до двух с половиной
   * гигабайт и остановку прогона по памяти. Снятие заглушек и очистка
   * истории вызовов после каждого теста убирают удержание.
   */
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});
