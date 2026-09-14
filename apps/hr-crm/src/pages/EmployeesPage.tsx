/**
 * Список сотрудников и карточка.
 *
 * Состояние списка живёт в адресе страницы: вернувшись из карточки,
 * человек видит тот же поиск, тот же фильтр и ту же страницу. Хранить
 * это в памяти компонента значило бы терять при каждом обновлении.
 *
 * Пагинация курсорная — так устроен backend. Номеров страниц здесь нет
 * и быть не может: сервер не считает общее число строк, и «страница 25»
 * была бы нарисованной кнопкой, ведущей неизвестно куда.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { messageFor } from '../api/errors';
import { AppShell, initials } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { EmployeeCard } from '../components/EmployeeCard';
import { useBlock, type Block } from '../features/dashboard/data';

/** Вкладки. Одна вкладка может покрывать несколько состояний модели. */
const TABS = [
  { key: 'all', title: 'Все', statuses: [] as string[] },
  { key: 'active', title: 'Активные', statuses: ['ACTIVE', 'PROBATION'] },
  { key: 'inactive', title: 'Неактивные', statuses: ['SUSPENDED'] },
  { key: 'left', title: 'Уволенные', statuses: ['TERMINATED', 'ARCHIVED'] },
] as const;

/** Что считается «активным» в строке под заголовком. Та же пара, что у
 *  вкладки «Активные»: два разных ответа на один вопрос читались бы как
 *  ошибка в числах. */
const ACTIVE_STATUSES = ['ACTIVE', 'PROBATION'];

const STATUS_TITLE: Record<string, string> = {
  ACTIVE: 'Активен',
  PROBATION: 'Испытательный срок',
  SUSPENDED: 'Приостановлен',
  TERMINATED: 'Уволен',
  ARCHIVED: 'В архиве',
};

/** Состояния привязки Telegram в подписи списка. */
const TELEGRAM_TITLE: Record<string, string> = {
  ACTIVE: 'Привязан',
  PENDING: 'Ожидает HR',
  PENDING_CONFIRMATION: 'Ожидает HR',
  REVOKED: 'Отозван',
  BLOCKED: 'Заблокирован',
};

const SIZES = ['8', '16', '32'];

