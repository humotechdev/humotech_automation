/** Фирменный блок: знак, надписи и отсутствие выдуманных показателей. */

import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { BrandPanel } from '../src/components/BrandPanel';
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
  test('знак, заголовок и одна строка о системе', () => {
    const { container } = render(<BrandPanel />);

    expect(container.querySelector('img[src*="humotech-lockup"]')).not.toBeNull();
    expect(screen.getByText('Управление персоналом')).toBeTruthy();
    expect(screen.getByText('Сотрудники, посещаемость и заявки — в одной системе.')).toBeTruthy();
  });

  test('ни графика, ни показателей, которые можно принять за настоящие', () => {
    // «125+ сотрудников» и «98% вовлечённости» на экране входа читаются
    // как данные организации. Взять их неоткуда: человек ещё не вошёл.
    const { container } = render(<BrandPanel />);
    const text = container.textContent ?? '';

    expect(container.querySelector('svg')).toBeNull();
    expect(text).not.toMatch(/\d+\s*%/);
    expect(text).not.toMatch(/\d+\+/);
  });

  test('заголовок панели не спорит с заголовком формы', () => {
    // Заголовок страницы один — «Вход в систему». Подпись панели —
    // оформление, а не второй раздел для читалки экрана.
    render(<BrandPanel />);

    expect(screen.queryAllByRole('heading')).toHaveLength(0);
  });
});
