/**
 * Основа дизайн-системы: то, из чего собраны все экраны.
 *
 * Библиотеки здесь нет намеренно. Кнопка, карточка и полоса прогресса —
 * это тридцать строк CSS, а любая UI-библиотека принесла бы собственную
 * систему тем, свои отступы и полсотни килобайт ради этих тридцати строк.
 *
 * Доступность заложена в сами компоненты, а не оставлена на экраны:
 * кнопка без подписи требует `label` типом, и забыть его нельзя.
 */

import type { ReactNode } from 'react';

// --- контейнер экрана -------------------------------------------------------

/**
 * Обёртка экрана: горизонтальные поля и запас снизу под навигацию.
 *
 * Запас берётся из токена, а не задаётся числом на каждом экране: иначе
 * на одном из них последняя карточка неизбежно окажется под панелью,
 * и заметят это уже на телефоне.
 */
export function PageContainer({
  children,
  className = '',
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`page ${className}`.trim()}>{children}</div>;
}

// --- заголовки --------------------------------------------------------------

export function SectionHeader({
  title,
  action,
}: {
  title: string;
  action?: ReactNode;
}) {
  return (
    <div className="section-header">
      <h2>{title}</h2>
      {action}
    </div>
  );
}

// --- кнопки -----------------------------------------------------------------

interface ButtonProps {
  children: ReactNode;
  onClick?: () => void;
  type?: 'button' | 'submit';
  disabled?: boolean;
  /** Растянуть на всю ширину — для главного действия экрана. */
  wide?: boolean;
  icon?: ReactNode;
}

export function PrimaryButton({
  children,
  onClick,
  type = 'button',
  disabled,
  wide,
  icon,
}: ButtonProps) {
  return (
    <button
      type={type}
      className={`btn btn-primary${wide ? ' btn-wide' : ''}`}
      onClick={onClick}
      disabled={disabled}
    >
      {icon}
      <span>{children}</span>
    </button>
  );
}

export function SecondaryButton({
  children,
  onClick,
  type = 'button',
  disabled,
  wide,
  icon,
}: ButtonProps) {
  return (
    <button
      type={type}
      className={`btn btn-secondary${wide ? ' btn-wide' : ''}`}
      onClick={onClick}
      disabled={disabled}
    >
      {icon}
      <span>{children}</span>
    </button>
  );
}

/**
 * Кнопка из одной иконки.
 *
 * `label` обязателен типом: иконка без подписи для экранного диктора —
 * это кнопка «графика», и назначение её узнать неоткуда.
 */
export function IconButton({
  label,
  icon,
  onClick,
  disabled,
}: {
  label: string;
  icon: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className="icon-btn"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
    >
      {icon}
    </button>
  );
}

// --- признаки состояния -----------------------------------------------------

export type Tone = 'neutral' | 'success' | 'danger' | 'warning' | 'navy';

/**
 * Небольшой значок состояния.
 *
 * Цвет тут вспомогательный: рядом с ним всегда есть слово. Смысл,
 * закодированный одним цветом, не читается ни при дальтонизме, ни на
 * солнце, ни в чёрно-белой печати скриншота.
 */
export function StatusBadge({
  children,
  tone = 'neutral',
  dot = false,
}: {
  children: ReactNode;
  tone?: Tone;
  dot?: boolean;
}) {
  return (
    <span className={`badge badge-${tone}`}>
      {dot && <span className="badge-dot" aria-hidden="true" />}
      {children}
    </span>
  );
}

// --- показатели -------------------------------------------------------------

/**
 * Один компактный показатель: подпись и значение.
 *
 * Значение крупнее подписи и темнее её — на такой блок смотрят мельком,
 * и цифра должна читаться раньше, чем название.
 */
export function MetricCard({
  label,
  value,
  hint,
  strong = false,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  strong?: boolean;
}) {
  return (
    <div className={`metric${strong ? ' metric-strong' : ''}`}>
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
      {hint && <span className="metric-hint">{hint}</span>}
    </div>
  );
}

/**
 * Тонкая полоса прогресса.
 *
 * Всегда с числами рядом: доля без числителя и знаменателя ничего
 * не сообщает, а на экране, по которому считают рабочее время, —
 * ещё и вводит в заблуждение.
 */
export function ProgressBar({
  value,
  max,
  label,
  tone = 'navy',
}: {
  value: number;
  max: number;
  /** Что именно показывает полоса — уходит в aria-label. */
  label: string;
  tone?: 'navy' | 'success';
}) {
  const share = max > 0 ? Math.min(Math.max(value / max, 0), 1) : 0;
  return (
    <div
      className={`progress progress-${tone}`}
      role="progressbar"
      aria-label={label}
      aria-valuenow={Math.round(share * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className="progress-fill" style={{ width: `${share * 100}%` }} />
    </div>
  );
}

// --- строки списка ----------------------------------------------------------

export function ListItem({
  icon,
  title,
  subtitle,
  trailing,
  onClick,
}: {
  icon?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  trailing?: ReactNode;
  onClick?: () => void;
}) {
  const content = (
    <>
      {icon && <span className="list-icon">{icon}</span>}
      <span className="list-text">
        <span className="list-title">{title}</span>
        {subtitle && <span className="list-subtitle">{subtitle}</span>}
      </span>
      {trailing && <span className="list-trailing">{trailing}</span>}
    </>
  );

  if (!onClick) {
    return <div className="list-item">{content}</div>;
  }
  return (
    <button type="button" className="list-item list-item-action" onClick={onClick}>
      {content}
    </button>
  );
}

// --- карточка ---------------------------------------------------------------

export function Card({
  children,
  className = '',
}: {
  children: ReactNode;
  className?: string;
}) {
  return <section className={`card ${className}`.trim()}>{children}</section>;
}
