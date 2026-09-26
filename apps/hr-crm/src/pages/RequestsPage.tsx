/**
 * Страница «Заявки»: отпуска, больничные, исправления отметок и отмены.
 *
 * Вёрстка повторяет эталон 1672×941. Размеры — в `styles/requests.css`,
 * классы с префиксом `rq-`.
 *
 * Список и подробности стоят рядом: кадровик разбирает очередь, и терять
 * её из виду на каждой заявке — значит каждый раз искать место, где он
 * остановился. Состояние — в адресе: вкладка, фильтры, страница и
 * открытая заявка переживают обновление и ссылку коллеге.
 *
 * Очередь приходит одним адресом `/requests`: склейка двух списков на
 * клиенте дала бы не очередь, а произвольную смесь её половин.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { messageFor } from '../api/errors';
import { AppShell } from '../components/AppShell';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import { Dropdown } from '../components/AppSelect';
import { AppDateRangePicker } from '../components/DateRangePicker';
import { formatTime, today, useBlock, type Block } from '../features/dashboard/data';
import {
  Face,
  calendarDaysWord,
  certificateState,
  dateTime,
  dayTime,
  kindOf,
  periodOf,
  personOf,
  shortName,
  statusOf,
  waitingWord,
} from '../features/requests/model';
import '../styles/requests.css';

const OPEN = 'SUBMITTED,IN_REVIEW';

/** Вкладки. Каждая — настоящий фильтр сервера, а не срез показанной страницы. */
const TABS = [
  { key: 'open', title: 'Требуют решения', params: { status: OPEN } as api.QueueQuery },
  { key: 'all', title: 'Все', params: {} as api.QueueQuery },
  { key: 'leave', title: 'Отпуска', params: { kind: 'absence', type: 'ANNUAL_LEAVE,UNPAID_LEAVE', request_kind: 'CREATE,EXTEND' } },
  { key: 'sick', title: 'Больничные', params: { kind: 'absence', type: 'SICK_LEAVE', request_kind: 'CREATE,EXTEND' } },
  { key: 'fixes', title: 'Исправления', params: { kind: 'correction' } },
  { key: 'cancel', title: 'Отмена', params: { kind: 'absence', request_kind: 'CANCEL' } },
] as const;

const STATUS_OPTIONS = [
  { id: OPEN, name: 'На рассмотрении' },
  { id: 'APPROVED', name: 'Одобрена' },
  { id: 'REJECTED', name: 'Отклонена' },
  { id: 'CANCELLED', name: 'Отменена' },
];

// Larger cursor pages mean fewer trips through the queue; the list itself
// remains independently scrollable inside its panel.
const PAGE = '20';

