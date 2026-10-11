/**
 * Вкладка «Лента событий» страницы «Уведомления».
 *
 * Это лента событий, а не переписка: строка — событие (заявка, справка,
 * исправление отметки, обращение), справа — переход к самой записи.
 *
 * Правила, на которых держится вкладка:
 *
 * — ЧИСЛА С СЕРВЕРА. Итог и диапазон «1–20 из N» берутся из счётчиков
 *   того же ответа (`counts[scope]`), а не из длины загруженной страницы;
 * — ПАГИНАЦИЯ ПО КУРСОРУ. Номеров страниц у API нет, есть только
 *   курсор. Курсоры посещённых страниц хранятся; на дальнюю страницу
 *   переходим одним запросом с большим `limit` (до серверного предела)
 *   и берём её хвост — так курсор остаётся настоящим, а не вычисленным;
 * — УСТАРЕВШИЙ ОТВЕТ НЕ ПЕРЕЗАПИСЫВАЕТ ТЕКУЩИЙ. Каждый запрос получает
 *   номер и `AbortController`; ответ на прежний отбор отбрасывается;
 * — ПРОЧТЕНИЕ — ТОЛЬКО ПОСЛЕ ОТВЕТА СЕРВЕРА. Точка гаснет, когда сервер
 *   принял отметку; отказ ничего не гасит и объясняется словами.
 *   Прочтение не закрывает событие: «Требует действия» считает сервер
 *   по самой записи, а не по точке;
 * — ЖИВОГО ОБНОВЛЕНИЯ У ВКЛАДКИ НЕТ и здесь оно не заводится:
 *   колокольчик опрашивает счётчики сам, а новый опрос или второе
 *   подключение ради этой страницы были бы лишней нагрузкой.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import * as api from '../../api/crm';
import { ApiFailure, messageFor } from '../../api/errors';
import { AppSelect } from '../../components/AppSelect';
import { facts } from './feed-model';
import {
  FEED_KINDS, FEED_MAX_LIMIT, FEED_PAGE_SIZE, FEED_SEGMENTS, contextLine, dayLabel,
  initials, isKindScope, knownScope, pageCount, pageNumbers, rangeLine,
  safeActionPath, scopeTotal, timeLabel, typeLabel,
} from './feed-page';
import { PageCard, type PageView } from './PageCard';
import { useSession } from '../auth/session';
import { useStickyState } from '../shell/sticky';

type Loaded = {
  scope: string;
  page: number;
  items: api.FeedEvent[];
  counts: api.FeedCounts;
  hasMore: boolean;
  windowDays: number;
};

type Status = 'loading' | 'ready' | 'error' | 'denied';

/** Курсоры одного отбора: ключ k — курсор, с которого начинается страница k+1. */
type Cursors = { scope: string; map: Map<number, string | null> };

const SLOW_AFTER_MS = 3500;

const isDenied = (error: unknown) =>
  error instanceof ApiFailure && (error.kind === 'session' || error.kind === 'credentials');

const isGone = (error: unknown) => error instanceof ApiFailure && error.status === 404;

