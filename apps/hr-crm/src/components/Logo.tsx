/**
 * Фирменный знак HUMOTECH.
 *
 * Это те же пиксели, что на аватарке Telegram-бота
 * (`apps/employee-telegram-bot/ChatGPT Image Sep 4, 2026, 04_16_29 PM.png`):
 * знак не перерисован и не заменён похожей иконкой — из исходника взята
 * форма, а цвет переведён в чистый чёрный на прозрачном фоне. Поэтому
 * знак одинаков в боте и в CRM, а не «примерно такой же».
 *
 * Отдельный компонент, а не кусок разметки страницы: тот же знак
 * понадобится в шапке CRM, в отчётах и на узких экранах.
 */

import mark from '../assets/humotech-mark.png';

type Props = {
  /** Сторона белого квадрата в пикселях. */
  size?: number;
  className?: string;
};

export function Logo({ size = 72, className }: Props) {
  return (
    <span
      className={className ? `logo ${className}` : 'logo'}
      // Поля и скругление считаются от стороны квадрата. В пикселях, а
      // не в процентах: процентный `padding` в CSS отмеряется от ширины
      // родителя, и знак схлопывается в ноль тем сильнее, чем шире блок.
      style={{
        width: size,
        height: size,
        padding: Math.round(size * 0.14),
        borderRadius: Math.round(size * 0.28),
      }}
    >
      <img src={mark} alt="" width={size} height={size} />
    </span>
  );
}

/** Знак вместе с названием — так он стоит и в макете входа. */
export function Wordmark({ size = 72 }: { size?: number }) {
  return (
    <div className="wordmark">
      <Logo size={size} />
      <span className="wordmark__text">
        <span className="wordmark__name">HUMOTECH</span>
        <span className="wordmark__tagline">HR CONTROL SYSTEM</span>
      </span>
    </div>
  );
}
