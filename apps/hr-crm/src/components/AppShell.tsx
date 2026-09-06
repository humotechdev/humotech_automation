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
import { Link } from 'react-router-dom';

import { Logo } from './Logo';
import { Icon, type IconName } from './nav-icons';
import { useSession } from '../features/auth/session';

type Item = {
  key: string;
  title: string;
  icon: IconName;
  /** Адрес готового раздела. Без него пункт показан недоступным. */
  to?: string;
};

const WORKSPACE: Item[] = [
  { key: 'home', title: 'Главная', icon: 'home', to: '/' },
  { key: 'employees', title: 'Сотрудники', icon: 'users', to: '/employees' },
  { key: 'attendance', title: 'Посещаемость', icon: 'clock', to: '/attendance' },
  { key: 'requests', title: 'Заявки', icon: 'doc', to: '/requests' },
  { key: 'offices', title: 'Офисы и регионы', icon: 'pin' },
  { key: 'analytics', title: 'Аналитика', icon: 'chart' },
  { key: 'questions', title: 'Обращения', icon: 'chat' },
];

const MANAGEMENT: Item[] = [
  { key: 'reports', title: 'Отчёты', icon: 'report' },
  { key: 'knowledge', title: 'База знаний', icon: 'book' },
  { key: 'notifications', title: 'Уведомления', icon: 'bell' },
  { key: 'admin', title: 'Администрирование', icon: 'admin' },
  { key: 'settings', title: 'Настройки', icon: 'settings' },
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

  return (
    <div className="shell">
      <nav className="side" aria-label="Разделы">
        <div className="side__brand">
          <Logo size={40} />
          <span className="side__brand-text">
            <span className="side__name">HUMOTECH</span>
            <span className="side__tagline">HR CONTROL SYSTEM</span>
          </span>
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
          <Icon name="logout" />
          Выйти
        </button>
      </nav>

      <div className="work">
        <header className="topbar">
          <p className="crumbs">
            <span>Рабочее пространство</span>
            <span className="crumbs__sep">/</span>
            <span className="crumbs__here">{breadcrumb}</span>
          </p>
          <div className="topbar__tools">
            <EmployeeSearch />
            <Bell />
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
              <Icon name={item.icon} />
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

/**
 * Поиск сотрудника. Отдельного поискового сервиса в backend нет —
 * используется параметр `search` существующего состава смены.
 */
function EmployeeSearch() {
  const [text, setText] = useState('');
  return (
    <label className="find">
      <Icon name="search" size={16} />
      <input
        type="search"
        placeholder="Поиск сотрудника"
        value={text}
        onChange={(event) => setText(event.target.value)}
        aria-label="Поиск сотрудника"
      />
    </label>
  );
}

/** Уведомления. Числа нет: право `notifications.read` есть не у всех. */
function Bell() {
  return (
    <button type="button" className="tool" aria-label="Уведомления">
      <Icon name="bell" size={18} />
    </button>
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
        <Icon name="chevron" size={14} />
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