export function FeedBoard({
  opened,
  onOpenDetail,
  onView,
}: {
  opened: string;
  onOpenDetail: (id: string) => void;
  onView: (view: PageView) => void;
}) {
  const navigate = useNavigate();
  const [stored, setStored] = useStickyState<string>('notifications.filter', 'all');
  const scope = knownScope(stored);
  const [page, setPage] = useState(1);
  const [reload, setReload] = useState(0);
  const [data, setData] = useState<Loaded | null>(null);
  const [status, setStatus] = useState<Status>('loading');
  const [failure, setFailure] = useState<string | null>(null);
  const [slow, setSlow] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  const [readingAll, setReadingAll] = useState(false);
  const [menu, setMenu] = useState<string | null>(null);

  const cursors = useRef<Cursors>({ scope, map: new Map([[0, null]]) });
  const request = useRef(0);
  // Замки — в ссылках: состояние обновляется асинхронно, и два быстрых
  // нажатия успели бы увидеть его прежним и отправить два запроса.
  const reading = useRef(new Set<string>());
  const allLock = useRef(false);
  const opening = useRef(false);

  const forget = useCallback((forScope: string) => {
    cursors.current = { scope: forScope, map: new Map([[0, null]]) };
  }, []);

  // --- чтение страницы -----------------------------------------------------

  useEffect(() => {
    const id = ++request.current;
    const stop = new AbortController();
    if (cursors.current.scope !== scope) forget(scope);
    const store = cursors.current;
    setStatus('loading');
    setFailure(null);
    setSlow(false);
    const slowTimer = window.setTimeout(() => {
      if (request.current === id) setSlow(true);
    }, SLOW_AFTER_MS);

    fetchPage(scope, page, store, stop.signal)
      .then((result) => {
        if (request.current !== id || stop.signal.aborted) return;
        const total = scopeTotal(scope, result.counts);
        // Последняя страница опустела (события прочитали, закрыли или они
        // вышли из окна) — шаг назад, а не пустая таблица с итогом «26».
        if (result.items.length === 0 && page > 1) {
          const last = pageCount(total) ?? 1;
          setPage(Math.max(1, Math.min(page - 1, last)));
          return;
        }
        setData({
          scope, page, items: result.items, counts: result.counts,
          hasMore: result.hasMore, windowDays: result.windowDays,
        });
        setStatus('ready');
      })
      .catch((error: unknown) => {
        if (request.current !== id || stop.signal.aborted) return;
        if (isDenied(error)) {
          // Сессия кончилась или доступ отозван: показанное убирается,
          // а не остаётся висеть до перезагрузки.
          setData(null);
          setStatus('denied');
          return;
        }
        setFailure(messageFor(error));
        setStatus('error');
      })
      .finally(() => window.clearTimeout(slowTimer));

    return () => {
      stop.abort();
      window.clearTimeout(slowTimer);
    };
  }, [scope, page, reload, forget]);

  // --- отбор ---------------------------------------------------------------

  function choose(next: string) {
    const value = knownScope(next);
    setMenu(null);
    setNote(null);
    if (value === scope) return;
    // Строки прежнего отбора скрываются сразу (`shown` сверяет отбор),
    // а счётчики в заголовке остаются до нового ответа — без мигания.
    setPage(1);
    setStored(value);
  }

  // --- прочтение -----------------------------------------------------------

  /** `true` — сервер принял отметку (или она уже стояла). */
  async function markRead(event: api.FeedEvent): Promise<'ok' | 'gone' | 'denied' | 'failed'> {
    if (event.read_at) return 'ok';
    if (reading.current.has(event.id)) return 'failed';
    reading.current.add(event.id);
    setPending(new Set(reading.current));
    setNote(null);
    try {
      const counts = await api.readFeedEvent(event.id);
      const at = new Date().toISOString();
      setData((was) => was && {
        ...was,
        counts,
        items: was.items.map((one) => (one.id === event.id ? { ...one, read_at: at } : one)),
      });
      // Порядок ленты зависит от прочтения — сохранённые курсоры больше
      // не описывают те же границы страниц.
      forget(scope);
      return 'ok';
    } catch (error) {
      if (isDenied(error)) {
        setData(null);
        setStatus('denied');
        return 'denied';
      }
      if (isGone(error)) {
        setNote('Событие больше недоступно: запись изменилась, закрыта или нет доступа. Список обновлён.');
        setReload((n) => n + 1);
        return 'gone';
      }
      setNote(`Не удалось отметить прочитанным. ${messageFor(error)}`);
      return 'failed';
    } finally {
      reading.current.delete(event.id);
      setPending(new Set(reading.current));
    }
  }

  async function open(event: api.FeedEvent) {
    const path = safeActionPath(event.action_url);
    if (!path || opening.current) return;
    opening.current = true;
    try {
      const result = await markRead(event);
      // Запись исчезла или сессия кончилась — переходить некуда.
      if (result === 'gone' || result === 'denied') return;
      navigate(path);
    } finally {
      opening.current = false;
    }
  }

  async function readAll() {
    if (allLock.current) return;
    allLock.current = true;
    setReadingAll(true);
    setNote(null);
    try {
      const result = await api.readAllFeed();
      setData((was) => was && {
        ...was,
        counts: {
          all: result.all, unread: result.unread, action: result.action,
          requests: result.requests, documents: result.documents, questions: result.questions,
        },
      });
      forget(scope);
      setPage(1);
      setReload((n) => n + 1);
    } catch (error) {
      if (isDenied(error)) {
        setData(null);
        setStatus('denied');
      } else {
        setNote(`Не удалось отметить все прочитанными. ${messageFor(error)}`);
      }
    } finally {
      allLock.current = false;
      setReadingAll(false);
    }
  }

  // --- разметка ------------------------------------------------------------

  const shown = data && data.scope === scope ? data : null;
  const counts = shown?.counts ?? data?.counts ?? null;
  const total = scopeTotal(scope, shown?.counts ?? null);
  const pages = pageCount(total);
  const unread = counts?.unread ?? 0;
  const segment = isKindScope(scope) ? null : scope;
  const kind = isKindScope(scope) ? scope : '';

  const action = (
    <button type="button" className="ntf-btn ntf-btn--outline"
            onClick={() => void readAll()}
            disabled={!counts || unread === 0 || readingAll || status === 'denied'}>
      <span aria-hidden="true">✓</span>
      {readingAll ? 'Отмечаем…' : 'Прочитать все'}
    </button>
  );

  return (
    <PageCard view="feed" count={status === 'denied' ? null : counts?.all ?? null} action={action} onView={onView}>
      <div className="ntf-filters">
        <div className="ntf-seg" role="group" aria-label="Отбор событий">
          {FEED_SEGMENTS.map((one) => (
            <button key={one.key} type="button" aria-pressed={segment === one.key}
                    className={segment === one.key ? 'ntf-seg__btn ntf-seg__btn--on' : 'ntf-seg__btn'}
                    onClick={() => choose(one.key)}>
              {one.title}
            </button>
          ))}
        </div>
        <AppSelect label="Тип событий" className="ntf-filters__type" value={kind}
                   empty="Все типы" minWidth={180}
                   options={FEED_KINDS.map((one) => ({ value: one.key, label: one.title }))}
                   onChange={(value) => choose(value || 'all')} />
      </div>

      {note && <p className="ntf-note ntf-note--bad" role="alert">{note}</p>}

      {status === 'denied' && (
        <p className="ntf-empty ntf-empty--bad" role="alert">
          Сессия истекла или доступ к ленте закрыт. Войдите заново.
        </p>
      )}

      {status === 'loading' && !shown && (
        <p className="ntf-empty" role="status">
          {slow ? 'Сервер отвечает медленно. Продолжаем загружать ленту…' : 'Загружаем ленту…'}
        </p>
      )}

      {status === 'error' && (
        <p className="ntf-empty ntf-empty--bad" role="alert">
          Не удалось загрузить ленту. Это ошибка запроса, а не пустой список.
          {failure ? ` ${failure}` : ''}{' '}
          <button type="button" className="ntf-link" onClick={() => setReload((n) => n + 1)}>
            Повторить
          </button>
        </p>
      )}

      {shown && status !== 'error' && status !== 'denied' && (
        shown.items.length === 0 ? (
          <p className="ntf-empty">
            {(shown.counts.all ?? 0) === 0
              ? `За последние ${shown.windowDays} дней событий не было.`
              : 'В этом отборе событий нет. Выберите другой отбор.'}
          </p>
        ) : (
          <div className="ntf-table" aria-busy={status === 'loading'}>
            <div className="ntf-table__head" aria-hidden="true">
              <span>Событие</span>
              <span>Тип</span>
              <span>Создано</span>
              <span>Действие</span>
            </div>
            {status === 'loading' && slow && (
              <p className="ntf-note" role="status">Сервер отвечает медленно…</p>
            )}
            <ul className="ntf-list" aria-label="События">
              {shown.items.map((event) => (
                <FeedItem
                  key={event.id}
                  event={event}
                  open={opened === event.id}
                  menu={menu === event.id}
                  reading={pending.has(event.id)}
                  onMenu={(on) => setMenu(on ? event.id : null)}
                  onOpen={() => void open(event)}
                  onRead={() => { setMenu(null); void markRead(event); }}
                  onDetail={() => { setMenu(null); onOpenDetail(opened === event.id ? '' : event.id); }}
                />
              ))}
            </ul>
          </div>
        )
      )}

      {shown && shown.items.length > 0 && status !== 'error' && status !== 'denied' && (
        <footer className="ntf-foot">
          <span className="ntf-foot__range">{rangeLine(shown.page, shown.items.length, total)}</span>
          <Pager current={shown.page} pages={pages} hasMore={shown.hasMore}
                 busy={status === 'loading'} onPage={(n) => { setMenu(null); setPage(n); }} />
        </footer>
      )}
    </PageCard>
  );
}