export function EmployeesPage() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.find((t) => t.key === params.get('tab')) ?? TABS[0];
  const search = params.get('search') ?? '';
  const region = params.get('region_id') ?? '';
  const office = params.get('office_id') ?? '';
  const department = params.get('department_id') ?? '';
  const limit = params.get('limit') ?? '8';
  // Номер страницы, а не курсор: по макету страницы перечислены, и
  // перейти нужно на любую. Курсором так нельзя — он ведёт только
  // вперёд, поэтому список принимает сдвиг.
  const page = Math.max(1, Number(params.get('page') ?? '1'));
  const opened = params.get('employee') ?? '';
  const picked = params.get('picked') ?? '';
  // Вид тоже в адресе: человек, вернувшийся из карточки, должен увидеть
  // то же представление, из которого уходил.
  // Карточки — основной рабочий вид списка: он даёт быстрый обзор
  // назначения, графика и статуса без горизонтально разреженной таблицы.
  // Таблица остаётся доступной явным переключателем и по ссылке
  // `?view=table`, чтобы не менять привычный сценарий тех, кто работает
  // со столбцами.
  const view = params.get('view') === 'table' ? 'table' : 'cards';

  const [draft, setDraft] = useState(search);
  useEffect(() => setDraft(search), [search]);

  // Выгрузка ставится в общую очередь отчётов. Здесь только заказ и
  // короткое подтверждение: готовые файлы живут на своей странице, и
  // второе место для той же очереди разошлось бы с ней.
  const [ordering, setOrdering] = useState(false);
  const [ordered, setOrdered] = useState<string | null>(null);

  async function order() {
    if (ordering) return;
    setOrdering(true);
    setOrdered(null);
    try {
      await api.orderExport({
        kind: 'employees',
        fmt: 'xlsx',
        ...(office ? { office_id: office } : {}),
        ...(region ? { region_id: region } : {}),
      });
      setOrdered('Выгрузка поставлена в очередь');
    } catch (error) {
      setOrdered(messageFor(error));
    } finally {
      setOrdering(false);
    }
  }

  /** Меняем адрес, а не состояние: возврат из карточки ничего не теряет. */
  const patch = useCallback(
    (changes: Record<string, string | null>, keepCursor = false) => {
      setParams(
        (was) => {
          const next = new URLSearchParams(was);
          for (const [key, value] of Object.entries(changes)) {
            if (value) next.set(key, value);
            else next.delete(key);
          }
          // Любой новый фильтр начинает выборку заново: страница от
          // прошлого набора указывает в чужие строки.
          if (!keepCursor) next.delete('page');
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  // Ввод в поиск не бьёт по серверу на каждую букву.
  useEffect(() => {
    if (draft === search) return;
    const timer = setTimeout(() => patch({ search: draft || null }), 350);
    return () => clearTimeout(timer);
  }, [draft, search, patch]);

  const filters: api.EmployeeQuery = useMemo(
    () => ({
      ...(search ? { search } : {}),
      ...(region ? { region_id: region } : {}),
      ...(office ? { office_id: office } : {}),
      ...(department ? { department_id: department } : {}),
    }),
    [search, region, office, department],
  );
  const key = `${tab.key}|${search}|${region}|${office}|${department}|${limit}|${page}`;
  const offset = (page - 1) * Number(limit);

  const [list] = useBlock(
    (signal) =>
      api.employees(
        {
          ...filters,
          limit,
          ...(offset ? { offset: String(offset) } : {}),
          ...(tab.statuses.length ? { status: tab.statuses.join(',') } : {}),
        },
        signal,
      ),
    key,
  );

  const [counts] = useBlock(
    (signal) => api.employeeCounts(filters, signal),
    `counts|${search}|${region}|${office}|${department}`,
  );

  const [directory] = useBlock(
    (signal) =>
      Promise.all([api.regions(signal), api.offices(signal), api.departments(signal)]).then(
        ([r, o, d]) => ({ regions: r.items, offices: o.items, departments: d.items }),
      ),
    'directory',
  );

  const offices = useMemo(() => {
    if (directory.state !== 'ready') return [];
    const all = directory.data.offices.filter((o) => o.status === 'ACTIVE');
    return region ? all.filter((o) => o.region_id === region) : all;
  }, [directory, region]);

  const officeCount = offices.length;

  // Состав смены на сегодня. Свой запрос: он не зависит ни от вкладки,
  // ни от фильтров списка, и перезапрашивать его при каждом поиске
  // означало бы дёргать сервер ради неизменного ответа.
  const [today] = useBlock((signal) => api.presence({}, signal), 'presence|today');

  // Новички, именинники и люди без графика — вопрос про всю организацию,
  // а не про показанную страницу. Считает сервер, одним ответом.
  const [highlights] = useBlock(
    (signal) => api.employeeHighlights(
      { ...(office ? { office_id: office } : {}), ...(department ? { department_id: department } : {}) },
      signal,
    ),
    `highlights|${office}|${department}`,
  );

  // Время последнего ответа: по макету оно стоит в подзаголовке. Берётся
  // от прихода данных, а не от открытия страницы, — иначе оно врало бы
  // после каждого фильтра.
  const [updated, setUpdated] = useState<Date | null>(null);
  useEffect(() => {
    if (list.state === 'ready') setUpdated(new Date());
  }, [list]);

  // Всего в выборке — из счётчиков вкладок: по одной странице этого не
  // видно, а номера страниц без общего числа не построить.
  const total = counts.state === 'ready' ? count(counts.data, tab.statuses) : 0;

  const chosen =
    list.state === 'ready'
      ? list.data.items.find((one) => one.id === picked) ?? null
      : null;

  return (
    <AppShell breadcrumb="Сотрудники" section="employees">
      <header className="head head--tight">
        <div>
          <h1 className="head__title">Сотрудники</h1>
          {/* Одна строка вместо четырёх карточек наверху: те же три числа
              читаются здесь за один взгляд и не отодвигают список вниз. */}
          <p className="head__sub head__sub--facts">
            {counts.state === 'ready' && (
              <>
                <span>{plural(count(counts.data, []), 'сотрудник')}</span>
                <span>{count(counts.data, ACTIVE_STATUSES)} активных</span>
              </>
            )}
            {directory.state === 'ready' && (
              <span>{plural(officeCount, 'офис')}</span>
            )}
            {updated && <span className="head__when">{ago(updated)}</span>}
          </p>
        </div>
        <div className="head__actions">
          <button type="button" className="btn" onClick={() => void order()}
                  disabled={ordering}>
            <AppIcon name="report" size={16} />
            {ordering ? 'Ставим в очередь…' : 'Экспорт'}
          </button>
          <Link className="btn btn--dark" to="/employees/new">
            <AppIcon name="plus" size={16} />
            Добавить сотрудника
          </Link>
        </div>
      </header>

      {ordered && (
        <p className="note note--wide" role="status">
          {ordered}
          {' — '}
          <Link to="/reports">файл появится в отчётах</Link>
        </p>
      )}

      <div className="grid">
       <div className="grid__main">
        <section className="sheet">
        <div className="toolbar">
          <label className="find find--wide">
            <AppIcon name="search" size={16} />
            <input
              type="search"
              value={draft}
              placeholder="Поиск по ФИО, должности или Telegram"
              aria-label="Поиск по ФИО, должности или Telegram"
              onChange={(event) => setDraft(event.target.value)}
            />
          </label>
          <Picker label="Офис" value={office} empty="Все офисы" options={offices}
                  onChange={(value) => patch({ office_id: value || null })} />
          <Picker
            label="Отдел"
            value={department}
            empty="Все отделы"
            options={directory.state === 'ready' ? directory.data.departments : []}
            onChange={(value) => patch({ department_id: value || null })}
          />
          {/* Статус и вкладки — одна и та же ось отбора, а не две. Список
              меняет вкладку, вкладка меняет список: два независимых
              состояния для одного признака однажды разошлись бы. */}
          <Picker
            label="Статус"
            value={tab.key === 'all' ? '' : tab.key}
            empty="Все статусы"
            options={TABS.filter((one) => one.key !== 'all')
              .map((one) => ({ id: one.key, name: one.title }))}
            onChange={(value) => patch({ tab: value || null })}
          />
          {/* Переключатель вида. Таблица и карточки показывают ОДИН и тот
              же ответ сервера — это способ смотреть, а не второй запрос. */}
          <div className="modes modes--right" role="group" aria-label="Вид списка">
            <button
              type="button"
              className={view === 'table' ? 'mode mode--on' : 'mode'}
              aria-pressed={view === 'table'}
              onClick={() => patch({ view: 'table' }, true)}
            >
              <AppIcon name="list" size={16} />
              Таблица
            </button>
            <button
              type="button"
              className={view === 'cards' ? 'mode mode--on' : 'mode'}
              aria-pressed={view === 'cards'}
              onClick={() => patch({ view: null }, true)}
            >
              <AppIcon name="grid" size={16} />
              Карточки
            </button>
          </div>
        </div>

        <div className="tabs" role="tablist">
          {TABS.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={item.key === tab.key}
              className={item.key === tab.key ? 'tab tab--on' : 'tab'}
              onClick={() => patch({ tab: item.key === 'all' ? null : item.key })}
            >
              {item.title}
              {counts.state === 'ready' && (
                <span className="tab__count">{count(counts.data, item.statuses)}</span>
              )}
            </button>
          ))}
        </div>

        <Rows block={list}>
          {(data) =>
            data.items.length === 0 ? (
              <p className="empty">
                {search || region || office || department
                  ? 'По этим условиям никого не нашлось.'
                  : 'В доступной области нет сотрудников.'}
              </p>
            ) : view === 'cards' ? (
              <div className="people-cards">
                {data.items.map((person) => (
                  <PersonCard
                    key={person.id}
                    person={person}
                    chosen={person.id === picked}
                    onPick={() => patch({ picked: person.id }, true)}
                  />
                ))}
              </div>
            ) : (
              <div className="scroller">
                <table className="people">
                  <thead>
                    <tr>
                      <th>Сотрудник</th>
                      <th>Должность / отдел</th>
                      <th>Офис</th>
                      <th>График</th>
                      <th>Telegram</th>
                      <th>Статус</th>
                      <th aria-label="Открыть" />
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((person) => (
                      <tr
                        key={person.id}
                        tabIndex={0}
                        onClick={() => patch({ picked: person.id }, true)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') patch({ picked: person.id }, true);
                        }}
                        className={person.id === picked ? 'people__row--on' : undefined}
                      >
                        <td>
                          <span className="who">
                            <span className="avatar">{initials(person.full_name)}</span>
                            <span className="who__text">
                              <span className="who__name">{person.full_name}</span>
                              <span className="who__id">{person.employee_number ?? '—'}</span>
                            </span>
                          </span>
                        </td>
                        <td>
                          <Two
                            first={person.current_assignment?.position_name}
                            second={person.current_assignment?.department_name}
                          />
                        </td>
                        <td>
                          <Two
                            first={person.current_assignment?.office_name}
                            second={person.current_assignment?.region_name}
                          />
                        </td>
                        <td>
                          {person.current_schedule ? (
                            <Two
                              first={person.current_schedule.name}
                              second={hours(person.current_schedule.weekly_minutes)}
                            />
                          ) : (
                            <span className="muted">Не назначен</span>
                          )}
                        </td>
                        <td>
                          {person.telegram_state === 'ACTIVE' ? (
                            <span className="pill">
                              <AppIcon name="send" size={16} />
                              Привязан
                            </span>
                          ) : person.telegram_state ? (
                            <span className="pill">
                              <AppIcon name="clock" size={16} />
                              {TELEGRAM_TITLE[person.telegram_state] ?? person.telegram_state}
                            </span>
                          ) : (
                            <span className="muted">Не привязан</span>
                          )}
                        </td>
                        <td>
                          <span className="state">
                            <i className="state__dot" />
                            {STATUS_TITLE[person.employment_status] ?? person.employment_status}
                          </span>
                        </td>
                        <td className="people__go">
                          <AppIcon name="arrow" size={16} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }
        </Rows>

        <div className="pager">
          <p className="pager__note">
            {list.state === 'ready'
              ? `Показано ${list.data.items.length} из ${plural(total, 'сотрудник')}`
              : ''}
          </p>
          <div className="pager__tools">
            <label className="pick pick--small">
              <span className="visually-hidden">Строк на странице</span>
              <select value={limit} onChange={(event) => patch({ limit: event.target.value })}>
                {SIZES.map((size) => (
                  <option key={size} value={size}>{size}</option>
                ))}
              </select>
            </label>
            <Pages
              page={page}
              pages={Math.max(1, Math.ceil(total / Number(limit)))}
              onGo={(next) => patch({ page: next === 1 ? null : String(next) }, true)}
            />
          </div>
        </div>
        <p className="sheet__hint">
          Нажмите на сотрудника, чтобы увидеть его справа.
        </p>
        </section>
       </div>

       <div className="grid__side">
        <Today block={today} />
        <Highlights block={highlights} />
        <Chosen
          person={chosen}
          onOpen={() => chosen && patch({ employee: chosen.id }, true)}
        />
       </div>
      </div>

      {opened && (
        <EmployeeCard id={opened} onClose={() => patch({ employee: null }, true)} />
      )}
    </AppShell>
  );
}

// --- правая колонка --------------------------------------------------------

/** Подписи состояний смены. Ровно те, что считает сервер. */
const PRESENCE_TITLE: [string, string][] = [
  ['IN_OFFICE', 'На месте'],
  ['LEFT', 'Ушли'],
  ['NOT_COME', 'Нет на месте'],
  ['VACATION', 'В отпуске'],
  ['SICK_LEAVE', 'На больничном'],
  ['OTHER_ABSENCE', 'Другое отсутствие'],
  ['DAY_OFF', 'Выходной'],
  ['NO_SCHEDULE', 'Без графика'],
];

function Today({ block }: { block: Block<api.Presence> }) {
  return (
    <section className="panel">
      <h2 className="panel__title panel__title--row">
        <span className="panel__mark" aria-hidden="true">
          <AppIcon name="users" size={20} />
        </span>
        Сегодня в команде
      </h2>

      {block.state !== 'ready' ? (
        <p className="empty">
          {block.state === 'error' || block.state === 'denied'
            ? 'Состав смены недоступен'
            : 'Считаем…'}
        </p>
      ) : (
        <TodayBody data={block.data} />
      )}
    </section>
  );
}

function TodayBody({ data }: { data: api.Presence }) {
  // Тело без `counts` — не повод уронить страницу: панель состава смены
  // стоит рядом со списком, и её сбой не должен уносить список с собой.
  const counts: Record<string, number> = data.counts ?? {};
  const here = counts['IN_OFFICE'] ?? 0;
  // Доля считается от тех, у кого сегодня рабочий день: выходные и людей
  // без графика в знаменателе превратили бы обычную субботу в провал
  // посещаемости.
  const expected =
    (data.total ?? 0) - (counts['DAY_OFF'] ?? 0) - (counts['NO_SCHEDULE'] ?? 0);
  const share = expected > 0 ? Math.round((here / expected) * 100) : 0;
  const rows = PRESENCE_TITLE.filter(([key]) => (counts[key] ?? 0) > 0);

  return (
    <div className="shift">
      <div className="shift__top">
        <Ring share={share} />
        <p className="shift__sum">
          <strong>{here} из {expected}</strong>
          <span>{expected > 0 ? 'на месте сейчас' : 'сегодня рабочих дней нет'}</span>
        </p>
      </div>
      <ul className="shift__rows">
        {rows.map(([key, title]) => (
          <li key={key}>
            <i className={`shift__dot shift__dot--${key.toLowerCase()}`} />
            <span className="shift__name">{title}</span>
            <span className="shift__count">{counts[key]}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Кольцо доли. Дугой, а не заливкой: так видна и сотая часть. */
function Ring({ share }: { share: number }) {
  const length = 2 * Math.PI * 26;
  return (
    <svg className="ring" viewBox="0 0 60 60" width={64} height={64}
         role="img" aria-label={`На месте ${share} процентов`}>
      <circle className="ring__track" cx="30" cy="30" r="26" />
      <circle
        className="ring__fill"
        cx="30"
        cy="30"
        r="26"
        strokeDasharray={`${(Math.min(share, 100) / 100) * length} ${length}`}
      />
      <text className="ring__text" x="30" y="30">{share}%</text>
    </svg>
  );
}

function Chosen({
  person,
  onOpen,
}: {
  person: api.EmployeeRow | null;
  onOpen: () => void;
}) {
  return (
    <section className="panel">
      <h2 className="panel__title panel__title--row">
        <span className="panel__mark" aria-hidden="true">
          <AppIcon name="user" size={20} />
        </span>
        Выбранный сотрудник
      </h2>

      {person === null ? (
        <p className="empty">Выберите сотрудника в списке.</p>
      ) : (
        <div className="chosen">
          <div className="chosen__head">
            <span className="avatar avatar--big">{initials(person.full_name)}</span>
            <span className="chosen__who">
              <span className="chosen__name">{person.full_name}</span>
              <span className="who__id">{person.employee_number ?? '—'}</span>
              <span className="chosen__role">
                {person.current_assignment?.position_name ?? 'Должность не назначена'}
              </span>
              {person.current_assignment?.department_name && (
                <span className="two__second">
                  {person.current_assignment.department_name}
                </span>
              )}
            </span>
            <Status value={person.employment_status} />
          </div>

          <div className="chosen__facts">
            <Fact icon="pin" value={person.current_assignment?.office_name} />
            <Fact
              icon="clock"
              value={
                person.current_schedule
                  ? person.current_schedule.name
                  : 'График не назначен'
              }
            />
            <Fact icon="send" value={telegramTitle(person.telegram_state)} />
          </div>

          {/* Одна кнопка, а не две: написать сотруднику из CRM пока нечем —
              отправки сообщений в API нет, и вторая кнопка была бы
              нарисованной. */}
          <button type="button" className="btn btn--dark chosen__open" onClick={onOpen}>
            Открыть профиль
          </button>
        </div>
      )}
    </section>
  );
}

function Fact({
  icon,
  value,
}: {
  icon: 'pin' | 'clock' | 'send';
  value: string | null | undefined;
}) {
  return (
    <div className="chosen__fact">
      <AppIcon name={icon} size={16} />
      <span>{value || '—'}</span>
    </div>
  );
}

// --- карточка в списке -----------------------------------------------------

/**
 * Лицо или инициалы.
 *
 * Снимок живёт в приватном хранилище и приходит отдельным адресом:
 * в строке списка лежит только признак того, что он есть. Если снимка
 * нет — инициалы, и это не заглушка, а обычное состояние.
 */
function Avatar({ id, name, has, size = 'card' }: {
  id: string;
  name: string;
  has: boolean;
  size?: 'card' | 'row' | 'big';
}) {
  const [broken, setBroken] = useState(false);
  const shape = `avatar avatar--${size}`;
  if (!has || broken) {
    return <span className={shape} aria-hidden="true">{initials(name)}</span>;
  }
  return (
    <img
      className={`${shape} avatar--photo`}
      src={api.employeePhotoUrl(id)}
      alt=""
      // Битый или удалённый снимок не должен оставлять пустое место:
      // карточка возвращается к инициалам.
      onError={() => setBroken(true)}
    />
  );
}

/** Номера страниц. Середина сворачивается многоточием, как в макете. */
function Pages({ page, pages, onGo }: {
  page: number;
  pages: number;
  onGo: (next: number) => void;
}) {
  if (pages <= 1) return null;
  const shown: (number | 'gap')[] = [];
  for (let at = 1; at <= pages; at += 1) {
    if (at <= 5 || at === pages || Math.abs(at - page) <= 1) shown.push(at);
    else if (shown[shown.length - 1] !== 'gap') shown.push('gap');
  }
  return (
    <nav className="pages" aria-label="Страницы списка">
      <button type="button" className="pick pick--icon" aria-label="Предыдущая"
              disabled={page <= 1} onClick={() => onGo(page - 1)}>
        <AppIcon name="chevron" size={16} className="rot-r" />
      </button>
      {shown.map((item, at) =>
        item === 'gap' ? (
          <span key={`gap-${at}`} className="pages__gap">…</span>
        ) : (
          <button
            key={item}
            type="button"
            className={item === page ? 'pages__one pages__one--on' : 'pages__one'}
            aria-current={item === page ? 'page' : undefined}
            onClick={() => onGo(item)}
          >
            {item}
          </button>
        ),
      )}
      <button type="button" className="pick pick--icon" aria-label="Следующая"
              disabled={page >= pages} onClick={() => onGo(page + 1)}>
        <AppIcon name="chevron" size={16} className="rot-l" />
      </button>
    </nav>
  );
}

/** «5 минут назад» — подпись о свежести данных. */
function ago(moment: Date): string {
  const minutes = Math.round((Date.now() - moment.getTime()) / 60000);
  if (minutes < 1) return 'Обновлено только что';
  if (minutes < 60) return `Обновлено ${minutes} мин назад`;
  return `Обновлено в ${moment.getHours()}:${String(moment.getMinutes()).padStart(2, '0')}`;
}

/**
 * Три сводки правой колонки: новички, именинники и люди без графика.
 *
 * Блок с нулём не прячется: «сегодня именинников нет» — это ответ, а
 * исчезнувший блок читается как поломка.
 */
function Highlights({ block }: { block: Block<api.EmployeeHighlights> }) {
  if (block.state !== 'ready') {
    return (
      <section className="panel">
        <p className="muted">{block.state === 'loading' ? 'Загружаем…' : 'Не удалось загрузить.'}</p>
      </section>
    );
  }
  const data = block.data;
  return (
    <>
      <section className="panel mark">
        <span className="mark__icon mark__icon--blue">
          <AppIcon name="user" size={20} />
        </span>
        <span className="mark__text">
          <span className="mark__title">Онбординг</span>
          <span className="mark__sub">
            {plural(data.recent_hires, 'новый')} за последние 30 дней
          </span>
        </span>
        <Link className="mark__go" to="/employees/new" aria-label="Добавить сотрудника">
          <AppIcon name="next" size={16} />
        </Link>
      </section>

      <section className="panel mark">
        <span className="mark__icon mark__icon--pink">
          <AppIcon name="calendar" size={20} />
        </span>
        <span className="mark__text">
          <span className="mark__title">Дни рождения</span>
          <span className="mark__sub">
            {data.birthdays_today ? `${plural(data.birthdays_today, 'сотрудник')} сегодня`
              : 'Сегодня никого'}
          </span>
        </span>
        <Faces rows={data.birthdays} total={data.birthdays_today} />
      </section>

      <section className="panel mark">
        <span className="mark__icon mark__icon--amber">
          <AppIcon name="clock" size={20} />
        </span>
        <span className="mark__text">
          <span className="mark__title">Нет графика</span>
          <span className="mark__sub">
            {data.without_schedule
              ? `${plural(data.without_schedule, 'сотрудник')} требуют настройки`
              : 'У всех назначен график'}
          </span>
        </span>
        <Faces rows={data.unscheduled} total={data.without_schedule} />
      </section>
    </>
  );
}

/** Несколько лиц и «+N» — сколько не поместилось. */
function Faces({ rows, total }: { rows: api.EmployeeBrief[]; total: number }) {
  if (rows.length === 0) return null;
  const rest = total - rows.length;
  return (
    <span className="faces">
      {rows.slice(0, 3).map((one) => (
        <Avatar key={one.id} id={one.id} name={one.full_name} has={one.photo} size="row" />
      ))}
      {rest > 0 && <span className="faces__rest">+{rest}</span>}
    </span>
  );
}

function PersonCard({
  person,
  chosen,
  onPick,
}: {
  person: api.EmployeeRow;
  chosen: boolean;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      className={chosen ? 'person person--on' : 'person'}
      aria-pressed={chosen}
      onClick={onPick}
    >
      <span className="avatar avatar--big">{initials(person.full_name)}</span>

      <span className="person__body">
        <span className="person__top">
          <span className="person__name">{person.full_name}</span>
          <Status value={person.employment_status} />
        </span>
        <span className="who__id">{person.employee_number ?? '—'}</span>

        <span className="person__role">
          {person.current_assignment?.position_name ?? 'Должность не назначена'}
        </span>
        {person.current_assignment?.department_name && (
          <span className="two__second">
            {person.current_assignment.department_name}
          </span>
        )}

        <span className="person__meta">
          <span className="person__bit">
            <AppIcon name="pin" size={16} />
            {person.current_assignment?.office_name ?? '—'}
          </span>
          <span className="person__bit">
            <AppIcon name="clock" size={16} />
            {person.current_schedule ? person.current_schedule.name : 'Нет графика'}
          </span>
          <span className="person__bit">
            <AppIcon name="send" size={16} />
            {telegramShort(person.telegram_state)}
          </span>
        </span>
      </span>
    </button>
  );
}

/** Состояние сотрудника словом и цветом. */
function Status({ value }: { value: string }) {
  const tone =
    value === 'ACTIVE' || value === 'PROBATION'
      ? 'ok'
      : value === 'TERMINATED' || value === 'ARCHIVED'
        ? 'off'
        : 'wait';
  return (
    <span className={`state state--${tone}`}>
      <i className="state__dot" />
      {STATUS_TITLE[value] ?? value}
    </span>
  );
}

const telegramShort = (state: string | null | undefined): string =>
  state === 'ACTIVE'
    ? 'Привязан'
    : state
      ? TELEGRAM_TITLE[state] ?? state
      : 'Не привязан';

const telegramTitle = (state: string | null | undefined): string =>
  state === 'ACTIVE' ? 'Telegram привязан' : `Telegram: ${telegramShort(state).toLowerCase()}`;

// --- мелочи ---------------------------------------------------------------

function count(counts: Record<string, number>, statuses: readonly string[]): number {
  if (statuses.length === 0) return counts['total'] ?? 0;
  return statuses.reduce((sum, key) => sum + (counts[key] ?? 0), 0);
}

const hours = (minutes: number) => `${Math.round(minutes / 60)} ч в неделю`;

/** Склонения, которые встречаются на этой странице. */
const FORMS: Record<string, [string, string, string]> = {
  'сотрудник': ['сотрудник', 'сотрудника', 'сотрудников'],
  'офис': ['офис', 'офиса', 'офисов'],
};

/** «252 сотрудника», «12 офисов». Число вместе со словом. */
function plural(n: number, word: keyof typeof FORMS): string {
  const forms = FORMS[word] as [string, string, string];
  const form =
    n % 10 === 1 && n % 100 !== 11
      ? forms[0]
      : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)
        ? forms[1]
        : forms[2];
  return `${n} ${form}`;
}

function Two({ first, second }: { first?: string | null | undefined; second?: string | null | undefined }) {
  return (
    <span className="two">
      <span className="two__first">{first ?? '—'}</span>
      {second && <span className="two__second">{second}</span>}
    </span>
  );
}

function Picker({ label, value, empty, options, onChange }: {
  label: string;
  value: string;
  empty: string;
  options: { id: string; name: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="pick">
      <span className="visually-hidden">{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">{empty}</option>
        {options.map((option) => (
          <option key={option.id} value={option.id}>{option.name}</option>
        ))}
      </select>
    </label>
  );
}

function Rows<T>({ block, children }: { block: Block<T>; children: (data: T) => React.ReactNode }) {
  if (block.state === 'loading') return <p className="empty">Загружаем список…</p>;
  if (block.state === 'denied') return <p className="empty">Нет доступа к списку сотрудников.</p>;
  if (block.state === 'error') {
    return (
      <p className="empty empty--bad">
        Не удалось загрузить список. Это ошибка запроса, а не «сотрудников нет».
      </p>
    );
  }
  return <>{children(block.data)}</>;
}
