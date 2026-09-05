/**
 * Иконки — рисованные здесь, а не библиотекой.
 *
 * Их шестнадцать. Библиотека ради шестнадцати иконок стоила бы больше,
 * чем весь остальной прирост сборки, и принесла бы ещё пятьсот, которые
 * никогда не понадобятся.
 *
 * Эмодзи в роли интерфейсных иконок не годятся: у каждой платформы свой
 * рисунок и своя ширина, они не наследуют цвет текста и не читаются
 * экранным диктором как элемент управления.
 *
 * Все иконки одного размера и рисуются `currentColor` — цвет задаёт то
 * место, куда иконку поставили, а не она сама.
 */

interface IconProps {
  /** Размер стороны в пикселях. По умолчанию 20 — под текст 15–16 px. */
  size?: number;
  className?: string;
}

function svg(path: React.ReactNode) {
  return function Icon({ size = 20, className }: IconProps) {
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
        className={className}
        // Иконка здесь всегда рядом с текстом или внутри кнопки,
        // у которой есть aria-label. Дублировать смысл незачем.
        aria-hidden="true"
        focusable="false"
      >
        {path}
      </svg>
    );
  };
}

export const HomeIcon = svg(
  <>
    <path d="M3 10.5 12 3l9 7.5" />
    <path d="M5 9.5V20h14V9.5" />
  </>,
);

export const ChartIcon = svg(
  <>
    <path d="M4 20V10" />
    <path d="M10 20V4" />
    <path d="M16 20v-7" />
    <path d="M22 20H2" />
  </>,
);

export const QrIcon = svg(
  <>
    <rect x="3" y="3" width="7" height="7" rx="1.5" />
    <rect x="14" y="3" width="7" height="7" rx="1.5" />
    <rect x="3" y="14" width="7" height="7" rx="1.5" />
    <path d="M14 14h3v3h-3z" />
    <path d="M20 14v3" />
    <path d="M14 20h7" />
  </>,
);

export const HistoryIcon = svg(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3.5 2" />
  </>,
);

export const RequestsIcon = svg(
  <>
    <path d="M7 3h10l3 3v15H4V6z" />
    <path d="M8 11h8" />
    <path d="M8 15h5" />
  </>,
);

export const ChevronRightIcon = svg(<path d="m9 5 7 7-7 7" />);

export const CheckIcon = svg(<path d="m4 12.5 5 5L20 6.5" />);

export const CloseIcon = svg(
  <>
    <path d="m6 6 12 12" />
    <path d="m18 6-12 12" />
  </>,
);

export const AlertIcon = svg(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7.5v5.5" />
    <path d="M12 16.5h.01" />
  </>,
);

export const ClockIcon = svg(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7.5V12l3 1.8" />
  </>,
);

export const OfficeIcon = svg(
  <>
    <path d="M4 21V5l8-2v18" />
    <path d="M12 9h8v12" />
    <path d="M4 21h17" />
  </>,
);

export const CalendarIcon = svg(
  <>
    <rect x="3" y="5" width="18" height="16" rx="2" />
    <path d="M3 10h18" />
    <path d="M8 3v4" />
    <path d="M16 3v4" />
  </>,
);

export const MedicalIcon = svg(
  <>
    <rect x="3" y="7" width="18" height="13" rx="2" />
    <path d="M9 7V4h6v3" />
    <path d="M12 11v5" />
    <path d="M9.5 13.5h5" />
  </>,
);

export const PlaneIcon = svg(
  <path d="M21 15.5 3 9.8l3-1.6 4.6 1.4 4-3.4a2 2 0 0 1 2.6 3l-3 3.4z" />,
);

export const OfflineIcon = svg(
  <>
    <path d="M2 3 22 21" />
    <path d="M5 12.5a10 10 0 0 1 4-2.4" />
    <path d="M8.5 16a5 5 0 0 1 2.2-1.3" />
    <path d="M12 20h.01" />
    <path d="M19.5 12.5a10 10 0 0 0-4.6-2.6" />
  </>,
);

export const RefreshIcon = svg(
  <>
    <path d="M20 11a8 8 0 1 0-.6 4" />
    <path d="M20 5v6h-6" />
  </>,
);

export const CameraIcon = svg(
  <>
    <path d="M4 8h3l1.5-2h7L17 8h3v12H4z" />
    <circle cx="12" cy="13.5" r="3.5" />
  </>,
);

export const DocumentIcon = svg(
  <>
    <path d="M7 3h7l5 5v13H7z" />
    <path d="M14 3v5h5" />
  </>,
);

export const ImageIcon = svg(
  <>
    <path d="M4 5h16v14H4z" />
    <circle cx="9" cy="10" r="1.6" />
    <path d="m4 16.5 4.5-4 3.5 3 3-2.5 5 4.5" />
  </>,
);