// --- строка ------------------------------------------------------------------

function FeedItem({
  event, open, menu, reading, onMenu, onOpen, onRead, onDetail,
}: {
  event: api.FeedEvent;
  open: boolean;
  menu: boolean;
  reading: boolean;
  onMenu: (on: boolean) => void;
  onOpen: () => void;
  onRead: () => void;
  onDetail: () => void;
}) {
  const { zone } = useZone();
  const unread = !event.read_at;
  const path = safeActionPath(event.action_url);
  const context = contextLine(event);
  const day = dayLabel(event.created_at, zone);
  const time = timeLabel(event.created_at, zone);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) onMenu(false);
    };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') onMenu(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', key);
    };
  }, [menu, onMenu]);

  return (
    <li className={unread ? 'ntf-row ntf-row--unread' : 'ntf-row'} data-testid="feed-row">
      <div className="ntf-row__event">
        {event.employee_id && initials(event.employee_name) ? (
          <span className="ntf-avatar" aria-hidden="true">{initials(event.employee_name)}</span>
        ) : (
          <span className="ntf-avatar ntf-avatar--none" aria-hidden="true" />
        )}
        <span className="ntf-row__text">
          <span className="ntf-row__title">
            <b>{event.title || typeLabel(event)}</b>
            {unread && <span className="ntf-dot" role="img" aria-label="Не прочитано" />}
          </span>
          {context && <span className="ntf-row__context">{context}</span>}
        </span>
      </div>

      <div className="ntf-row__type">
        <span className="ntf-row__label">Тип</span>
        <span>{typeLabel(event)}</span>
        {event.requires_action && <span className="ntf-tag">Требует действия</span>}
      </div>

      <div className="ntf-row__when">
        <span className="ntf-row__label">Создано</span>
        {day ? (
          <>
            <span>{day}</span>
            <span className="ntf-row__time">{time}</span>
          </>
        ) : (
          <span className="ntf-row__time">Дата не указана</span>
        )}
      </div>

      <div className="ntf-row__actions">
        <button type="button" className="ntf-btn ntf-btn--small"
                disabled={!path || reading}
                title={path ? undefined : 'Для этого события нет страницы в CRM'}
                aria-label={`Открыть: ${event.title || typeLabel(event)}`}
                onClick={onOpen}>
          Открыть <span aria-hidden="true">→</span>
        </button>
        <div className="ntf-menu" ref={menuRef}>
          <button type="button" className="ntf-menu__toggle" aria-haspopup="menu"
                  aria-expanded={menu} aria-label={`Ещё действия: ${event.title || typeLabel(event)}`}
                  onClick={() => onMenu(!menu)}>
            <span aria-hidden="true">⋯</span>
          </button>
          {menu && (
            <div className="ntf-menu__list" role="menu">
              {unread && (
                <button type="button" role="menuitem" disabled={reading} onClick={onRead}>
                  {reading ? 'Отмечаем…' : 'Отметить прочитанным'}
                </button>
              )}
              <button type="button" role="menuitem" onClick={onDetail}>
                {open ? 'Скрыть подробности' : 'Подробности'}
              </button>
            </div>
          )}
        </div>
      </div>

      {open && <FeedDetailPanel id={event.id} />}
    </li>
  );
}

