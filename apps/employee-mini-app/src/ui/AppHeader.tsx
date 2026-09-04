/**
 * Шапка главного экрана: кто вошёл и куда нажать, чтобы открыть профиль.
 *
 * Занимает мало места намеренно. Приветствие и имя человек читает один
 * раз в день, а место на экране телефона забирает у того, ради чего
 * приложение открывают.
 *
 * Аватар — инициалы из имени, пришедшего с backend. Фотографию из
 * `initDataUnsafe.user.photo_url` брать нельзя: эти поля не подписаны,
 * подставить туда чужого сотрудника может кто угодно, и выглядеть
 * подмена будет ровно так же.
 */

import { OfficeIcon } from './icons';

/** «Доброе утро» до полудня и так далее — по часам офиса. */
export function greeting(hour: number): string {
  if (hour < 6) return 'Доброй ночи';
  if (hour < 12) return 'Доброе утро';
  if (hour < 18) return 'Добрый день';
  return 'Добрый вечер';
}

/** Инициалы: «Рахимов Далер» → «РД». Одна буква, если слово одно. */
export function initials(fullName: string): string {
  const words = fullName.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '—';
  return words
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? '')
    .join('');
}

/** Местный час в поясе офиса — не в поясе телефона. */
export function officeHour(timeZone: string, now: Date = new Date()): number {
  const text = now.toLocaleString('ru-RU', {
    timeZone,
    hour: '2-digit',
    hour12: false,
  });
  const hour = Number.parseInt(text, 10);
  return Number.isNaN(hour) ? now.getHours() : hour;
}

export function AppHeader({
  fullName,
  office,
  position,
  timeZone,
  onProfile,
}: {
  fullName: string;
  office: string;
  position?: string | null;
  timeZone: string;
  onProfile: () => void;
}) {
  return (
    <header className="app-header">
      <div className="app-header-text">
        <p className="app-header-greeting">{greeting(officeHour(timeZone))}</p>
        <p className="app-header-name">{fullName}</p>
        <p className="app-header-place">
          <OfficeIcon size={14} />
          <span>{[position, office].filter(Boolean).join(' · ')}</span>
        </p>
      </div>

      <button
        type="button"
        className="avatar"
        onClick={onProfile}
        aria-label="Профиль и помощь"
      >
        <span aria-hidden="true">{initials(fullName)}</span>
      </button>
    </header>
  );
}