export function RequestsPage() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.find((one) => one.key === params.get('tab')) ?? TABS[0];
  const search = params.get('search') ?? '';
  const region = params.get('region_id') ?? '';
  const office = params.get('office_id') ?? '';
  const status = params.get('status') ?? '';
  const from = params.get('date_from') ?? '';
  const to = params.get('date_to') ?? '';
  const cursor = params.get('cursor') ?? '';
  const opened = params.get('request') ?? '';
  // Заявки одного сотрудника: приходит ссылкой с главной, своего поля нет.
  const employee = params.get('employee_id') ?? '';

  const [draft, setDraft] = useState(search);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [ordered, setOrdered] = useState<string | null>(null);
  useEffect(() => setDraft(search), [search]);

  const patch = useCallback(
    (changes: Record<string, string | null>, keepCursor = false) => {
      setParams(
        (was) => {
          const next = new URLSearchParams(was);
          for (const [key, value] of Object.entries(changes)) {
            if (value) next.set(key, value);
            else next.delete(key);
          }
          if (!keepCursor) next.delete('cursor');
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  useEffect(() => {
    if (draft === search) return;
    const timer = setTimeout(() => patch({ search: draft || null }), 350);
    return () => clearTimeout(timer);
  }, [draft, search, patch]);

  // Общие фильтры — без вкладки: по ним же считаются счётчики вкладок.
  const common: api.QueueQuery = useMemo(
    () => ({
      ...(search ? { search } : {}),
      ...(region ? { region_id: region } : {}),
      ...(office ? { office_id: office } : {}),
      ...(employee ? { employee_id: employee } : {}),
      ...(from ? { date_from: from } : {}),
      ...(to ? { date_to: to } : {}),
    }),
    [search, region, office, employee, from, to],
  );
  const base = `${search}|${region}|${office}|${employee}|${from}|${to}|${attempt}`;

  const [list] = useBlock(
    (signal) =>
      api.queue(
        { ...common, ...tab.params, ...(status ? { status } : {}), limit: PAGE, ...(cursor ? { cursor } : {}) },
        signal,
      ).then((body) => {
        setUpdated(new Date());
        return body;
      }),
    `${tab.key}|${status}|${cursor}|${base}`,
  );

  const [counts] = useBlock((signal) => api.queueCounts(common, signal), `counts|${base}`);

  const [directory] = useBlock(
    (signal) =>
      Promise.all([api.regions(signal), api.offices(signal)]).then(([r, o]) => ({
        regions: r.items,
        offices: o.items.filter((one) => one.status === 'ACTIVE'),
      })),
    'directory',
  );

  const offices = useMemo(() => {
    if (directory.state !== 'ready') return [];
    return region ? directory.data.offices.filter((one) => one.region_id === region) : directory.data.offices;
  }, [directory, region]);

  const items = list.state === 'ready' ? list.data.items : [];
  // Шторка открывается ТОЛЬКО по выбору. Прежде без выбора открывалась
  // первая заявка очереди — это было верно для постоянной колонки
  // рядом со списком, но окно поверх страницы, которое появляется само,
  // закрывает собой очередь, ради которой страницу открыли.
  const current = opened ? items.find((item) => item.id === opened) ?? null : null;
  const first = items[0];
  const employeeName = employee
    ? first?.absence?.employee.full_name ?? first?.correction?.employee?.full_name ?? null
    : null;
  const dirty = Boolean(search || region || office || employee || status || from || to);
  const waiting = counts.state === 'ready' ? counts.data['open'] ?? 0 : null;

  async function order() {
    setOrdered(null);
    try {
      await api.orderExport({
        kind: 'absences', fmt: 'xlsx',
        ...(from ? { date_from: from } : {}),
        ...(to ? { date_to: to } : {}),
        ...(office ? { office_id: office } : region ? { region_id: region } : {}),
      });
      setOrdered('Выгрузка поставлена в очередь');
    } catch (error) {
      setOrdered(messageFor(error));
    }
  }

  return (
    <AppShell breadcrumb="Заявки" section="requests">
      <div className="rq">
        <header className="rq-head">
          <div>
            <h1 className="rq-head__title">Заявки</h1>
            <p className="rq-head__sub">
              Отпуска, больничные и исправления отметок
              {waiting !== null && <> · {waiting} {waitingWord(waiting)} внимания</>}
              {updated ? <> · обновлено в {formatTime(updated)}</> : <> · загружаем…</>}
            </p>
          </div>
          <div className="rq-head__actions">
            <button type="button" className="rq-btn rq-btn--light" onClick={() => setAttempt((n) => n + 1)}>
              <AppIcon name="refresh" size={20} />
              Обновить
            </button>
            <button type="button" className="rq-btn rq-btn--light rq-btn--blue-text" onClick={() => void order()}>
              <AppIcon name="download" size={20} />
              Экспорт
            </button>
          </div>
        </header>

        {ordered && (
          <p className="rq-note" role="status">
            {ordered} — <Link to="/reports">файл появится в отчётах</Link>
          </p>
        )}

        <div className="rq-grid">
          <section className="rq-list" aria-label="Очередь заявок">
            {/* Вкладки подчёркиванием, а не заливкой: их шесть, и шесть
                залитых кнопок в ряд спорят за внимание с самой
                очередью, ради которой страницу открыли. */}
            <div className="rq-tabs" role="tablist" aria-label="Вид заявок">
              {TABS.map((one) => {
                const count = counts.state === 'ready' && one.key !== 'all'
                  ? counts.data[one.key] ?? 0
                  : null;
                return (
                  <button
                    key={one.key}
                    type="button"
                    role="tab"
                    aria-selected={one.key === tab.key}
                    onClick={() => patch({
                      tab: one.key === 'open' ? null : one.key,
                      request: null,
                      status: null,
                    })}
                  >
                    {one.title}
                    {count !== null && <i>{count}</i>}
                  </button>
                );
              })}
            </div>

            {/* Одна строка отбора. Чипов с офисом, статусом, датами и поиском
                здесь нет: они повторяли то, что и так видно в полях. Метка
                остаётся только у условий без своего поля — сотрудник,
                пришедший по ссылке, и регион, — и рядом «Сбросить». */}
            <div className="rq-filters">
              <label className="rq-search">
                <AppIcon name="search" size={16} />
                <input type="search" value={draft} placeholder="Поиск сотрудника"
                       aria-label="Поиск сотрудника"
                       onChange={(event) => setDraft(event.target.value)} />
              </label>
              <Select label="Офис" empty="Все офисы" value={office} options={offices}
                      onChange={(value) => patch({ office_id: value || null })} />
              <Select label="Статус" empty="Все статусы" value={status} options={STATUS_OPTIONS}
                      onChange={(value) => patch({ status: value || null })} />
              {/* Период фильтрует даты самого отсутствия, а не дату подачи. */}
              <AppDateRangePicker className="rq-dates" label="Даты отсутствия" now={today()} from={from} to={to}
                onFromChange={(value) => patch({ date_from: value || null })}
                onToChange={(value) => patch({ date_to: value || null })} />
              {region && !office && directory.state === 'ready' && (
                <Chip text={directory.data.regions.find((one) => one.id === region)?.name ?? 'Регион'}
                      onClear={() => patch({ region_id: null })} />
              )}
              {employee && (
                <Chip text={employeeName ? `Заявки: ${employeeName}` : 'Заявки одного сотрудника'}
                      onClear={() => patch({ employee_id: null })} />
              )}
              {dirty && (
                <button type="button" className="rq-reset" onClick={() =>
                  patch({ search: null, region_id: null, office_id: null, employee_id: null,
                          status: null, date_from: null, date_to: null })}>
                  Сбросить
                </button>
              )}
            </div>

            {/* Не таблица: у заявки нет восьми равноправных столбцов, у
                неё есть человек, вид и состояние. Шапка колонок над
                пустым телом — самое заметное, что было на этой
                странице, и самое бесполезное. */}
            <div className="rq-rows">
              <Rows block={list}>
                {(data) => data.items.length === 0 ? (
                  <div className="rq-none">
                    <p>По выбранным условиям заявок нет</p>
                    <small>Измените фильтры или сбросьте поиск.</small>
                  </div>
                ) : (
                  data.items.map((item) => (
                    <Row key={item.id} item={item} on={item.id === current?.id}
                         onPick={() => patch({ request: item.id }, true)} />
                  ))
                )}
              </Rows>
            </div>

            {/* Листалка появляется, только когда есть что листать:
                две неактивные кнопки под пустым списком — обещание
                страниц, которых нет. */}
            {list.state === 'ready' && (list.data.has_more || cursor) && (
              <footer className="rq-pager">
                <button type="button" className="rq-pager__btn" disabled={!cursor}
                        onClick={() => patch({ cursor: null })}>
                  <AppIcon name="back" size={16} />
                  В начало
                </button>
                <button type="button" className="rq-pager__btn"
                        disabled={!list.data.has_more}
                        onClick={() => patch({ cursor: list.data.next_cursor }, true)}>
                  Далее
                  <AppIcon name="next" size={16} />
                </button>
              </footer>
            )}
          </section>

          {/* Панель стоит рядом постоянно, а не выезжает поверх: это
              одна рабочая зона, а не список с окном над ним. Пока
              заявку не выбрали — короткая подсказка вместо пустоты. */}
          {current ? (
            <Preview key={current.id} item={current} />
          ) : (
            <aside className="rq-view rq-view--idle" aria-label="Выбранная заявка">
              <p>Выберите заявку слева — здесь появится, что по ней проверить.</p>
            </aside>
          )}
        </div>
      </div>
    </AppShell>
  );
}

// --- строка ----------------------------------------------------------------------

function Row({ item, on, onPick }: {
  item: api.QueueItem;
  on: boolean;
  onPick: () => void;
}) {
  const person = personOf(item);
  const state = statusOf(item);
  const kind = kindOf(item);
  const at = item.absence?.submitted_at ?? item.correction?.submitted_at ?? item.created_at;
  return (
    <div
      className={on ? 'rq-row rq-row--on' : 'rq-row'}
      role="button"
      tabIndex={0}
      aria-pressed={on}
      onClick={onPick}
      onKeyDown={(event) => { if (event.key === 'Enter') onPick(); }}
    >
      <Face id={person?.id ?? ''} name={person?.full_name ?? ''} className="rq-row__face" />
      <span className="rq-row__who">
        <b>{shortName(person?.full_name)}</b>
        <small>{item.place?.office_name ?? '—'}</small>
      </span>
      <span className="rq-row__kind">
        <AppIcon name={kind.icon} size={20} />
        <span>{kind.title}</span>
      </span>
      <span className="rq-row__when">
        <small>Подана</small>
        <span>{at ? dayTime(at) : '—'}</span>
      </span>
      <span className={`rq-status rq-status--${state.tone} rq-row__state`}>
        <i aria-hidden="true" />
        {state.title}
      </span>
      {/* Стрелка ведёт на страницу заявки, строка — только выбирает.
          Одно нажатие, два разных намерения: посмотреть рядом и уйти
          разбираться. */}
      <Link
        className="rq-row__go"
        to={`/requests/${item.id}`}
        aria-label="Открыть заявку"
        onClick={(event) => event.stopPropagation()}
      >
        <AppIcon name="next" size={20} />
      </Link>
    </div>
  );
}

// --- предпросмотр ------------------------------------------------------------
//
// Правая колонка ЧИТАЕТ заявку, а не решает по ней. Решение — на
// отдельной странице: у него есть цена, и принимать его мимоходом, не
// открыв бумаги и историю, нельзя. Здесь кадровик понимает, стоит ли
// вообще открывать эту заявку.

function Preview({ item }: { item: api.QueueItem }) {
  const person = personOf(item);
  const kind = kindOf(item);
  const state = statusOf(item);
  const period = periodOf(item);
  const absence = item.absence;
  const missing = absence?.missing_for_approval ?? [];
  const cert = certificateState(item);
  const vacation = item.kind === 'absence' && !absence?.requires_document;

  return (
    <aside className="rq-view" aria-label="Выбранная заявка">
      <header className="rq-view__head">
        <span className="rq-view__icon" aria-hidden="true">
          <AppIcon name={kind.icon} size={20} />
        </span>
        <div>
          <h2>{kind.title}</h2>
          <p>
            {shortName(person?.full_name)}
            {item.place?.office_name && <> · {item.place.office_name}</>}
          </p>
          <span className={`rq-status rq-status--${state.tone}`}>{state.title}</span>
        </div>
      </header>

      {item.kind === 'correction' ? (
        <div className="rq-view__body">
          <h3>Исправление отметки</h3>
          <dl className="rq-pairs">
            <Check title="Дата" value={period.long} tone="grey" icon="calendar" />
            <Check title="Запрошено" value={correctionAsk(item)} tone="grey" icon="clock" />
            <Check
              title="Комментарий"
              value={item.correction?.reason ?? '—'}
              tone="grey"
              icon="chat"
            />
          </dl>
        </div>
      ) : vacation ? (
        /* У отпуска нет ни справки, ни заявления: спрашивать их —
           значит просить бумагу, которой никто не ждёт. */
        <div className="rq-view__body">
          <h3>Что проверить</h3>
          {/* Теми же строками, что и у больничного: у отпуска пунктов
              меньше, но смотрят на них так же — сверху вниз, по одному
              на строку. Раньше здесь стоял плотный список пар, и одна
              очередь выглядела двумя разными экранами. */}
          <ul className="rq-view__checks">
            <Check title="Период" value={period.long} tone="grey" icon="calendar" />
            <Check
              title="Длительность"
              value={period.days !== null
                ? `${period.days} ${calendarDaysWord(period.days)}`
                : '—'}
              tone="grey"
              icon="clock"
            />
            <Check title="Решение" value={state.title} tone={state.tone} />
          </ul>
        </div>
      ) : (
        <div className="rq-view__body">
          <h3>Что проверить</h3>
          <ul className="rq-view__checks">
            <Check title="Справка" value={cert.title} tone={cert.tone} />
            <Check
              title="Подписанное заявление"
              value={absence?.application_received_at ? 'Подтверждено' : 'Не подтверждено'}
              tone={absence?.application_received_at ? 'green' : 'orange'}
            />
            <Check
              title="Фактические даты"
              value={missing.includes('period') ? 'Не указаны' : period.long}
              tone={missing.includes('period') ? 'orange' : 'green'}
            />
          </ul>
        </div>
      )}

      <p className="rq-hint">
        <AppIcon name="info" size={18} />
        {missing.length > 0
          ? 'Одобрение станет доступно после проверки всех пунктов.'
          : 'Все пункты проверки закрыты.'}
      </p>

      <footer className="rq-view__foot">
        <Link className="rq-btn rq-btn--light" to={`/requests/${item.id}`}>
          Открыть заявку
        </Link>
        {/* Кнопка здесь неактивна всегда и намеренно: одобряют на
            странице заявки, где видно, что именно одобряют. */}
        <button type="button" className="rq-btn rq-btn--approve" disabled>
          <AppIcon name="check" size={20} />
          Одобрить
        </button>
      </footer>
    </aside>
  );
}

function Check({ title, value, tone, icon = 'doc' }: {
  title: string;
  value: string;
  tone: string;
  icon?: AppIconName;
}) {
  return (
    <li className="rq-check">
      <AppIcon name={icon} size={20} />
      <b>{title}</b>
      <span className={`rq-check__value rq-check__value--${tone}`}>{value}</span>
    </li>
  );
}

function correctionAsk(item: api.QueueItem): string {
  const entry = item.correction?.requested_entry_at;
  const exit = item.correction?.requested_exit_at;
  const both = [entry && `вход ${dateTime(entry, true)}`, exit && `выход ${dateTime(exit, true)}`]
    .filter(Boolean)
    .join(', ');
  return both || '—';
}

// --- мелочи ------------------------------------------------------------------------------

function Chip({ text, onClear }: { text: string; onClear: () => void }) {
  return (
    <button type="button" className="rq-chip" onClick={onClear} aria-label={`Снять фильтр: ${text}`}>
      {text}
      <AppIcon name="close" size={16} />
    </button>
  );
}

function Select({ label, empty, value, options, onChange }: {
  label: string;
  empty: string;
  value: string;
  options: { id: string; name: string }[];
  onChange: (value: string) => void;
}) {
  return <Dropdown label={label} empty={empty} value={value} options={options} onChange={onChange} />;
}

function Rows<T>({ block, children }: { block: Block<T>; children: (data: T) => React.ReactNode }) {
  if (block.state === 'loading') return <p className="rq-empty">Загружаем очередь…</p>;
  if (block.state === 'denied') return <p className="rq-empty">Нет доступа к заявкам.</p>;
  if (block.state === 'error') {
    return <p className="rq-empty rq-empty--bad">Не удалось загрузить очередь. Это ошибка запроса, а не «заявок нет».</p>;
  }
  return <>{children(block.data)}</>;
}
