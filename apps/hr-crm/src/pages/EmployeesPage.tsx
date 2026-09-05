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
import { useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell, initials } from '../components/AppShell';
import { Icon } from '../components/nav-icons';
import { EmployeeCard } from '../components/EmployeeCard';
import { useBlock, type Block } from '../features/dashboard/data';

/** Вкладки. Одна вкладка может покрывать несколько состояний модели. */
const TABS = [
  { key: 'all', title: 'Все', statuses: [] as string[] },
  { key: 'active', title: 'Активные', statuses: ['ACTIVE', 'PROBATION'] },
  { key: 'inactive', title: 'Неактивные', statuses: ['SUSPENDED'] },
  { key: 'left', title: 'Уволенные', statuses: ['TERMINATED', 'ARCHIVED'] },
] as const;

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

const SIZES = ['10', '25', '50'];

export function EmployeesPage() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.find((t) => t.key === params.get('tab')) ?? TABS[0];
  const search = params.get('search') ?? '';
  const region = params.get('region_id') ?? '';
  const office = params.get('office_id') ?? '';
  const department = params.get('department_id') ?? '';
  const limit = params.get('limit') ?? '10';
  const cursor = params.get('cursor') ?? '';
  const opened = params.get('employee') ?? '';

  const [draft, setDraft] = useState(search);
  useEffect(() => setDraft(search), [search]);

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
          // Любой новый фильтр начинает выборку заново: курсор от
          // прошлого набора указывает в чужую страницу.
          if (!keepCursor) next.delete('cursor');
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
  const key = `${tab.key}|${search}|${region}|${office}|${department}|${limit}|${cursor}`;

  const [list] = useBlock(
    (signal) =>
      api.employees(
        {
          ...filters,
          limit,
          ...(cursor ? { cursor } : {}),
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

  return (
    <AppShell breadcrumb="Сотрудники" section="employees">
      <header className="head head--tight">
        <div>
          <h1 className="head__title">Сотрудники</h1>
          <p className="head__sub">
            {directory.state === 'ready'
              ? `Управление командой во всех ${officeCount} офисах`
              : 'Управление командой'}
          </p>
        </div>
        <div className="head__actions">
          <button type="button" className="btn" disabled title="Выгрузка появится следующим этапом">
            <Icon name="report" size={16} />
            Экспорт
          </button>
          <button type="button" className="btn btn--dark" disabled
                  title="Форма добавления появится следующим этапом">
            <Icon name="users" size={16} />
            Добавить сотрудника
          </button>
        </div>
      </header>

      <section className="sheet">
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

        <div className="toolbar">
          <label className="find find--wide">
            <Icon name="search" size={16} />
            <input
              type="search"
              value={draft}
              placeholder="Поиск по имени или ID"
              aria-label="Поиск по имени или ID"
              onChange={(event) => setDraft(event.target.value)}
            />
          </label>
          <Picker
            label="Регион"
            value={region}
            empty="Все регионы"
            options={directory.state === 'ready' ? directory.data.regions : []}
            onChange={(value) => patch({ region_id: value || null, office_id: null })}
          />
          <Picker label="Офис" value={office} empty="Все офисы" options={offices}
                  onChange={(value) => patch({ office_id: value || null })} />
          <Picker
            label="Отдел"
            value={department}
            empty="Все отделы"
            options={directory.state === 'ready' ? directory.data.departments : []}
            onChange={(value) => patch({ department_id: value || null })}
          />
        </div>

        <Rows block={list}>
          {(data) =>
            data.items.length === 0 ? (
              <p className="empty">
                {search || region || office || department
                  ? 'По этим условиям никого не нашлось.'
                  : 'В доступной области нет сотрудников.'}
              </p>
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
                        onClick={() => patch({ employee: person.id }, true)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') patch({ employee: person.id }, true);
                        }}
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
                              <Icon name="send" size={14} />
                              Привязан
                            </span>
                          ) : person.telegram_state ? (
                            <span className="pill">
                              <Icon name="clock" size={14} />
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
                          <Icon name="arrow" size={16} />
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
              ? `Показано ${list.data.items.length} ${plural(list.data.items.length)}`
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
            {/* Курсор ведёт только вперёд, поэтому «назад» — это возврат
                к началу выборки, а не прыжок на предыдущую страницу. */}
            <button type="button" className="pick pick--icon" aria-label="В начало"
                    disabled={!cursor} onClick={() => patch({ cursor: null })}>
              <Icon name="chevron" size={14} className="rot-r" />
            </button>
            <button
              type="button"
              className="pick pick--icon"
              aria-label="Далее"
              disabled={list.state !== 'ready' || !list.data.has_more}
              onClick={() =>
                list.state === 'ready' &&
                patch({ cursor: list.data.next_cursor }, true)
              }
            >
              <Icon name="chevron" size={14} className="rot-l" />
            </button>
          </div>
        </div>
        <p className="sheet__hint">Нажмите на сотрудника, чтобы открыть карточку.</p>
      </section>

      {opened && (
        <EmployeeCard id={opened} onClose={() => patch({ employee: null }, true)} />
      )}
    </AppShell>
  );
}

// --- мелочи ---------------------------------------------------------------

function count(counts: Record<string, number>, statuses: readonly string[]): number {
  if (statuses.length === 0) return counts['total'] ?? 0;
  return statuses.reduce((sum, key) => sum + (counts[key] ?? 0), 0);
}

const hours = (minutes: number) => `${Math.round(minutes / 60)} ч в неделю`;

const plural = (n: number) =>
  n % 10 === 1 && n % 100 !== 11 ? 'сотрудник'
    : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'сотрудника'
    : 'сотрудников';

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
