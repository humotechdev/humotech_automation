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

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';

import { BrandLockup } from './Logo';
import { NotificationBell } from './NotificationBell';
import { backdropImage } from '../features/shell/backdrop';
import { AppIcon, ICON_SIZE, type AppIconName } from './AppIcon';
import { useSession } from '../features/auth/session';
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
];

const MANAGEMENT: Item[] = [
  { key: 'reports', title: 'Отчёты', icon: 'report', to: '/reports' },
  { key: 'knowledge', title: 'База знаний', icon: 'book', to: '/knowledge' },
  { key: 'notifications', title: 'Уведомления', icon: 'bell', to: '/notifications' },
  { key: 'admin', title: 'Администрирование', icon: 'admin', to: '/admin' },
  { key: 'settings', title: 'Настройки', icon: 'settings', to: '/settings' },
];

/** Человеческие названия ролей. Незнакомый код показывается как есть. */
const ROLE_NAMES: Record<string, string> = {
  HR_ADMIN_LOCAL: 'HR-администратор',
  HR_MANAGER: 'HR-менеджер',
  OFFICE_MANAGER: 'Руководитель офиса',
  VIEWER: 'Наблюдатель',
};

type Props = {
  children: ReactNode;
  /** Счётчики у разделов. Только настоящие, только пришедшие с сервера. */
  badges?: Record<string, number>;
  breadcrumb: string;
  /** Какой пункт навигации подсвечен. */
  section?: string;
};

export function AppShell({ children, badges = {}, breadcrumb, section = 'home' }: Props) {
  const session = useSession();
  const user = session.status === 'authenticated' ? session.user : null;

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
          <Group title="Рабочее пространство" items={WORKSPACE} badges={badges}
                 active={section} />
          <div className="side__rule" />
          <Group title="Управление" items={MANAGEMENT} badges={badges} active={section} />
        </div>

        <div className="side__user">
          <span className="avatar" aria-hidden="true">{initials(user?.email)}</span>
          <span className="side__who">
            <span className="side__role">{roleName(user?.roles)}</span>
            <span className="side__scope">{user?.organization_code ?? ''}</span>
          </span>
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

      <div className="work">
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
            <span className="avatar avatar--sm" aria-hidden="true">
              {initials(user?.email)}
            </span>
          </div>
        </header>
        <main className="canvas">{children}</main>
      </div>
    </div>
  );
}

function Group({ title, items, badges, active }: {
  title: string; items: Item[]; badges: Record<string, number>; active: string;
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
              {count !== undefined && count > 0 && (
                <span className="nav__badge">{count}</span>
              )}
            </>
          );
          return (
            <li key={item.key}>
              {item.to ? (
                <Link
                  to={item.to}
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
