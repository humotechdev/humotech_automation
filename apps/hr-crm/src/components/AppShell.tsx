/**
 * Оболочка CRM: боковая навигация, верхняя панель и рабочая область.
 *
 * От края до края: ни `max-width`, ни внешних полей, ни общей тени.
 * Серый здесь только внутри рабочей области — как фон под карточками,
 * а не рамка вокруг приложения.
 *
 * Разделы, кроме «Главной», ещё не сделаны. Они показаны неактивными и
 * никуда не ведут: ссылка на несуществующую страницу — это 404 вместо
 * ответа, а спрятать их значило бы скрыть от человека план системы.
 */

import {
  createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState,
  type ReactNode,
} from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';

import { BrandLockup } from './Logo';
import { NotificationBell } from './NotificationBell';
import { backdropImage } from '../features/shell/backdrop';
import { AppIcon, ICON_SIZE, type AppIconName } from './AppIcon';
import { useSession } from '../features/auth/session';
import { useBadges } from '../features/shell/badges';
import { placeOf, rememberPlace } from '../features/shell/places';
import { useScrollMemory } from '../features/shell/scroll';
import { GlobalEmployeeSearch } from './GlobalEmployeeSearch';

type Item = {
  key: string;
  title: string;
  icon: AppIconName;
  /** Адрес готового раздела. Без него пункт показан недоступным. */
  to?: string;
};

const WORKSPACE: Item[] = [
  { key: 'home', title: 'Главная', icon: 'home', to: '/' },
  { key: 'employees', title: 'Сотрудники', icon: 'users', to: '/employees' },
  { key: 'attendance', title: 'Посещаемость', icon: 'clock', to: '/attendance' },
  { key: 'requests', title: 'Заявки', icon: 'doc', to: '/requests' },
  { key: 'offices', title: 'Офисы и регионы', icon: 'pin', to: '/offices' },
  { key: 'analytics', title: 'Аналитика', icon: 'chart', to: '/analytics' },
  { key: 'questions', title: 'Обращения', icon: 'chat', to: '/questions' },
  { key: 'surveys', title: 'Опросы', icon: 'sheet', to: '/surveys' },
];

/*
 * «Базы знаний» и «Настроек» здесь нет намеренно.
 *
 * Материалы ассистента остались на сервере — бот отвечает по ним
 * по-прежнему, — но отдельного раздела для кадровика у них больше нет:
 * он туда не ходил, а пункт меню обещал работу, которой там не было.
 *
 * Настройки не вынесены в отдельное место, потому что настройки
 * бывают только у чего-то: структура компании и доступ — в
 * «Администрировании», адреса, геозона и QR-точки — в «Офисах и
 * регионах». Общая страница «Настройки» неизбежно становится свалкой
 * того, чему не нашлось места.
 */
const MANAGEMENT: Item[] = [
  { key: 'reports', title: 'Отчёты', icon: 'report', to: '/reports' },
  { key: 'notifications', title: 'Уведомления', icon: 'bell', to: '/notifications' },
  { key: 'admin', title: 'Администрирование', icon: 'admin', to: '/administration' },
];

/** Человеческие названия ролей. Незнакомый код показывается как есть. */
const ROLE_NAMES: Record<string, string> = {
  HR_ADMIN_LOCAL: 'HR-администратор',
  HR_MANAGER: 'HR-менеджер',
  OFFICE_MANAGER: 'Руководитель офиса',
  VIEWER: 'Наблюдатель',
};

type Meta = {
  breadcrumb: string;
  /** Какой пункт навигации подсвечен. */
  section: string;
  /** Счётчики у разделов. Только настоящие, только пришедшие с сервера. */
  badges?: Record<string, number>;
};

type Props = {
  children: ReactNode;
  badges?: Record<string, number>;
  breadcrumb: string;
  section?: string;
};

/*
 * Оболочка живёт в маршруте-раскладке и не пересоздаётся при переходах.
 *
 * Раньше каждая страница рендерила `AppShell` сама. Страницы — разные
 * компоненты, и при переходе React разбирал всё дерево целиком: меню,
 * верхнюю панель, колокольчик, поиск и данные страницы. Со стороны это
 * выглядело как перезагрузка с нуля, хотя адрес менялся без запроса
 * документа.
 *
 * Теперь `ShellLayout` стоит над маршрутами и остаётся на месте, а
 * `AppShell` внутри страницы только сообщает ему заголовок, раздел и
 * счётчики. Без раскладки над собой (отдельный рендер страницы) он, как
 * и прежде, рисует оболочку сам.
 */
