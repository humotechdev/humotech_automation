/**
 * Состояния экрана: загрузка, пусто, ошибка, нет сети.
 *
 * Пустой белый экран — тоже состояние, просто необъяснённое: человек
 * не знает, идёт ли загрузка, ничего не нашлось или всё сломалось, и
 * единственное доступное действие — закрыть приложение.
 *
 * Скелет повторяет форму будущего содержимого, а не крутит колесо
 * посреди экрана. Разница в том, что после подстановки данных ничего
 * не прыгает: блоки уже стоят на своих местах.
 */

import type { ReactNode } from 'react';

import { AlertIcon, OfflineIcon, RefreshIcon } from './icons';
import { SecondaryButton } from './primitives';

// --- скелет -----------------------------------------------------------------

/**
 * Прямоугольник на месте будущего текста или карточки.
 *
 * Анимации нет: мерцание на каждом блоке — это движение на весь экран,
 * которое при `prefers-reduced-motion` пришлось бы отключать целиком.
 * Достаточно того, что место занято.
 */
export function LoadingSkeleton({
  height = 16,
  width = '100%',
  radius = 8,
  className = '',
}: {
  height?: number;
  width?: number | string;
  radius?: number;
  className?: string;
}) {
  return (
    <span
      className={`skeleton ${className}`.trim()}
      style={{ height, width, borderRadius: radius }}
      aria-hidden="true"
    />
  );
}

/** Скелет карточки: заголовок и две строки. */
export function SkeletonCard({ lines = 2 }: { lines?: number }) {
  return (
    <div className="card skeleton-card">
      <LoadingSkeleton height={20} width="55%" />
      {Array.from({ length: lines }, (_, index) => (
        <LoadingSkeleton key={index} height={14} width={index ? '70%' : '90%'} />
      ))}
    </div>
  );
}

/**
 * Область загрузки целиком.
 *
 * Для экранного диктора это одно сообщение «Загружаем», а не десяток
 * пустых прямоугольников: `aria-busy` на контейнере и живая область,
 * которая объявит смену состояния.
 */
export function LoadingScreen({
  label = 'Загружаем данные',
  cards = 3,
}: {
  label?: string;
  cards?: number;
}) {
  return (
    <div className="stack" aria-busy="true">
      <span className="visually-hidden" role="status">
        {label}
      </span>
      {Array.from({ length: cards }, (_, index) => (
        <SkeletonCard key={index} lines={index === 0 ? 3 : 2} />
      ))}
    </div>
  );
}

// --- пусто ------------------------------------------------------------------

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="state-block">
      {icon && <span className="state-icon">{icon}</span>}
      <p className="state-title">{title}</p>
      {description && <p className="state-text">{description}</p>}
      {action}
    </div>
  );
}

// --- ошибка -----------------------------------------------------------------

/**
 * Ошибка с действием.
 *
 * Кнопка «Обновить» обязательна: сообщение без выхода превращает сбой
 * сети в тупик, из которого человек выходит закрытием приложения.
 */
export function ErrorState({
  title = 'Не получилось загрузить',
  message,
  onRetry,
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="state-block state-error" role="alert">
      <span className="state-icon state-icon-danger">
        <AlertIcon size={28} />
      </span>
      <p className="state-title">{title}</p>
      {message && <p className="state-text">{message}</p>}
      {onRetry && (
        <SecondaryButton onClick={onRetry} icon={<RefreshIcon size={18} />}>
          Обновить
        </SecondaryButton>
      )}
    </div>
  );
}

// --- нет сети ---------------------------------------------------------------

/**
 * Узкая полоса сверху вместо экрана-заглушки.
 *
 * Уже загруженное показывать можно и нужно: смотреть вчерашние отметки
 * без сети безопасно. Отметиться — нельзя, и это запрещает сервер,
 * а не эта полоса.
 */
export function OfflineBanner({ onRetry }: { onRetry?: () => void }) {
  return (
    <NoticeBanner
      icon={<OfflineIcon size={18} />}
      text="Нет связи. Показаны последние загруженные данные."
      onRetry={onRetry}
    />
  );
}

/**
 * Та же полоса для неудачного обновления уже открытого кабинета.
 *
 * Значок здесь не «нет сети»: сеть есть, ответил сервер, и путать эти
 * два случая нельзя — от них зависит, ждать или звонить в отдел кадров.
 */
export function StaleBanner({
  message,
  onRetry,
}: {
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <NoticeBanner
      icon={<AlertIcon size={18} />}
      text={message ?? 'Не удалось обновить данные. Показано последнее.'}
      onRetry={onRetry}
    />
  );
}

function NoticeBanner({
  icon,
  text,
  onRetry,
}: {
  icon: ReactNode;
  text: string;
  onRetry?: () => void;
}) {
  return (
    <div className="offline-banner" role="status">
      {icon}
      <span>{text}</span>
      {onRetry && (
        <button type="button" className="offline-retry" onClick={onRetry}>
          Ещё раз
        </button>
      )}
    </div>
  );
}