// --- подробности ---------------------------------------------------------------

function FeedDetailPanel({ id }: { id: string }) {
  const [card, setCard] = useState<api.FeedDetail | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    const stop = new AbortController();
    setCard(null);
    setFailed(null);
    api.feedEvent(id, stop.signal)
      .then((data) => { if (!stop.signal.aborted) setCard(data); })
      .catch((error: unknown) => {
        if (stop.signal.aborted) return;
        setFailed(
          isGone(error)
            ? 'Событие больше недоступно: запись изменилась, закрыта или удалена.'
            : isDenied(error)
              ? 'Нет доступа к этой записи или сессия истекла.'
              : messageFor(error),
        );
      });
    return () => stop.abort();
  }, [id]);

  return (
    <div className="ntf-detail" aria-label="Подробности события" role="region">
      {failed && <p className="ntf-empty ntf-empty--bad">{failed}</p>}
      {!failed && !card && <p className="ntf-empty">Открываем подробности…</p>}
      {card && (
        <>
          <dl className="ntf-detail__facts">
            {facts(card).map((fact) => (
              <div key={fact.key}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>
            ))}
            {card.status_label && <div><dt>Статус</dt><dd>{card.status_label}</dd></div>}
          </dl>
          {card.comment && <p className="ntf-detail__comment">{card.comment}</p>}
        </>
      )}
    </div>
  );
}

