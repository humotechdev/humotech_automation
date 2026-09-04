/**
 * Постоянная нижняя навигация из пяти разделов.
 *
 * Пять — потому что шестой уже не попадает под палец на ширине 360 px.
 * «Профиль» отдельной вкладкой не стоит намеренно: его открывают раз
 * в месяц, и вкладка ради этого забрала бы место у того, чем пользуются
 * каждый день. Он живёт за аватаром в шапке.
 *
 * Отметка — по центру и приподнята: это единственное действие, ради
 * которого приложение открывают стоя, на ходу, одной рукой.
 *
 * Панель непрозрачная. Полупрозрачная с размытием стоила бы постоянной
 * перерисовки на скролле и на слабом телефоне превращает прокрутку
 * в рывки.
 *
 * Нижняя безопасная зона — часть самой панели, а не отступ под ней:
 * иначе между панелью и краем экрана видна полоса фона, а на айфоне
 * туда же попадает системная черта.
 */

import { ChartIcon, HistoryIcon, HomeIcon, QrIcon, RequestsIcon } from './icons';

export type Tab = 'home' | 'stats' | 'scan' | 'history' | 'requests';

const TABS: Array<{
  key: Tab;
  label: string;
  Icon: (props: { size?: number }) => React.ReactElement;
}> = [
  { key: 'home', label: 'Главная', Icon: HomeIcon },
  { key: 'stats', label: 'Статистика', Icon: ChartIcon },
  { key: 'scan', label: 'Отметка', Icon: QrIcon },
  { key: 'history', label: 'История', Icon: HistoryIcon },
  { key: 'requests', label: 'Заявки', Icon: RequestsIcon },
];

export function BottomNavigation({
  active,
  onChange,
}: {
  active: Tab;
  onChange: (tab: Tab) => void;
}) {
  return (
    <nav className="bottom-nav" aria-label="Разделы приложения">
      {TABS.map(({ key, label, Icon }) => {
        const current = key === active;
        const scan = key === 'scan';
        return (
          <button
            key={key}
            type="button"
            className={`nav-item${current ? ' nav-item-active' : ''}${
              scan ? ' nav-item-scan' : ''
            }`}
            onClick={() => onChange(key)}
            // Для диктора это именно текущий раздел, а не просто
            // кнопка другого цвета.
            aria-current={current ? 'page' : undefined}
          >
            <span className="nav-icon">
              <Icon size={scan ? 24 : 22} />
            </span>
            <span className="nav-label">{label}</span>
          </button>
        );
      })}
    </nav>
  );
}
