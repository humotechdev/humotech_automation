/**
 * Всё, что оболочка помнит между переходами, — одной кнопкой «забыть».
 *
 * Нужна при выходе из системы (следующему вошедшему не должно достаться
 * ни чужих ответов сервера, ни чужих фильтров) и между тестами.
 */

import { forgetBlocks } from '../dashboard/data';
import { forgetPlaces } from './places';
import { forgetScroll } from './scroll';
import { forgetSticky } from './sticky';

export function forgetNavigation(): void {
  forgetBlocks();
  forgetPlaces();
  forgetScroll();
  forgetSticky();
}
