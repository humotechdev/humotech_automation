/**
 * Иконки страницы входа. Свои, а не библиотека: их пять, и тянуть ради
 * них пакет с тысячей значков — это мегабайты в сборке и чужой стиль.
 *
 * Все рисуются `currentColor`, поэтому цвет задаёт CSS, а не разметка.
 */

type Props = { className?: string };

export function UserIcon({ className }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"
         fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
      <circle cx="12" cy="8" r="3.4" />
      <path d="M4.8 19.4c0-3.4 3.2-5.6 7.2-5.6s7.2 2.2 7.2 5.6" />
    </svg>
  );
}

export function LockIcon({ className }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"
         fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
      <rect x="4.8" y="10.2" width="14.4" height="9.4" rx="2.2" />
      <path d="M8.4 10.2V7.6a3.6 3.6 0 0 1 7.2 0v2.6" />
    </svg>
  );
}

export function EyeIcon({ className }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"
         fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
      <path d="M2.6 12S6 5.8 12 5.8 21.4 12 21.4 12 18 18.2 12 18.2 2.6 12 2.6 12Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

export function EyeOffIcon({ className }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"
         fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
      <path d="M4 4.4 19.6 20" />
      <path d="M9.6 6.2A9.6 9.6 0 0 1 12 5.8c6 0 9.4 6.2 9.4 6.2a17 17 0 0 1-3.1 3.9" />
      <path d="M6.3 8.1A17 17 0 0 0 2.6 12S6 18.2 12 18.2a9.7 9.7 0 0 0 3.2-.55" />
      <path d="M10.1 10.2a3 3 0 0 0 4 4.1" />
    </svg>
  );
}

export function GlobeIcon({ className }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"
         fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="12" cy="12" r="8.4" />
      <path d="M3.6 12h16.8M12 3.6c2.1 2.3 3.2 5.3 3.2 8.4s-1.1 6.1-3.2 8.4c-2.1-2.3-3.2-5.3-3.2-8.4S9.9 5.9 12 3.6Z" />
    </svg>
  );
}

export function ChevronIcon({ className }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"
         fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M6.5 9.5 12 15l5.5-5.5" />
    </svg>
  );
}

export function ShieldIcon({ className }: Props) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"
         fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
         strokeLinejoin="round">
      <path d="M12 3.2 5.4 5.9v5.3c0 4 2.7 7.6 6.6 9.6 3.9-2 6.6-5.6 6.6-9.6V5.9Z" />
      <path d="m9.2 12.1 2 2 3.6-3.9" />
    </svg>
  );
}
