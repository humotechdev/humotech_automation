/** Фирменный блок: знак, надписи и отсутствие выдуманных показателей. */

import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { BrandPanel } from '../src/components/BrandPanel';
import { RATIO } from '../src/components/DecorChart';
import { Logo, Wordmark } from '../src/components/Logo';

describe('знак', () => {
  test('это файл-ассет, а не нарисованная в разметке буква', () => {
    // Знак взят с аватарки Telegram-бота и лежит отдельным файлом:
    // «похожая H» из шрифта или случайной иконки — другой знак.
    const { container } = render(<Logo />);
    const image = container.querySelector('img');

    expect(image).not.toBeNull();
    expect(image?.getAttribute('src')).toMatch(/humotech-mark/);
    // Декоративный: рядом всегда идёт слово HUMOTECH.
    expect(image?.getAttribute('alt')).toBe('');
  });

  test('рядом со знаком стоят обе надписи', () => {
    render(<Wordmark />);

    expect(screen.getByText('HUMOTECH')).toBeTruthy();
    expect(screen.getByText('HR CONTROL SYSTEM')).toBeTruthy();
  });
});

describe('левая панель', () => {
  test('три колонки на месте макета', () => {
    render(<BrandPanel />);

    expect(screen.getByText('Единый учёт')).toBeTruthy();
    expect(screen.getByText('Контроль доступа')).toBeTruthy();
    expect(screen.getByText('Прозрачная аналитика')).toBeTruthy();
    expect(screen.getByText('Единое управление персоналом')).toBeTruthy();
  });

  test('ни одного показателя, который можно принять за настоящий', () => {
    // «125+ сотрудников» и «98% вовлечённости» на экране входа читаются
    // как данные организации. Взять их неоткуда: человек ещё не вошёл.
    const { container } = render(<BrandPanel />);
    const text = container.textContent ?? '';

    expect(text).not.toMatch(/\d+\s*%/);
    expect(text).not.toMatch(/\d+\+/);
    expect(text).not.toMatch(/Вовлечённость|Сотрудников:|Отделов:/);
  });

  test('график сохраняет пропорции, а не растягивается по панели', () => {
    // `preserveAspectRatio="none"` растягивал рисунок под контейнер:
    // на высоком мониторе круглые точки становились овалами, а столбцы
    // тянулись вверх. Пропорции держит `viewBox` вместе с отношением
    // сторон контейнера в CSS.
    const { container } = render(<BrandPanel />);
    const svg = container.querySelector('svg');

    expect(svg?.getAttribute('preserveAspectRatio')).toBeNull();
    expect(svg?.getAttribute('viewBox')).toBe('0 0 340 190');
    expect(Math.round(RATIO * 100) / 100).toBe(1.79);
  });

  test('график не объявляет себя данными для читалки экрана', () => {
    const { container } = render(<BrandPanel />);
    const svg = container.querySelector('svg');

    expect(svg?.getAttribute('aria-hidden')).toBe('true');
  });
});
