/**
 * Время в поясе организации.
 *
 * Прежняя ошибка на «Посещаемости» выглядела безобидно: `at.slice(11, 16)`
 * брал часы прямо из ISO-строки, то есть показывал UTC — а рядом стояла
 * подпись «Asia/Dushanbe». Пять часов разницы, и ни одного признака, что
 * что-то не так.
 *
 * Поэтому проверяется не «функция что-то возвращает», а ровно то, чего
 * прежний код не делал: перевод в названный пояс, переход через полночь
 * и независимость от пояса машины, на которой открыта страница.
 */

import { describe, expect, test } from 'vitest';

import {
  clock,
  clockOnDay,
  dayInZone,
  moment,
  shortDate,
} from '../src/features/time/zone';

// 2026-09-07T23:40:00Z — в Душанбе это уже 04:40 следующего дня.
const NIGHT = '2026-09-07T23:40:00Z';
// 2026-09-07T03:15:00Z — в Душанбе 08:15 того же дня.
const MORNING = '2026-09-07T03:15:00Z';

describe('перевод в пояс организации', () => {
  test('показывается местное время, а не UTC', () => {
    expect(clock(MORNING, 'Asia/Dushanbe')).toBe('08:15');
    // Тот же момент в UTC — другое число. Значит, пояс действительно
    // участвует, а не приписан к неизменной подстроке.
    expect(clock(MORNING, 'UTC')).toBe('03:15');
  });

  test('пояс с другим смещением даёт своё время', () => {
    expect(clock(MORNING, 'Europe/Moscow')).toBe('06:15');
    expect(clock(MORNING, 'Asia/Tokyo')).toBe('12:15');
  });

  test('пустой момент — прочерк, а не «—:—»', () => {
    expect(clock(null, 'Asia/Dushanbe')).toBe('—');
    expect(clock('', 'Asia/Dushanbe')).toBe('—');
    expect(clock('не дата', 'Asia/Dushanbe')).toBe('—');
  });

  test('момент с датой показывает день в том же поясе', () => {
    expect(moment(NIGHT, 'Asia/Dushanbe', false)).toContain('08');
    expect(moment(NIGHT, 'Asia/Dushanbe', false)).toContain('сент');
  });
});

describe('переход через полночь', () => {
  test('день считается в поясе организации, а не по UTC', () => {
    expect(dayInZone(NIGHT, 'UTC')).toBe('2026-09-07');
    expect(dayInZone(NIGHT, 'Asia/Dushanbe')).toBe('2026-09-08');
  });

  test('выход на следующий день помечается датой', () => {
    // Строка таблицы — за 7 сентября; выход пришёлся уже на 8-е.
    expect(clockOnDay(NIGHT, 'Asia/Dushanbe', '2026-09-07')).toBe(
      '04:40 (8 сен)',
    );
  });

  test('в свой день приписки нет', () => {
    expect(clockOnDay(MORNING, 'Asia/Dushanbe', '2026-09-07')).toBe('08:15');
  });

  test('без дня строки приписка не появляется', () => {
    expect(clockOnDay(NIGHT, 'Asia/Dushanbe', '')).toBe('04:40');
  });

  test('короткая дата берётся из пояса организации', () => {
    expect(shortDate(NIGHT, 'Asia/Dushanbe')).toBe('8 сен');
    expect(shortDate(NIGHT, 'UTC')).toBe('7 сен');
  });
});

describe('независимость от пояса браузера', () => {
  test('результат не зависит от пояса машины', () => {
    // Так выглядел бы перевод «по-местному»: если бы функция полагалась
    // на пояс машины, её ответ совпал бы вот с этим — и менялся бы
    // от ноутбука к ноутбуку.
    const local = new Intl.DateTimeFormat('ru-RU', {
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(MORNING));

    expect(clock(MORNING, 'Asia/Dushanbe')).toBe('08:15');
    if (Intl.DateTimeFormat().resolvedOptions().timeZone !== 'Asia/Dushanbe') {
      expect(clock(MORNING, 'Asia/Dushanbe')).not.toBe(local);
    }
  });

  test('час машины не подмешивается в день', () => {
    expect(dayInZone(NIGHT, 'Asia/Dushanbe')).toBe('2026-09-08');
    expect(dayInZone(NIGHT, 'America/New_York')).toBe('2026-09-07');
  });
});
