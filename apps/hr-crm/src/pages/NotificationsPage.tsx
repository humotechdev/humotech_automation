/**
 * Уведомления: события кадровика и очередь отправки сотрудникам.
 *
 * Две вкладки, потому что это два разных вопроса.
 *
 *   ЛЕНТА СОБЫТИЙ — что произошло в кадровом контуре и ждёт человека:
 *   заявки, справки, исправления отметок, незакрытые выходы, обращения.
 *   То же самое показывает колокольчик в шапке, здесь — целиком;
 *
 *   ОЧЕРЕДЬ ОТПРАВКИ — что уходило сотрудникам в Telegram и что с этим
 *   стало. Своя область данных, свои права (`notifications.read`) и своя
 *   ответственность, поэтому она осталась ровно такой, какой была.
 *
 * Про очередь — два вопроса, и оба про правду, а не про оформление:
 *
 *   УШЛО ЛИ. Статус берётся из очереди, а не выводится из нажатия.
 *   Успешный ответ отправщика означает «передано в Telegram», и
 *   прочтением он не становится ни при каких условиях;
 *
 *   ПОЧЕМУ НЕ УШЛО. Причина и история попыток приходят с сервера
 *   структурой. Ни одной попытки, ни одного времени и ни одной причины
 *   здесь не придумывается: у строк старше самой истории её нет, и об
 *   этом сказано словами.
 *
 * Отправляет бот. Браузер не пишет в Telegram ничего и никогда — он
 * только просит сервер вернуть строку в очередь.
 */

