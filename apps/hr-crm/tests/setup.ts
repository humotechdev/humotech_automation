/** Общая подготовка тестов: чистое хранилище и снятые заглушки. */

import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';

/*
 * jsdom не раскладывает страницу, поэтому у элементов нет `scrollIntoView`
 * — не «не работает», а вовсе отсутствует, и вызов падает с TypeError.
 * Заглушка ставится один раз здесь: подпирать её проверкой в самом
 * компоненте значило бы писать в рабочем коде обход тестовой среды.
 */
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
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

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