const ShellContext = createContext<((meta: Meta) => void) | null>(null);

function sameMeta(one: Meta, two: Meta): boolean {
  return one.breadcrumb === two.breadcrumb
    && one.section === two.section
    && JSON.stringify(one.badges ?? {}) === JSON.stringify(two.badges ?? {});
}

/** Оболочка для маршрутов кабинета: страница подставляется в `<Outlet />`. */
export function ShellLayout() {
  const [meta, setMeta] = useState<Meta>({ breadcrumb: '', section: '' });
  const publish = useCallback((next: Meta) => {
    setMeta((was) => (sameMeta(was, next) ? was : next));
  }, []);
  return (
    <ShellContext.Provider value={publish}>
      <ShellFrame meta={meta} remember>
        <Outlet />
      </ShellFrame>
    </ShellContext.Provider>
  );
}

export function AppShell({ children, badges, breadcrumb, section = 'home' }: Props) {
  const publish = useContext(ShellContext);
  const badgesKey = JSON.stringify(badges ?? {});

  // До отрисовки кадра: иначе новая страница на один кадр показалась бы
  // с заголовком и подсвеченным пунктом предыдущей.
  useLayoutEffect(() => {
    if (!publish) return;
    publish({ breadcrumb, section, ...(badges ? { badges } : {}) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [publish, breadcrumb, section, badgesKey]);

  if (publish) return <>{children}</>;
  return (
    <ShellFrame meta={{ breadcrumb, section, ...(badges ? { badges } : {}) }}>
      {children}
    </ShellFrame>
  );
}

function ShellFrame({ children, meta, remember = false }: {
  children: ReactNode;
  meta: Meta;
  /** Запоминать адреса разделов и прокрутку страниц. */
  remember?: boolean;
}) {
  const { breadcrumb, section, badges } = meta;
  const session = useSession();
  /*
   * Числа рядом с разделами приходят из общего источника, а не от
   * страницы: иначе они есть только там, где их кто-то посчитал, и
   * исчезают при переходе в соседний раздел. Страница всё ещё может
   * передать свои — они перекрывают общие.
   */
  const shared = useBadges();
  const counters = { ...shared, ...(badges ?? {}) };

  /*
   * Меню на узком экране выезжает поверх содержимого.
   *
   * Состояние живёт здесь, а не в CSS: закрыть меню обязан и переход в
   * раздел, и Escape, и нажатие мимо. На широком экране класс ничего не
   * меняет — там меню стоит колонкой сетки и никуда не выезжает.
   */
  const [menu, setMenu] = useState(false);
  const place = useLocation();
  useEffect(() => setMenu(false), [place.pathname, place.search]);
  useEffect(() => {
    if (!menu) return;
    const stop = new AbortController();
    document.addEventListener(
      'keydown',
      (event) => {
        if (event.key === 'Escape') setMenu(false);
      },
      { signal: stop.signal },
    );
    return () => stop.abort();
  }, [menu]);

  // Меню ведёт в раздел туда, где человек был в нём последний раз: с
  // теми же фильтрами, вкладкой, выбранной строкой и страницей списка.
  if (remember) rememberPlace(place.pathname, place.search);
  const work = useRef<HTMLDivElement>(null);
  useScrollMemory(work, remember ? place.pathname : '', remember);

  return (
    <div className={menu ? 'shell shell--menu' : 'shell'}>
      {/* Декоративная подложка. Снимок подставляется, если он лежит в
          `src/assets/office-backdrop.*`; без файла остаётся светлая
          заливка с мягкими бликами. Оба слоя вне потока и недоступны
          чтению с экрана. */}
      <div
        className="shell__backdrop"
        aria-hidden="true"
        {...(backdropImage ? { style: { backgroundImage: backdropImage } } : {})}
      />
      <div className="shell__wash" aria-hidden="true" />

      <nav className={menu ? 'side side--open' : 'side'} aria-label="Разделы">
        <div className="side__brand">
          <BrandLockup />
        </div>

        <div className="side__scroll">
          <Group title="Рабочее пространство" items={WORKSPACE} badges={counters}
                 active={section} remember={remember} />
          <div className="side__rule" />
          <Group title="Управление" items={MANAGEMENT} badges={counters} active={section}
                 remember={remember} />
        </div>

        <button type="button" className="side__exit" onClick={() => void session.signOut()}>
          <AppIcon name="logout" size={ICON_SIZE.nav} />
          Выйти
        </button>
      </nav>

      {menu && (
        <button
          type="button"
          className="side__scrim"
          aria-label="Закрыть меню разделов"
          onClick={() => setMenu(false)}
        />
      )}

      <div className="work" ref={work}>
        <header className="topbar">
          {/* Кнопка меню видна только там, где меню выезжает: на широком
              экране разделы и так на виду. */}
          <button
            type="button"
            className="tool tool--menu"
            aria-label="Разделы"
            aria-expanded={menu}
            onClick={() => setMenu((was) => !was)}
          >
            <AppIcon name="list" size={ICON_SIZE.title} />
          </button>
          <p className="crumbs">
            <span>Рабочее пространство</span>
            <span className="crumbs__sep">/</span>
            <span className="crumbs__here">{breadcrumb}</span>
          </p>
          <div className="topbar__tools">
            <GlobalEmployeeSearch />
            <NotificationBell />
            <Language />
          </div>
        </header>
        <main className="canvas">{children}</main>
      </div>
    </div>
  );
}

function Group({ title, items, badges, active, remember }: {
  title: string; items: Item[]; badges: Record<string, number>; active: string; remember: boolean;
}) {
  return (
    <>
      <p className="side__group">{title}</p>
      <ul className="side__list">
        {items.map((item) => {
          const count = badges[item.key];
          const inside = (
            <>
              <AppIcon name={item.icon} size={ICON_SIZE.nav} />
              <span>{item.title}</span>
              {/* Ноль не показывается: пустой кружок читается как
                  «ноль чего-то», а не «ничего не ждёт». */}
              {count !== undefined && count > 0 && (
                <span className="nav__badge">{count > 99 ? '99+' : count}</span>
              )}
            </>
          );
          return (
            <li key={item.key}>
              {item.to ? (
                <Link
                  to={remember ? placeOf(item.to) : item.to}
                  className={item.key === active ? 'nav nav--active' : 'nav'}
                  aria-current={item.key === active ? 'page' : undefined}
                >
                  {inside}
                </Link>
              ) : (
                <button
                  type="button"
                  className="nav"
                  aria-disabled
                  disabled
                  title="Раздел будет добавлен следующим этапом"
                >
                  {inside}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </>
  );
}

function Language() {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const stop = new AbortController();
    document.addEventListener(
      'mousedown',
      (event) => {
        if (!box.current?.contains(event.target as Node)) setOpen(false);
      },
      { signal: stop.signal },
    );
    return () => stop.abort();
  }, [open]);

  return (
    <div className="lang lang--top" ref={box}>
      <button
        type="button"
        className="tool tool--wide"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Язык интерфейса: русский"
        onClick={() => setOpen((was) => !was)}
      >
        RU
        <AppIcon name="chevron" size={16} />
      </button>
      {open && (
        <ul className="lang__list" role="listbox" aria-label="Язык интерфейса">
          <li>
            <button type="button" role="option" aria-selected className="lang__option"
                    onClick={() => setOpen(false)}>
              Русский
            </button>
          </li>
        </ul>
      )}
    </div>
  );
}

/**
 * Инициалы из почты или из ФИО.
 *
 * Два слова дают две буквы, одно — первые две: «СН» узнаётся в списке,
 * «СО» из одного слова — нет.
 */
export function initials(source: string | undefined): string {
  if (!source) return '—';
  const name = source.includes('@') ? (source.split('@')[0] ?? '') : source;
  const parts = name.split(/[\s.\-_]+/).filter(Boolean);
  const letters = parts.length > 1
    ? `${parts[0]?.[0] ?? ''}${parts[1]?.[0] ?? ''}`
    : name.slice(0, 2);
  return letters.toUpperCase();
}

export function roleName(roles: string[] | undefined): string {
  if (!roles || roles.length === 0) return 'Без роли';
  const first = roles[0] as string;
  return ROLE_NAMES[first] ?? first;
}