import { useCallback, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { AppShell } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { AppSegmentedControl, AppSelectField } from '../components/AppSelect';
import { AppDateRangePicker } from '../components/DateRangePicker';
import { FeedBoard } from '../features/notifications/FeedBoard';
import { PageCard, type PageView } from '../features/notifications/PageCard';
import { useFeed } from '../features/live/feed';
import {
  CHANNEL, STATUS, TABS, attemptTitle, cancelBlockedBecause, deliveryNote,
  eventTitle, moving, reasonTitle, relatedLink, retryBlockedBecause, tabCount,
  type Tab,
} from '../features/notifications/model';
import { today, useBlock, type Block } from '../features/dashboard/data';
import { clock, moment } from '../features/time/zone';
import '../styles/notifications.css';

const PAGE = '20';

export function NotificationsPage() {
  /*
   * Прав в интерфейсе нет: администратор один, и ему открыто всё.
   * Проверку исполняет сервер — он и ответит отказом, если когда-нибудь
   * появится учётная запись с урезанным доступом.
   */
  const can = (_code: string) => true;
  const mayRead = can('notifications.read');
  const mayManage = can('notifications.manage');

  const [params, setParams] = useSearchParams();
  const search = params.get('search') ?? '';
  const region = params.get('region_id') ?? '';
  const office = params.get('office_id') ?? '';
  const from = params.get('date_from') ?? '';
  const to = params.get('date_to') ?? '';
  const picked = params.get('id') ?? '';
  const tab = (TABS.find((item) => item.key === params.get('tab'))?.key ??
    'all') as Tab;
  // Вкладка страницы. Отдельный параметр от `tab`: тот уже занят
  // состояниями очереди, и ссылки вида `?tab=failed` обязаны продолжать
  // работать — они разосланы по системе и стоят в старых уведомлениях.
  const view = params.get('view') === 'delivery' ? 'delivery' : 'feed';
  const onQueue = view === 'delivery';

  const [updated, setUpdated] = useState<Date | null>(null);
  const [acting, setActing] = useState(false);
  // Замок держится в ссылке, а не в состоянии: состояние обновляется
  // асинхронно, и три нажатия подряд успевают увидеть его прежним —
  // на сервер уходит три запроса вместо одного. Отклонит их очередь
  // или нет, зависит от гонки, а не от нас.
  const busy = useRef(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const patch = useCallback(
    (changes: Record<string, string | null>) => {
      setParams((was) => {
        const next = new URLSearchParams(was);
        for (const [key, value] of Object.entries(changes)) {
          if (value) next.set(key, value);
          else next.delete(key);
        }
        return next;
      });
    },
    [setParams],
  );

  // --- справочники области ---------------------------------------------------

  const [directory] = useBlock(
    (signal) =>
      Promise.all([api.regions(signal), api.offices(signal)]).then(([r, o]) => ({
        regions: r.items.filter((item) => item.status === 'ACTIVE'),
        offices: o.items.filter((item) => item.status === 'ACTIVE'),
      })),
    'notifications-directory',
    mayRead && onQueue,
  );
  const scope = directory.state === 'ready'
    ? directory.data
    : { regions: [] as api.Region[], offices: [] as api.Office[] };

  // --- список и сводка -------------------------------------------------------

  const filters = useMemo(
    () => ({
      ...(search ? { search } : {}),
      ...(region && !office ? { region_id: region } : {}),
      ...(office ? { office_id: office } : {}),
      ...(from ? { date_from: from } : {}),
      ...(to ? { date_to: to } : {}),
    }),
    [search, region, office, from, to],
  );
  const statuses = TABS.find((item) => item.key === tab)?.statuses ?? '';
  const key = `${tab}|${search}|${region}|${office}|${from}|${to}|${attempt}`;

  const load = useCallback(
    async (signal: AbortSignal) => {
      const [page, counts] = await Promise.all([
        api.notifications(
          { ...filters, ...(statuses ? { status: statuses } : {}), limit: PAGE },
          signal,
        ),
        // Счётчики — по всему набору и БЕЗ фильтра вкладки: число рядом
        // с вкладкой не должно зависеть от открытой вкладки.
        api.notificationCounts(filters, signal),
      ]);
      return {
        items: page.items,
        counts,
        next: page.next_cursor,
        hasMore: page.has_more,
      };
    },
    [filters, statuses],
  );

  const more = useCallback(
    (cursor: string) =>
      api.notifications({
        ...filters,
        ...(statuses ? { status: statuses } : {}),
        limit: PAGE,
        cursor,
      }),
    [filters, statuses],
  );

  const onFresh = useCallback(() => setUpdated(new Date()), []);

  const { live, loadMore, more: tail, replace, refresh } = useFeed<
    api.Notification,
    api.NotificationCounts
  >({ key, load, more, waiting: moving, onFresh, enabled: mayRead && onQueue });

  const counts = live.state === 'ready' ? live.data.counts : null;
  const zone = counts?.timezone ?? '';

  // Ключ включает выбранную строку: при быстром переключении устаревший
  // ответ отбрасывается, и текст одного уведомления не окажется под
  // заголовком другого.
  const [card] = useBlock(
    (signal) => api.notification(picked, signal),
    `card|${picked}|${attempt}`,
    mayRead && onQueue && Boolean(picked),
  );
  const [attempts] = useBlock(
    (signal) => api.notificationAttempts(picked, signal),
    `attempts|${picked}|${attempt}`,
    mayRead && onQueue && Boolean(picked),
  );

  // --- действия --------------------------------------------------------------

  async function act(run: () => Promise<api.Notification>) {
    // Пока запрос идёт, второе нажатие не проходит: два заказа повтора —
    // это два сообщения человеку.
    if (busy.current) return;
    busy.current = true;
    setActing(true);
    setActionError(null);
    try {
      const fresh = await run();
      // На место карточки кладётся ТО, что вернул сервер. Рисовать
      // «Отправлено» сразу после нажатия нельзя: сообщение ещё
      // не отправлено, оно только вернулось в очередь.
      replace(fresh);
      setAttempt((n) => n + 1);
      refresh();
    } catch (error) {
      setActionError(
        error instanceof ApiFailure && error.kind === 'conflict'
          ? 'Состояние изменилось: очередь или другой кадровик успели раньше. Карточка обновлена.'
          : messageFor(error),
      );
      // При конфликте карточка перечитывается — человек должен увидеть
      // настоящее состояние, а не то, из которого нажимал.
      if (error instanceof ApiFailure && error.kind === 'conflict') {
        setAttempt((n) => n + 1);
        refresh();
      }
    } finally {
      busy.current = false;
      setActing(false);
    }
  }

  const showView = (next: PageView) =>
    patch({ view: next === 'feed' ? null : 'delivery', id: null });

  if (!onQueue) {
    return (
      <AppShell breadcrumb="Уведомления" section="notifications">
        <div className="ntf-page">
          <FeedBoard opened={picked} onOpenDetail={(id) => patch({ id: id || null })}
                     onView={showView} />
        </div>
      </AppShell>
    );
  }

  if (!mayRead) {
    return (
      <AppShell breadcrumb="Уведомления" section="notifications">
        <div className="ntf-page">
          <PageCard view="delivery" count={null} onView={showView}>
            <p className="ntf-empty ntf-empty--bad">
              Нет права на просмотр очереди отправки. В ней видно, что писали
              конкретным людям, поэтому это отдельное разрешение — попросите его
              у администратора.
            </p>
          </PageCard>
        </div>
      </AppShell>
    );
  }

  const denied = live.state === 'denied';

  return (
    <AppShell breadcrumb="Уведомления" section="notifications">
      <div className="ntf-page">
        <PageCard
          view="delivery"
          count={denied ? null : counts?.total ?? null}
          onView={showView}
          action={(
            <div className="ntf-stamp">
              <button type="button" className="ntf-btn ntf-btn--outline" onClick={refresh}
                      aria-label="Обновить список">
                <AppIcon name="refresh" size={16} />
                Обновить
              </button>
              <span className="ntf-stamp__text">
                {/* Время двигается только после УДАЧНОГО ответа. */}
                {updated
                  ? `Обновлено в ${clock(updated.toISOString(), zone)}${zone ? ` · ${zone}` : ''}`
                  : 'Загружаем…'}
              </span>
            </div>
          )}
        >
          <div className="ntf-filters ntf-filters--queue">
            <label className="ntf-search">
              <AppIcon name="search" size={16} />
              <input type="search" value={search}
                     placeholder="Поиск сообщения или сотрудника"
                     aria-label="Поиск сообщения или сотрудника"
                     onChange={(event) => patch({ search: event.target.value || null })} />
            </label>
            <AppSelectField label="Регион" className="ntf-filters__select" value={region}
                      onChange={(value) =>
                        // Смена региона сбрасывает офис: показанное
                        // обязано совпадать с отправляемым.
                        patch({ region_id: value || null, office_id: null })}>
                <option value="">Все регионы</option>
                {scope.regions.map((item) => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
            </AppSelectField>
            <AppSelectField label="Офис" className="ntf-filters__select" value={office}
                      onChange={(value) => patch({ office_id: value || null })}>
                <option value="">Все офисы</option>
                {(region
                  ? scope.offices.filter((item) => item.region_id === region)
                  : scope.offices
                ).map((item) => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
            </AppSelectField>
            <AppDateRangePicker className="ntf-filters__dates" label="Период уведомлений" now={today()} from={from} to={to}
              onFromChange={(value) => patch({ date_from: value || null })}
              onToChange={(value) => patch({ date_to: value || null })} />
          </div>

          <div className="ntf-queueBar">
            <AppSegmentedControl className="ntf-statusTabs" role="tablist" label="Состояние отправки" value={tab}
              options={TABS.map((item) => ({ value: item.key, label: item.title, count: tabCount(item.key, counts) }))}
              onChange={(key) => patch({ tab: key === 'all' ? null : key, id: null })} />
            <ul className="ntf-summary" aria-label="Сводка по состояниям">
              <Tile title="Всего" value={denied ? undefined : counts?.total} />
              <Tile title="Отправлено" value={denied ? undefined : counts?.sent} />
              <Tile title="В очереди" value={denied ? undefined : counts?.queued} />
              <Tile title="С ошибкой" value={denied ? undefined : counts?.failed} />
              <Tile title="Снято" value={denied ? undefined : counts?.cancelled} />
            </ul>
          </div>

          <div className={picked ? 'ntf-split ntf-split--open' : 'ntf-split'}>
            <div className="ntf-split__list">
              {live.state === 'loading' && <p className="ntf-empty" role="status">Загружаем историю…</p>}
              {denied && (
                <p className="ntf-empty ntf-empty--bad" role="alert">Сессия истекла. Войдите заново.</p>
              )}
              {live.state === 'error' && (
                <p className="ntf-empty ntf-empty--bad" role="alert">
                  Не удалось загрузить список. Это ошибка запроса, а не пустая история.{' '}
                  <button type="button" className="ntf-link" onClick={refresh}>
                    Повторить
                  </button>
                </p>
              )}

              {live.state === 'ready' && (
                <>
                  {live.stale && (
                    <p className="ntf-note" role="status">
                      Данные не обновились — показано последнее удачное чтение.
                    </p>
                  )}
                  {live.data.items.length === 0 ? (
                    <p className="ntf-empty">{emptyText(search, tab, Boolean(from || to))}</p>
                  ) : (
                    <table className="ntf-qtable">
                      <thead>
                        <tr>
                          <th>Сообщение / получатель</th>
                          <th>Офис</th>
                          <th>Статус</th>
                          <th>Создано</th>
                          <th><span className="ntf-sr">Действие</span></th>
                        </tr>
                      </thead>
                      <tbody>
                        {live.data.items.map((row) => (
                          <tr key={row.id} tabIndex={0}
                              className={row.id === picked ? 'ntf-qrow ntf-qrow--on' : 'ntf-qrow'}
                              onClick={() => patch({ id: row.id })}
                              onKeyDown={(event) => {
                                if (event.key === 'Enter' || event.key === ' ') {
                                  event.preventDefault();
                                  patch({ id: row.id });
                                }
                              }}>
                            <td className="ntf-qrow__main">
                              <span className="ntf-row__title"><b>{eventTitle(row)}</b></span>
                              <span className="ntf-row__context">{row.employee.full_name}</span>
                            </td>
                            <td data-label="Офис">{row.office_name ?? '—'}</td>
                            <td data-label="Статус"><StatusPill status={row.status} /></td>
                            <td className="ntf-qrow__when" data-label="Создано">{moment(row.created_at, zone, from === to && Boolean(from))}</td>
                            <td className="ntf-qrow__act">
                              <span className="ntf-btn ntf-btn--small" aria-hidden="true">Открыть →</span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  <footer className="ntf-foot">
                    <span className="ntf-foot__range">
                      {shownLine(live.data.items.length, live.data.hasMore, tabCount(tab, counts))}
                    </span>
                    <span className="ntf-foot__right">
                      {tail.kind && (
                        <span className="ntf-foot__bad">Не удалось дочитать список</span>
                      )}
                      {live.data.hasMore && (
                        <button type="button" className="ntf-btn ntf-btn--small" disabled={tail.busy}
                                onClick={() => void loadMore()}>
                          {tail.busy ? 'Читаем…' : 'Показать ещё'}
                        </button>
                      )}
                      <span className="ntf-foot__lock">
                        <AppIcon name="alert" size={16} />
                        Статус отправки не подтверждает прочтение сотрудником
                      </span>
                    </span>
                  </footer>
                </>
              )}
            </div>

            {picked && (
              <section className="ntf-split__view" aria-label="Выбранное уведомление">
                <Card
                  block={card}
                  attempts={attempts}
                  zone={zone}
                  mayManage={mayManage}
                  acting={acting}
                  error={actionError}
                  onClose={() => patch({ id: null })}
                  onRetry={(row) => void act(() => api.retryNotification(row.id))}
                  onCancel={(row) => void act(() => api.cancelNotification(row.id))}
                />
              </section>
            )}
          </div>
        </PageCard>
      </div>
    </AppShell>
  );
}

// --- карточка ---------------------------------------------------------------

function Card({
  block, attempts, zone, mayManage, acting, error, onClose, onRetry, onCancel,
}: {
  block: Block<api.Notification>;
  attempts: Block<api.Attempts>;
  zone: string;
  mayManage: boolean;
  acting: boolean;
  error: string | null;
  onClose: () => void;
  onRetry: (row: api.Notification) => void;
  onCancel: (row: api.Notification) => void;
}) {
  if (block.state === 'loading') return <p className="empty">Открываем уведомление…</p>;
  if (block.state === 'denied') return <p className="empty">Сессия истекла. Войдите заново.</p>;
  if (block.state === 'error') {
    return <p className="empty empty--bad">Не удалось открыть уведомление.</p>;
  }

  const row = block.data;
  const link = relatedLink(row);
  const noRetry = retryBlockedBecause(row, mayManage);
  const noCancel = cancelBlockedBecause(row, mayManage);
  const reason = reasonTitle(row.error_message);

  return (
    <>
      <header className="view__head">
        <span className="view__icon" aria-hidden="true">
          <AppIcon name="bell" size={20} />
        </span>
        <div className="view__who">
          <h2 className="view__title">Уведомление</h2>
          <p className="view__sub">№ {row.id.slice(0, 8)}</p>
          <p className="view__badges">
            <StatusPill status={row.status} />
          </p>
        </div>
        <button type="button" className="tool" aria-label="Закрыть карточку" onClick={onClose}>
          <AppIcon name="cross" size={18} />
        </button>
      </header>

      <dl className="facts">
        <div><dt>Получатель</dt><dd>{row.employee.full_name}</dd></div>
        <div>
          <dt>Офис</dt>
          <dd>
            {row.office_name ?? '—'}
            {row.region_name && row.office_name ? ` · ${row.region_name}` : ''}
          </dd>
        </div>
        <div><dt>Канал</dt><dd>{CHANNEL[row.channel] ?? row.channel}</dd></div>
        <div><dt>Создано</dt><dd>{moment(row.created_at, zone, false)}</dd></div>
      </dl>

      <div className="view__body">
        <h3 className="doc__h1">Сообщение</h3>
        {/* Текст показывается ровно тем, что ушло: он сохранён в строке,
            а не собирается сейчас по шаблону. Разметка React экранирует
            его сама — никакого HTML из содержимого не исполняется. */}
        <div className="message">
          {row.title && <p className="message__title">{row.title}</p>}
          {row.body.split('\n').map((line, index) =>
            line.trim() ? <p key={index} className="message__line">{line}</p> : null,
          )}
        </div>

        {link ? (
          <p className="message__link">
            <Link to={link.to}>{link.title}</Link>
            <AppIcon name="arrow" size={16} />
          </p>
        ) : row.related_entity_type ? (
          // Вид объекта сервер знает, а страницы для него в CRM нет.
          // Ссылка «в никуда» хуже её отсутствия.
          <p className="muted">Связанный объект: {row.related_entity_type}</p>
        ) : null}

        <h3 className="doc__h1">Попытки отправки</h3>
        <Attempts block={attempts} zone={zone} />

        {reason && (
          <p className="note note--dim" role="status">
            <AppIcon name="alert" size={16} />
            {reason}
          </p>
        )}
      </div>

      {error && <p className="empty empty--bad" role="alert">{error}</p>}

      <footer className="view__foot">
        <div className="view__actions">
          <button type="button" className="btn btn--dark"
                  disabled={Boolean(noRetry) || acting}
                  title={noRetry ? `Сейчас нельзя: ${noRetry}` : undefined}
                  onClick={() => onRetry(row)}>
            <AppIcon name="refresh" size={16} />
            {acting ? 'Отправляем запрос…' : 'Повторить отправку'}
          </button>
          <button type="button" className="btn"
                  disabled={Boolean(noCancel) || acting}
                  title={noCancel ? `Сейчас нельзя: ${noCancel}` : undefined}
                  onClick={() => onCancel(row)}>
            <AppIcon name="archive" size={16} />
            Снять с отправки
          </button>
        </div>
        <p className="view__note">{deliveryNote(row)}</p>
        {row.status === 'PENDING' && row.next_attempt_at && (
          <p className="view__note view__note--dim">
            Следующая попытка не раньше {moment(row.next_attempt_at, zone, false)}.
          </p>
        )}
        <p className="view__foot-link">
          <Link to={`/employees/${row.employee_id}`}>Карточка сотрудника</Link>
          <AppIcon name="arrow" size={16} />
        </p>
      </footer>
    </>
  );
}

function Attempts({ block, zone }: {
  block: Block<api.Attempts>;
  zone: string;
}) {
  if (block.state === 'loading') return <p className="empty">Читаем историю…</p>;
  if (block.state === 'error') {
    return <p className="empty empty--bad">Не удалось загрузить историю попыток.</p>;
  }
  if (block.state === 'denied') return null;

  const { items, kept } = block.data;

  // Предупреждение о неполноте живёт РЯДОМ со списком, а не вместо него.
  // Записанные попытки — настоящие и их надо показать; то, что до них
  // были другие, от этого не перестаёт быть правдой.
  const note = kept ? null : (
    <p className="muted">{historyNotKept(items.length > 0)}</p>
  );

  if (items.length === 0) {
    return note ?? (
      <p className="muted">Попыток ещё не было — уведомление ждёт очереди.</p>
    );
  }

  return (
    <>
      {note}
      <ol className="attempts">
        {items.map((item) => (
          <li key={item.number}>
            <AppIcon name={item.outcome === 'SENT' ? 'check' : 'alert'} size={16} />
            <span className="attempts__time">{moment(item.attempted_at, zone, false)}</span>
            <span className="attempts__what">{attemptTitle(item)}</span>
          </li>
        ))}
      </ol>
    </>
  );
}

// --- мелочи -----------------------------------------------------------------

function Tile({ title, value }: { title: string; value: number | undefined }) {
  return (
    <li className="ntf-summary__item">
      <span>{title}</span>
      {/* Пока сводка не пришла — прочерк, а не ноль. Ноль означал бы,
          что уведомлений нет. */}
      <b>{value === undefined ? '—' : value}</b>
    </li>
  );
}

function StatusPill({ status }: { status: string }) {
  const icon =
    status === 'SENT' || status === 'READ' ? 'check'
      : status === 'FAILED' ? 'alert'
        : status === 'CANCELLED' ? 'archive'
          : status === 'RUNNING' ? 'refresh'
            : 'clock';
  return (
    <span className={`state state--${status.toLowerCase()}`}>
      <AppIcon name={icon} size={16} />
      {STATUS[status] ?? status}
    </span>
  );
}

/**
 * Момент в поясе организации.
 *
 * Пояс приходит с сервера вместе со сводкой — своей арифметики над
 * поясами здесь нет, она разошлась бы с границами суток, по которым
 * сервер режет период. Для одного дня достаточно времени; для периода
 * дата обязательна, иначе «10:32» ничего не значит.
 */
// Формат времени общий на всю CRM: `features/time/zone`. Своя копия
// здесь означала бы, что «Уведомления» и «Посещаемость» однажды покажут
// один и тот же момент по-разному — а разбираться в этом будет человек,
// у которого и так не сходятся отметки.
export { clock, moment };

/** Честная подпись под списком. */
export function shownLine(
  shown: number,
  hasMore: boolean,
  total: number | null,
): string {
  if (shown === 0) return '';
  const word = plural(shown, 'уведомление', 'уведомления', 'уведомлений');
  if (total === null) return `Показано ${shown} ${word}`;
  if (!hasMore && shown >= total) return `Показаны все ${shown} ${word}`;
  return `Показано ${shown} ${word} из ${total}`;
}

function plural(n: number, one: string, few: string, many: string): string {
  const tens = n % 100;
  if (tens >= 11 && tens <= 14) return many;
  const units = n % 10;
  if (units === 1) return one;
  if (units >= 2 && units <= 4) return few;
  return many;
}

/**
 * Что сказать про уведомление старше самой истории.
 *
 * Пустой список у отправленного читался бы как «попыток не было» — это
 * неправда, и предупреждение обязано остаться на месте, сколько бы раз
 * строку ни повторяли и сколько бы новых попыток ни записалось потом.
 *
 * Счётчик попыток здесь не называется намеренно. Он относится к ТЕКУЩЕМУ
 * циклу повторов и обнуляется при нажатии «Повторить»: выдать его за
 * число утраченных попыток значило бы назвать число, которого никто не
 * знает.
 */
export function historyNotKept(hasRecorded: boolean): string {
  if (hasRecorded) {
    return 'Записаны не все попытки: те, что были до появления истории, '
      + 'не сохранялись, и восстановить их неоткуда.';
  }
  return 'История попыток по этому уведомлению не велась: оно старше '
    + 'самой истории, и восстановить её неоткуда.';
}

export function emptyText(search: string, tab: Tab, period: boolean): string {
  if (search) return 'По этому запросу ничего не нашлось.';
  if (tab !== 'all') return 'В этом состоянии уведомлений нет. Проверьте другие вкладки.';
  if (period) return 'За выбранный период уведомлений не было.';
  return 'Уведомлений пока нет. Они заводятся сами, когда происходит событие.';
}
