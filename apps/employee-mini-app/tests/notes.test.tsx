// @vitest-environment jsdom
/**
 * Лента уведомлений.
 *
 * Экран нужен ровно затем, чтобы колокольчик и карточка объявления вели
 * туда, где эти уведомления есть. Поэтому проверяется в первую очередь
 * не вёрстка, а что пустая лента отличима от несостоявшегося запроса,
 * а прочитанное — от нового.
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Notes } from '../src/screens/Notes';
import { failed, note, pending, ready } from './fixtures';

afterEach(cleanup);

const TZ = 'Asia/Dushanbe';

describe('уведомления', () => {
  it('показывает заголовок, текст и время в поясе офиса', () => {
    render(
      <Notes
        section={ready({ unread: 1, items: [note()] })}
        timeZone={TZ}
        onRead={() => {}}
      />,
    );

    expect(screen.getByText('Объявление')).toBeTruthy();
    expect(screen.getByText('Обновлён график работы офиса')).toBeTruthy();
    // 05:00 UTC — это 10:00 в Душанбе, а не в поясе машины, где идут тесты.
    expect(screen.getByText('4 сентября, 10:00')).toBeTruthy();
  });

  it('непрочитанное помечает точкой, прочитанное — нет', () => {
    const { container } = render(
      <Notes
        section={ready({
          unread: 1,
          items: [
            note({ id: 'a' }),
            note({ id: 'b', is_read: true, read_at: '2026-09-04T06:00:00Z' }),
          ],
        })}
        timeZone={TZ}
        onRead={() => {}}
      />,
    );

    expect(container.querySelectorAll('.note-card').length).toBe(2);
    expect(container.querySelectorAll('.note-card-new').length).toBe(1);
  });

  it('прочитанное не притворяется кнопкой: нажимать там нечего', () => {
    render(
      <Notes
        section={ready({
          unread: 0,
          items: [note({ is_read: true, read_at: '2026-09-04T06:00:00Z' })],
        })}
        timeZone={TZ}
        onRead={() => {}}
      />,
    );

    expect(screen.queryByRole('button', { name: /Отметить прочитанным/ })).toBeNull();
  });

  it('нажатие на строку отдаёт уведомление наружу', () => {
    const read = vi.fn();
    render(
      <Notes
        section={ready({ unread: 1, items: [note()] })}
        timeZone={TZ}
        onRead={read}
      />,
    );

    fireEvent.click(
      screen.getByRole('button', { name: 'Отметить прочитанным: Объявление' }),
    );
    expect(read).toHaveBeenCalledTimes(1);
    expect(read.mock.calls[0][0].id).toBe('n1');
  });

  it('пустая лента — это «уведомлений нет», а не ошибка', () => {
    render(
      <Notes
        section={ready({ unread: 0, items: [] })}
        timeZone={TZ}
        onRead={() => {}}
      />,
    );

    expect(screen.getByText('Уведомлений нет')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('несостоявшийся запрос — это ошибка с кнопкой, а не пустая лента', () => {
    const again = vi.fn();
    render(
      <Notes
        section={failed('Сервер временно недоступен', again)}
        timeZone={TZ}
        onRead={() => {}}
      />,
    );

    expect(screen.queryByText('Уведомлений нет')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Обновить' }));
    expect(again).toHaveBeenCalledTimes(1);
  });

  it('первая загрузка не выдаёт пустоту за ответ', () => {
    render(
      <Notes section={pending()} timeZone={TZ} onRead={() => {}} />,
    );

    expect(screen.queryByText('Уведомлений нет')).toBeNull();
  });
});