// --- пагинация -----------------------------------------------------------------

function Pager({ current, pages, hasMore, busy, onPage }: {
  current: number;
  pages: number | null;
  hasMore: boolean;
  busy: boolean;
  onPage: (page: number) => void;
}) {
  // Без итога номера не рисуются: «следующая» — по `has_more` сервера.
  const last = pages ?? current + (hasMore ? 1 : 0);
  const numbers = pages ? pageNumbers(current, pages) : [];
  return (
    <nav className="ntf-pager" aria-label="Страницы ленты">
      <button type="button" className="ntf-pager__btn" aria-label="Предыдущая страница"
              disabled={busy || current <= 1} onClick={() => onPage(current - 1)}>
        <span aria-hidden="true">‹</span>
      </button>
      {numbers.map((n, index) => (n === null ? (
        <span key={`gap-${index}`} className="ntf-pager__gap" aria-hidden="true">…</span>
      ) : (
        <button key={n} type="button" aria-current={n === current ? 'page' : undefined}
                aria-label={`Страница ${n}`}
                className={n === current ? 'ntf-pager__btn ntf-pager__btn--on' : 'ntf-pager__btn'}
                disabled={busy || n === current} onClick={() => onPage(n)}>
          {n}
        </button>
      )))}
      <button type="button" className="ntf-pager__btn" aria-label="Следующая страница"
              disabled={busy || !hasMore || current >= last} onClick={() => onPage(current + 1)}>
        <span aria-hidden="true">›</span>
      </button>
    </nav>
  );
}

// --- сеть ------------------------------------------------------------------------

type PageResult = { items: api.FeedEvent[]; counts: api.FeedCounts; hasMore: boolean; windowDays: number };

/**
 * Страница `n` отбора `scope`.
 *
 * Известен курсор начала — обычный запрос на `FEED_PAGE_SIZE` строк.
 * Не известен — идём от ближайшего известного курсора одним запросом
 * на нужное число строк (шагами не больше серверного предела) и
 * берём хвост. Курсоры по пути запоминаются, но только если за это
 * время набор курсоров не сбросили (прочтение меняет порядок).
 */
async function fetchPage(scope: string, n: number, store: Cursors, signal: AbortSignal): Promise<PageResult> {
  const size = FEED_PAGE_SIZE;
  const hopPages = Math.max(1, Math.floor(FEED_MAX_LIMIT / size));
  let k = 0;
  for (const key of store.map.keys()) if (key <= n - 1 && key > k) k = key;
  let cursor = store.map.get(k) ?? null;

  while (n - k > hopPages) {
    const hop = await api.feed(
      { scope, limit: String(hopPages * size), ...(cursor ? { cursor } : {}) }, signal,
    );
    if (!hop.has_more || !hop.next_cursor) {
      return { items: [], counts: hop.counts, hasMore: false, windowDays: hop.window_days };
    }
    k += hopPages;
    cursor = hop.next_cursor;
    store.map.set(k, cursor);
  }

  const span = n - k;
  const result = await api.feed(
    { scope, limit: String(span * size), ...(cursor ? { cursor } : {}) }, signal,
  );
  const items = result.items.slice((span - 1) * size);
  if (result.next_cursor) store.map.set(n, result.next_cursor);
  return { items, counts: result.counts, hasMore: result.has_more, windowDays: result.window_days };
}

// --- пояс ---------------------------------------------------------------------------

function useZone(): { zone: string } {
  const session = useSession();
  return { zone: session.status === 'authenticated' ? session.user.timezone : '' };
}
