/**
 * Всё, что оболочка помнит между переходами, — одной кнопкой «забыть».
 *
 * Нужна при выходе из системы (следующему вошедшему не должно достаться
 * ни чужих ответов сервера, ни чужих фильтров) и между тестами.
 */

import { forgetBlocks } from '../dashboard/data';
import { forgetBadges } from './badges';
import { forgetPlaces } from './places';
import { forgetScroll } from './scroll';
import { forgetSticky } from './sticky';

export function forgetNavigation(): void {
  forgetBlocks();
  // Счётчики меню тоже ответ сервера для прежнего человека: без сброса
  // следующий вошедший видел чужие числа до первого перечитывания.
  forgetBadges();
  forgetPlaces();
  forgetScroll();
  forgetSticky();
}
