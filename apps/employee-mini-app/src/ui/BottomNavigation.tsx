/**
 * Постоянная нижняя навигация из четырёх разделов.
 *
 * «Профиль» стал вкладкой: раньше он жил за аватаром в шапке, и найти
 * его удавалось не всем. Аватар открывает его по-прежнему — просто
 * теперь у раздела есть и видимое место.
 *
 * Сканера среди вкладок нет. Он никуда не делся и остаётся главным
 * действием приложения, но открывается с карточки статуса и синей
 * кнопкой бота: шестая вкладка не помещается под палец на ширине
 * 320 px, а пятая забрала бы место у «Профиля».
 *
 * Активный пункт — синяя иконка и синяя подпись, без цветного
 * прямоугольника под ними: заливка на панели высотой 62 px читается
 * как кнопка, которую нажали и не отпустили.
 *
 * Панель непрозрачная. Полупрозрачная с размытием стоила бы постоянной
 * перерисовки на скролле и на слабом телефоне превращает прокрутку
 * в рывки.
 *
 * Нижняя безопасная зона — часть самой панели, а не отступ под ней:
 * иначе между панелью и краем экрана видна полоса фона, а на айфоне
 * туда же попадает системная черта.
 */

import { HistoryIcon, HomeIcon, RequestsIcon, UserIcon } from './icons';

/**
 * Разделы приложения.
 *
 * `scan`, `stats` и `notes` в панели не показываются, но остаются
 * значениями типа: на сканер уходят с карточки статуса, на статистику —
 * из карточки недели, на уведомления — с колокольчика и с карточки
 * объявления. Выкинуть их из типа значило бы сломать возврат родной
 * кнопкой «назад», который различает главную и всё остальное.
 */
export type Tab =
  | 'home'
  | 'scan'
  | 'stats'
  | 'notes'
  | 'history'
  | 'requests'
  | 'profile';

const TABS: Array<{
  key: Tab;
  label: string;
  Icon: (props: { size?: number }) => React.ReactElement;
}> = [
  { key: 'home', label: 'Главная', Icon: HomeIcon },
  { key: 'history', label: 'Отметки', Icon: HistoryIcon },
  { key: 'requests', label: 'Заявки', Icon: RequestsIcon },
  { key: 'profile', label: 'Профиль', Icon: UserIcon },
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
        return (
          <button
            key={key}
            type="button"
            className={`nav-item${current ? ' nav-item-active' : ''}`}
            onClick={() => onChange(key)}
            // Для диктора это именно текущий раздел, а не просто
            // кнопка другого цвета.
            aria-current={current ? 'page' : undefined}
          >
            <span className="nav-icon">
              <Icon size={22} />
            </span>
            <span className="nav-label">{label}</span>
          </button>
        );
      })}
    </nav>
  );
}
