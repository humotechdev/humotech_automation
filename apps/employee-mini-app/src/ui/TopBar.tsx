/**
 * Верхняя панель: знак, уведомления, аватар.
 *
 * Единственная крупная тёмная плоскость приложения. Она же —
 * продолжение шапки Telegram: в полноэкранном режиме под ней проходит
 * собственная панель клиента, поэтому высота считается вместе
 * с безопасной зоной сверху, а не поверх неё.
 *
 * Знак — тот же файл, что в CRM и у бота, а не перерисованный и не
 * набранный шрифтом. Логотип, «примерно похожий» на фирменный, хуже
 * отсутствующего: подделку видно, а исправлять приходится везде сразу.
 *
 * Аватар — инициалы из имени, пришедшего с backend. Фотографию из
 * `initDataUnsafe.user.photo_url` брать нельзя: эти поля не подписаны,
 * подставить туда чужого сотрудника может кто угодно.
 */

import lockup from '../assets/humotech-lockup-dark.png';
import { BellIcon } from './icons';

/** Инициалы: «Рахимов Далер» → «РД». Одна буква, если слово одно. */
export function initials(fullName: string): string {
  const words = fullName.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '—';
  return words
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? '')
    .join('');
}

export function TopBar({
  fullName,
  unread,
  onNotifications,
  onProfile,
}: {
  fullName: string;
  /** Сколько уведомлений не прочитано. Точка загорается от единицы. */
  unread: number;
  onNotifications: () => void;
  onProfile: () => void;
}) {
  return (
    <header className="topbar">
      <img className="topbar-logo" src={lockup} alt="HUMOTECH" />

      <div className="topbar-actions">
        <button
          type="button"
          className="topbar-button"
          onClick={onNotifications}
          aria-label={
            unread > 0
              ? `Уведомления, непрочитанных: ${unread}`
              : 'Уведомления'
          }
        >
          <BellIcon size={22} />
          {/* Точка — рядом с иконкой, а не подложка под ней: цвет здесь
              несёт отдельный знак, а сама иконка остаётся белой линией. */}
          {unread > 0 && <span className="topbar-dot" aria-hidden="true" />}
        </button>

        <button
          type="button"
          className="topbar-avatar"
          onClick={onProfile}
          // Не просто «Профиль»: так называется и вкладка внизу, а два
          // элемента с одним именем диктор перечисляет неразличимо.
          aria-label="Открыть профиль"
        >
          <span aria-hidden="true">{initials(fullName)}</span>
        </button>
      </div>
    </header>
  );
}
