/**
 * Отчёты: настроить выгрузку, заказать её и забрать готовый файл.
 *
 * Считает и собирает всё сервер. Здесь нет ни одной строки, которая
 * складывала бы отчёт из того, что уже показано на других экранах:
 * выгрузка из таблицы браузера обошла бы и область видимости, и правила
 * подсчёта, и защиту от формул в тексте.
 *
 * Форма и история независимы. Период в форме — это параметр БУДУЩЕГО
 * файла; вкладки и фильтры истории — про уже заказанные. Иначе смена
 * дат в форме молча прятала бы вчерашние выгрузки.
 *
 * Заказанное задание неизменно: дальнейшая правка формы не меняет уже
 * стоящий в очереди отчёт. Оно и живёт на сервере — уход со страницы,
 * закрытие вкладки и повторный вход его не трогают.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { AppShell } from '../components/AppShell';
import { Icon, type IconName } from '../components/nav-icons';
import { formatTime, shift, today, useBlock } from '../features/dashboard/data';
import { dayTitle, momentTitle, sizeTitle, spanTitle } from '../features/reports/format';
import {
  KINDS, MAX_PERIOD_DAYS, STATUS_TITLE, TABS, kindTitle, tabCount,
  type Kind, type KindKey, type TabKey,
} from '../features/reports/kinds';
import { useHistory } from '../features/reports/queue';
import { useSession } from '../features/auth/session';

const ICONS: Record<Kind['icon'], IconName> = {
  calendar: 'calendar',
  clock: 'clock',
  late: 'late',
  doc: 'doc',
  users: 'users',
};

const STATUS_ICON: Record<string, IconName> = {
  QUEUED: 'clock',
  RUNNING: 'refresh',
  SUCCEEDED: 'check',
  FAILED: 'alert',
  CANCELLED: 'cross',
};

export function ReportsPage() {
  const session = useSession();
  const can = (code: string) =>
    session.status === 'authenticated' && session.user.permissions.includes(code);
  const me = session.status === 'authenticated' ? session.user.id : null;

  const mayExport = can('reports.export');
  const maySeeOthers = can('audit.read');

  // --- параметры будущего отчёта -------------------------------------------

  const [kind, setKind] = useState<KindKey>('attendance');
  const [to, setTo] = useState(today());
  const [from, setFrom] = useState(shift(today(), -29));
  const [region, setRegion] = useState('');
  const [office, setOffice] = useState('');
  const [fmt, setFmt] = useState<'xlsx' | 'csv'>('xlsx');

  const [ordering, setOrdering] = useState(false);
  const [fields, setFields] = useState<Record<string, string[]>>({});
  const [notice, setNotice] = useState<{ good: boolean; text: string } | null>(null);

  // --- история --------------------------------------------------------------

  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((item) => item.key === params.get('tab'))?.key ??
    'all') as TabKey;
  const mineOnly = !maySeeOthers || params.get('authors') !== 'all';
  const [attempt, setAttempt] = useState(0);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [rowError, setRowError] = useState<{ id: string; text: string } | null>(null);

  const onFresh = useCallback(() => setUpdated(new Date()), []);
  const { live, loadMore, more, replace, refresh } = useHistory({
    status: TABS.find((item) => item.key === tab)?.statuses ?? '',
    mineOnly,
    attempt,
    onFresh,
  });

  // --- справочники ----------------------------------------------------------

  const [directory] = useBlock(
    (signal) =>
      Promise.all([api.regions(signal), api.offices(signal)]).then(([r, o]) => ({
        regions: r.items.filter((item) => item.status === 'ACTIVE'),
        offices: o.items.filter((item) => item.status === 'ACTIVE'),
      })),
    'reports-directory',
  );

  const regions = directory.state === 'ready' ? directory.data.regions : [];
  const allOffices = directory.state === 'ready' ? directory.data.offices : [];
  // Офисы выбранного региона. Справочник уже пришёл в области видимости
  // человека: «все офисы» — это его офисы, а не офисы организации.
  const offices = useMemo(
    () => (region ? allOffices.filter((item) => item.region_id === region) : allOffices),
    [allOffices, region],
  );

  // Офис, выпавший из нового региона, сбрасывается. Иначе форма
  // показывала бы «Ташкент» при выбранном «Самарканд», а уехало бы
  // на сервер именно то, что показано.
  useEffect(() => {
    if (office && !offices.some((item) => item.id === office)) setOffice('');
  }, [offices, office]);

  const chosenKind = KINDS.find((item) => item.key === kind) as Kind;
  const allowed = mayExport && can(chosenKind.needs);

  const span = days(from, to);
  const badOrder = chosenKind.period && span <= 0;
  const tooLong = chosenKind.period && span > MAX_PERIOD_DAYS;
  const ready = allowed && !badOrder && !tooLong && !ordering;

  const scopeTitle = office
    ? (offices.find((item) => item.id === office)?.name ?? 'Выбранный офис')
    : region
      ? `${regions.find((item) => item.id === region)?.name ?? 'Регион'} · все офисы`
      : `Все доступные офисы · ${allOffices.length}`;

  async function order() {
    if (!ready) return;
    setOrdering(true);
    setFields({});
    setNotice(null);
    try {
      const job = await api.orderExport({
        kind,
        fmt,
        ...(chosenKind.period ? { date_from: from, date_to: to } : {}),
        ...(office ? { office_id: office } : {}),
        ...(region && !office ? { region_id: region } : {}),
      });
      // Сообщение и строка в истории — только после ответа сервера.
      // «Добавлено в очередь» до подтверждения означало бы обещание,
      // которого никто не давал.
      setNotice({ good: true, text: `Отчёт «${kindTitle(job.kind)}» добавлен в очередь` });
      setAttempt((n) => n + 1);
    } catch (error) {
      if (error instanceof ApiFailure) {
        setFields(error.fields);
        setNotice({ good: false, text: orderMessage(error) });
      } else {
        setNotice({ good: false, text: messageFor(error) });
      }
    } finally {
      // Введённое остаётся на месте: переписывать форму после отказа
      // значит заставить набрать всё заново.
      setOrdering(false);
    }
  }

  /** Отмена и повтор. Ответ сервера сразу заменяет строку. */
  async function act(job: api.ExportJob, what: 'cancel' | 'retry') {
    if (busy[job.id]) return;
    setBusy((was) => ({ ...was, [job.id]: true }));
    setRowError(null);
    try {
      replace(what === 'cancel' ? await api.cancelExport(job.id) : await api.retryExport(job.id));
      // Строка обновилась сразу, но счётчики вкладок считаются по
      // всему набору, а повтор вернул задание в очередь.
      refresh();
    } catch (error) {
      // Состояние могло измениться параллельно: показываем то, что
      // на сервере сейчас, а не то, что было на экране.
      try {
        replace(await api.exportJob(job.id));
      } catch {
        /* строку обновит следующий опрос */
      }
      setRowError({
        id: job.id,
        text:
          error instanceof ApiFailure && error.status === 409
            ? 'Состояние выгрузки изменилось — строка обновлена'
            : messageFor(error),
      });
    } finally {
      setBusy((was) => ({ ...was, [job.id]: false }));
    }
  }

  /** Повторить настройки готового задания в форме и заказать заново. */
  function reorder(job: api.ExportJob) {
    const known = KINDS.find((item) => item.key === job.kind);
    if (known) setKind(known.key);
    setFmt(job.fmt);
    const f = job.filters ?? {};
    if (f['date_from'] && f['date_to']) {
      setFrom(f['date_from']);
      setTo(f['date_to']);
    } else if (f['date']) {
      setFrom(f['date']);
      setTo(f['date']);
    }
    setRegion(f['region_id'] ?? '');
    setOffice(f['office_id'] ?? '');
    setNotice({
      good: true,
      text: 'Настройки перенесены в форму — нажмите «Сформировать отчёт»',
    });
    document.querySelector('.setup')?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
  }

  const counts = live.state === 'ready' ? live.data.counts : null;

  return (
    <AppShell breadcrumb="Отчёты" section="reports">
      <header className="head head--tight">
        <div>
          <h1 className="head__title">Отчёты</h1>
          <p className="head__sub">
            Сформируйте отчёт и скачайте данные в Excel или CSV
          </p>
        </div>
        <div className="head__actions">
          <button type="button" className="tool" aria-label="Обновить историю"
                  onClick={() => setAttempt((n) => n + 1)}>
            <Icon name="refresh" size={18} />
          </button>
          <p className="head__stamp">
            {/* Время двигается только после удачного ответа. */}
            {updated ? `Обновлено в ${formatTime(updated)}` : 'Загружаем…'}
          </p>
        </div>
      </header>

      {!mayExport && (
        <p className="empty empty--bad">
          Нет права на выгрузку отчётов. Файлы уходят из системы, и это
          отдельное разрешение — попросите его у администратора.
        </p>
      )}

      <p className="picker__title">Тип отчёта</p>
      <div className="kinds" role="radiogroup" aria-label="Тип отчёта">
        {KINDS.map((item) => {
          const permitted = mayExport && can(item.needs);
          const on = item.key === kind;
          return (
            <button
              key={item.key}
              type="button"
              role="radio"
              aria-checked={on}
              disabled={!permitted}
              title={permitted ? undefined : `Нужно право ${item.needs}`}
              className={on ? 'kind kind--on' : 'kind'}
              onClick={() => setKind(item.key)}
            >
              <span className="kind__icon" aria-hidden="true">
                <Icon name={ICONS[item.icon]} size={19} />
              </span>
              <span className="kind__text">
                <span className="kind__title">{item.title}</span>
                <span className="kind__note">
                  {permitted ? item.note : 'Нет доступа к данным'}
                </span>
              </span>
              {on && (
                <span className="kind__mark" aria-hidden="true">
                  <Icon name="check" size={18} />
                </span>
              )}
            </button>
          );
        })}
      </div>

      <section className="panel setup">
        <div className="setup__form">
          <h2 className="setup__title">Параметры отчёта</h2>

          <div className="setup__row">
            {chosenKind.period ? (
              <label className="field field--wide">
                <span className="field__label">Период</span>
                <span className="field__box">
                  <Icon name="calendar" size={16} />
                  <input type="date" value={from} aria-label="Начало периода"
                         onChange={(event) => setFrom(event.target.value)} />
                  <span aria-hidden="true">—</span>
                  <input type="date" value={to} aria-label="Конец периода"
                         onChange={(event) => setTo(event.target.value)} />
                </span>
                {badOrder && (
                  <span className="field__bad" role="alert">
                    Дата окончания раньше даты начала
                  </span>
                )}
                {!badOrder && tooLong && (
                  <span className="field__bad" role="alert">
                    В один отчёт помещается не больше {MAX_PERIOD_DAYS} дней;
                    выбрано {span}
                  </span>
                )}
                {fields['date_to']?.[0] && !badOrder && !tooLong && (
                  <span className="field__bad" role="alert">{fields['date_to'][0]}</span>
                )}
              </label>
            ) : (
              <label className="field field--wide">
                <span className="field__label">Состав</span>
                <span className="field__box field__box--static">
                  <Icon name="users" size={16} />
                  Текущий состав на {dayTitle(today())}
                </span>
                {/* Исторического среза «на дату» у списка сотрудников нет:
                    назначения хранятся, но отдельного отчёта по ним
                    не существует. Выбор даты обещал бы срез, которого
                    никто не соберёт. */}
                <span className="field__hint">
                  Среза на прошлую дату у этого отчёта нет
                </span>
              </label>
            )}

            <label className="field">
              <span className="field__label">Регион</span>
              <span className="field__box">
                <select value={region} aria-label="Регион"
                        onChange={(event) => setRegion(event.target.value)}>
                  <option value="">Все регионы</option>
                  {regions.map((item) => (
                    <option key={item.id} value={item.id}>{item.name}</option>
                  ))}
                </select>
              </span>
            </label>

            <label className="field">
              <span className="field__label">Офис</span>
              <span className="field__box">
                <select value={office} aria-label="Офис"
                        onChange={(event) => setOffice(event.target.value)}>
                  <option value="">Все офисы · {offices.length}</option>
                  {offices.map((item) => (
                    <option key={item.id} value={item.id}>{item.name}</option>
                  ))}
                </select>
              </span>
            </label>
          </div>

          <div className="setup__row setup__row--bottom">
            <div className="field">
              <span className="field__label">Формат файла</span>
              <div className="switch switch--format" role="group" aria-label="Формат файла">
                <button type="button" className={fmt === 'xlsx' ? 'switch__on' : ''}
                        onClick={() => setFmt('xlsx')}>
                  Excel (.xlsx)
                </button>
                <button type="button" className={fmt === 'csv' ? 'switch__on' : ''}
                        onClick={() => setFmt('csv')}>
                  CSV
                </button>
              </div>
            </div>
            <p className="setup__zone">
              <Icon name="calendar" size={15} />
              {zoneNote(chosenKind, Boolean(office))}
            </p>
          </div>

          <p className="setup__hint">
            <Icon name="alert" size={15} />
            Большие отчёты готовятся в фоне — можно продолжить работу.
          </p>
        </div>

        <aside className="setup__side">
          <span className="setup__badge" aria-hidden="true">
            <Icon name="download" size={22} />
          </span>
          <p className="setup__ready">Готово к формированию</p>
          <p className="setup__line">
            {chosenKind.title} ·{' '}
            {chosenKind.period ? spanTitle(from, to) : 'текущий состав'}
          </p>
          <p className="setup__line setup__line--dim">{scopeTitle}</p>
          <p className="setup__line setup__line--dim">
            {fmt === 'xlsx' ? 'Excel (.xlsx)' : 'CSV'}
          </p>

          <button type="button" className="btn btn--dark btn--wide"
                  disabled={!ready} onClick={() => void order()}>
            {ordering ? 'Отправляем…' : 'Сформировать отчёт'}
            {!ordering && <Icon name="arrow" size={17} />}
          </button>

          {notice ? (
            <p className={notice.good ? 'setup__note' : 'setup__note setup__note--bad'}
               role="status">
              {notice.text}
            </p>
          ) : (
            <p className="setup__note">Файл появится в истории ниже</p>
          )}
        </aside>
      </section>

      <section className="sheet">
        <div className="head head--inside">
          <h2 className="sheet__title">История выгрузок</h2>
          <div className="head__actions">
            {maySeeOthers && (
              <label className="pick pick--small">
                <span className="visually-hidden">Чьи выгрузки показывать</span>
                <select
                  value={mineOnly ? 'mine' : 'all'}
                  aria-label="Чьи выгрузки показывать"
                  onChange={(event) =>
                    patchParam(setParams, 'authors',
                               event.target.value === 'all' ? 'all' : null)}
                >
                  {/* Ровно два значения, потому что их ровно два и на
                      сервере. Списка авторов у API нет, а фильтровать
                      загруженную страницу значило бы прятать строки,
                      которые лежат на следующей. */}
                  <option value="mine">Только мои</option>
                  <option value="all">Все авторы</option>
                </select>
              </label>
            )}
            <button type="button" className="tool" aria-label="Обновить историю выгрузок"
                    onClick={() => setAttempt((n) => n + 1)}>
              <Icon name="refresh" size={18} />
            </button>
          </div>
        </div>

        <div className="tabs" role="tablist" aria-label="Состояние выгрузок">
          {TABS.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={item.key === tab}
              className={item.key === tab ? 'tab tab--on' : 'tab'}
              onClick={() => patchParam(setParams, 'tab', item.key === 'all' ? null : item.key)}
            >
              {item.title}
              {/* Числа только настоящие: пока счётчики не пришли,
                  на их месте ничего нет, а не ноль. */}
              {counts && <span className="tab__count">{tabCount(item.key, counts)}</span>}
            </button>
          ))}
        </div>

        {live.state === 'loading' && <p className="empty">Загружаем историю…</p>}
        {live.state === 'denied' && <p className="empty">Сессия истекла. Войдите заново.</p>}
        {live.state === 'error' && (
          <p className="empty empty--bad">
            Не удалось загрузить историю выгрузок. Это ошибка запроса, а не
            отсутствие файлов.{' '}
            <button type="button" className="link" onClick={() => setAttempt((n) => n + 1)}>
              Повторить
            </button>
          </p>
        )}

        {live.state === 'ready' && (
          <>
            {live.stale && (
              <p className="sheet__stale" role="status">
                Данные не обновились — показано последнее полученное состояние
              </p>
            )}

            {live.data.items.length === 0 ? (
              <p className="empty">
                {tab === 'all'
                  ? 'Выгрузок пока нет. Выберите тип отчёта выше и сформируйте первый.'
                  : 'В этом состоянии выгрузок нет. Проверьте другие вкладки.'}
              </p>
            ) : (
              <div className="scroller">
                <table className="people people--flat">
                  <thead>
                    <tr>
                      <th>Отчёт</th>
                      <th>Период</th>
                      <th>Область</th>
                      <th>Формат</th>
                      <th>Статус</th>
                      <th>Создан</th>
                      <th aria-label="Действия" />
                    </tr>
                  </thead>
                  <tbody>
                    {live.data.items.map((job) => (
                      <Row
                        key={job.id}
                        job={job}
                        mine={job.requested_by_user_id === me}
                        offices={allOffices}
                        regions={regions}
                        busy={Boolean(busy[job.id])}
                        error={rowError?.id === job.id ? rowError.text : null}
                        onCancel={() => void act(job, 'cancel')}
                        onRetry={() => void act(job, 'retry')}
                        onReorder={() => reorder(job)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="sheet__foot">
              <span className="muted">{shownTitle(live.data)}</span>
              <span className="sheet__right">
                {more.kind && <span className="field__bad">Не удалось дочитать список</span>}
                {live.data.hasMore && (
                  <button type="button" className="btn btn--small"
                          disabled={more.busy} onClick={() => void loadMore()}>
                    {more.busy ? 'Читаем…' : 'Показать ещё'}
                  </button>
                )}
                <span className="muted sheet__lock">
                  <Icon name="lock" size={15} />
                  Файл доступен только заказавшему и хранится ограниченное время
                </span>
              </span>
            </div>
          </>
        )}
      </section>
    </AppShell>
  );
}

// --- строка истории ---------------------------------------------------------

type RowProps = {
  job: api.ExportJob;
  mine: boolean;
  offices: api.Office[];
  regions: api.Region[];
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onRetry: () => void;
  onReorder: () => void;
};

function Row({ job, mine, offices, regions, busy, error, onCancel, onRetry, onReorder }: RowProps) {
  const filters = job.filters ?? {};
  const gone = fileGone(job);

  return (
    <tr>
      <td className="grid-table__name">
        <span className="who">
          <Icon name="sheet" size={16} />
          <b>{kindTitle(job.kind)}</b>
        </span>
      </td>
      <td>{periodTitle(job)}</td>
      <td>{scopeOf(filters, offices, regions)}</td>
      <td className="mono">{job.fmt.toUpperCase()}</td>
      <td>
        <span className={`state state--${job.status.toLowerCase()}`}>
          <Icon name={STATUS_ICON[job.status] ?? 'clock'} size={16} />
          <span>
            {STATUS_TITLE[job.status] ?? job.status}
            {job.status === 'SUCCEEDED' && !gone && sizeTitle(job.size_bytes) && (
              <span className="who__id">{sizeTitle(job.size_bytes)}</span>
            )}
            {job.status === 'SUCCEEDED' && gone && (
              <span className="who__id">Файл удалён по сроку хранения</span>
            )}
            {job.status === 'FAILED' && job.error_message && (
              <span className="who__id">{job.error_message}</span>
            )}
          </span>
        </span>
      </td>
      <td>
        {momentTitle(job.created_at)}
        <span className="who__id">{mine ? 'Вы' : (job.requested_by ?? 'Другой сотрудник')}</span>
      </td>
      <td className="num">
        {job.status === 'SUCCEEDED' && !gone && (
          // Обычная ссылка: файл забирает браузер, а не JavaScript.
          // Имя приходит от сервера вместе с заголовком вложения —
          // подменять его атрибутом download значило бы потерять то
          // безопасное имя, которое он и придумал.
          <a className="btn btn--small" href={api.downloadUrl(job.id)}
             target="_blank" rel="noopener noreferrer">
            <Icon name="download" size={16} />
            Скачать
          </a>
        )}
        {job.status === 'SUCCEEDED' && gone && (
          <button type="button" className="btn btn--small" onClick={onReorder}>
            Заказать заново
          </button>
        )}
        {job.status === 'QUEUED' && (
          <button type="button" className="link" disabled={busy} onClick={onCancel}>
            {busy ? 'Отменяем…' : 'Отменить'}
          </button>
        )}
        {/* У RUNNING кнопки отмены нет: сервер отменяет только то, что
            ещё не начато, и «отменить» рядом с собираемым прямо сейчас
            файлом означало бы действие, которого не произойдёт. */}
        {job.status === 'RUNNING' && <span className="muted">Идёт сборка</span>}
        {(job.status === 'FAILED' || job.status === 'CANCELLED') && (
          <button type="button" className="btn btn--small" disabled={busy} onClick={onRetry}>
            <Icon name="refresh" size={16} />
            {busy ? 'Ставим…' : 'Повторить'}
          </button>
        )}
        {error && <span className="field__bad" role="alert">{error}</span>}
      </td>
    </tr>
  );
}

// --- вспомогательное --------------------------------------------------------

/**
 * Файла у готового задания уже нет.
 *
 * Два признака, и оба настоящие: уборка просроченных обнуляет размер
 * вместе с ключом хранения, а срок мог истечь и до её прохода. Живая
 * кнопка «Скачать» на такой строке привела бы к отказу вместо файла.
 */
export function fileGone(job: api.ExportJob, now: Date = new Date()): boolean {
  if (job.status !== 'SUCCEEDED') return false;
  if (job.size_bytes === null) return true;
  return job.expires_at !== null && new Date(job.expires_at) <= now;
}

function periodTitle(job: api.ExportJob): string {
  const f = job.filters ?? {};
  if (f['date_from'] || f['date_to']) return spanTitle(f['date_from'], f['date_to']);
  if (f['date']) return dayTitle(f['date']);
  if (job.kind === 'employees') return 'Текущий состав';
  return '—';
}

function scopeOf(
  filters: Record<string, string>,
  offices: api.Office[],
  regions: api.Region[],
): string {
  if (filters['office_id']) {
    return offices.find((item) => item.id === filters['office_id'])?.name ?? 'Один офис';
  }
  if (filters['region_id']) {
    return regions.find((item) => item.id === filters['region_id'])?.name ?? 'Один регион';
  }
  // Не «вся организация»: файл собран по области видимости заказчика,
  // а она могла быть уже.
  return 'Все доступные офисы';
}

/**
 * Что написано под таблицей.
 *
 * «Показаны все N» — только когда сервер сказал, что дальше ничего нет,
 * И счётчик пришёл. В остальных случаях честное «показано N».
 */
export function shownTitle(data: {
  items: unknown[];
  hasMore: boolean;
  counts: { total: number } | null;
}): string {
  const shown = data.items.length;
  if (!data.hasMore && data.counts && data.counts.total === shown) {
    return `Показаны все ${shown} ${plural(shown)}`;
  }
  return `Показано ${shown} ${plural(shown)}`;
}

function plural(count: number): string {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return 'выгрузок';
  const last = count % 10;
  if (last === 1) return 'выгрузка';
  if (last >= 2 && last <= 4) return 'выгрузки';
  return 'выгрузок';
}

/** Дней в периоде включительно. Ноль и меньше — даты перепутаны. */
export function days(from: string, to: string): number {
  const a = Date.parse(`${from}T00:00:00Z`);
  const b = Date.parse(`${to}T00:00:00Z`);
  if (Number.isNaN(a) || Number.isNaN(b)) return 0;
  return Math.round((b - a) / 86_400_000) + 1;
}

/**
 * Пояснение про часовые пояса — по фактическим правилам backend.
 *
 * Присутствие считается в поясе ОФИСА, и при нескольких офисах берётся
 * пояс первого из выборки — он же назван в шапке файла. Обещать «по
 * часовому поясу офиса» для отчёта по всей области было бы неправдой.
 *
 * Отсутствия сравниваются по датам заявок, а их даты Django сравнивает
 * в UTC: пояс офиса тут ни при чём.
 */
export function zoneNote(kind: Kind, oneOffice: boolean): string {
  if (kind.key === 'absences') return 'Даты отсутствий сравниваются по UTC';
  if (!kind.period) return 'Состав берётся на момент формирования файла';
  return oneOffice
    ? 'Даты — по часовому поясу офиса'
    : 'Даты — по поясу первого офиса выборки; он указан в файле';
}

function orderMessage(error: ApiFailure): string {
  if (error.status === 409) {
    return 'Слишком много незавершённых выгрузок. Дождитесь их окончания или отмените лишние';
  }
  if (error.kind === 'validation') return 'Проверьте параметры отчёта';
  if (error.kind === 'session') return 'Нет доступа к этому отчёту';
  return messageFor(error);
}

function patchParam(
  setParams: ReturnType<typeof useSearchParams>[1],
  key: string,
  value: string | null,
) {
  setParams((was) => {
    const next = new URLSearchParams(was);
    if (value) next.set(key, value);
    else next.delete(key);
    return next;
  });
}
