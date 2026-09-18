/**
 * «Обращения»: вопросы сотрудников из Telegram и ответы HR.
 *
 * Не «Заявки»: отпуск, больничный и исправление отметки здесь только
 * обсуждаются, оформляются они на своих страницах. Отсюда к ним ведут
 * ссылки — и только те, что backend действительно умеет открыть.
 *
 * Три колонки: очередь, переписка, контекст сотрудника. Выбор, вкладка и
 * фильтры живут в адресе и переживают обновление и ссылку коллеге.
 * Данные обновляются опросом раз в 15 секунд, пока вкладка видна: другого
 * канала обновлений у CRM нет. Опрос не выбрасывает показанное — ни
 * колонки, ни прокрутка, ни набранный ответ не прыгают.
 *
 * Черновик ассистента — подсказка, а не ответ. Он опирается только на
 * опубликованные документы базы знаний, показывает уверенность и
 * источник и никогда не уходит сотруднику сам: отправляет человек.
 */

import {
  useCallback, useEffect, useMemo, useRef, useState,
  type KeyboardEvent, type ReactNode,
} from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { AppShell, initials } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { AppFilterButton, AppSegmentedControl, AppSelectField } from '../components/AppSelect';
import { useSession } from '../features/auth/session';
import { shift, today, useBlock, type Block } from '../features/dashboard/data';
import { useStickyState } from '../features/shell/sticky';
import '../styles/questions.css';

const REFRESH_MS = 15_000;
const PAGE = 30;
const MAX_LIMIT = 200;

const TABS: { key: api.QuestionStatus; title: string }[] = [
  { key: 'NEW', title: 'Новые' },
  { key: 'IN_PROGRESS', title: 'В работе' },
  { key: 'WAITING_EMPLOYEE', title: 'Ждут сотрудника' },
  { key: 'CLOSED', title: 'Закрытые' },
];

/** Эмодзи для ответа: короткий набор, который к месту в переписке HR. */
const EMOJI = ['🙂', '👍', '🙏', '✅', '📄', '📌', '⏰', '❗'];

/** Служебные события, которым не место в ленте переписки. */
const HIDDEN_EVENTS = new Set(['TAKEN', 'ASSIGNED', 'TRANSFERRED', 'PRIORITY', 'CATEGORY']);

const QUICK = [
  { key: 'all', title: 'Все' },
  { key: 'unanswered', title: 'Без ответа' },
  { key: 'urgent', title: 'Срочные' },
] as const;
type QuickKey = (typeof QUICK)[number]['key'];

export const STATUS_TITLE: Record<api.QuestionStatus, string> = {
  NEW: 'Новое',
  IN_PROGRESS: 'В работе',
  WAITING_EMPLOYEE: 'Ждёт сотрудника',
  CLOSED: 'Закрыто',
};

export const CATEGORY_TITLE: Record<api.QuestionCategory, string> = {
  VACATION: 'Отпуск',
  SICK_LEAVE: 'Больничный',
  ATTENDANCE: 'Посещаемость',
  SCHEDULE: 'График',
  SALARY: 'Зарплата',
  DOCUMENTS: 'Документы',
  TELEGRAM: 'Telegram',
  OTHER: 'Другое',
};

const PRIORITY_TITLE: Record<api.QuestionPriority, string> = {
  LOW: 'Низкий приоритет',
  NORMAL: 'Обычный приоритет',
  HIGH: 'Высокий приоритет',
  URGENT: 'Срочно',
};

const PERIODS = [
  { key: '', title: 'За всё время' },
  { key: 'today', title: 'Сегодня' },
  { key: '7', title: 'За 7 дней' },
  { key: '30', title: 'За 30 дней' },
];

const DELIVERY_TITLE: Record<api.DeliveryStatus, string> = {
  QUEUED: 'В очереди Telegram',
  DELIVERED: 'Доставлено',
  READ: 'Прочитано',
  FAILED: 'Не доставлено',
  UNKNOWN: 'Доставка неизвестна',
};

const TELEGRAM_REASON: Record<string, string> = {
  not_linked: 'Telegram не подключён',
  pending_confirmation: 'привязка Telegram ждёт подтверждения',
  link_revoked: 'привязка Telegram отозвана',
  link_blocked: 'Telegram заблокирован',
  employee_inactive: 'сотрудник не работает',
  no_assignment: 'у сотрудника нет назначения',
  organization_inactive: 'организация неактивна',
};

const REQUEST_STATUS: Record<string, string> = {
  SUBMITTED: 'Подана',
  IN_REVIEW: 'На рассмотрении',
  APPROVED: 'Одобрена',
  REJECTED: 'Отклонена',
  CANCELLED: 'Отменена',
};

const DOCUMENT_STATUS: Record<string, string> = {
  MISSING: 'Нет файла',
  UPLOADED: 'Загружен',
  GENERATED_LATER: 'Будет позже',
  REVIEW: 'На проверке',
};

const EMPLOYMENT: Record<string, [string, string]> = {
  ACTIVE: ['ok', 'Активен'],
  PROBATION: ['ok', 'Испытательный срок'],
  SUSPENDED: ['warn', 'Приостановлен'],
  TERMINATED: ['off', 'Уволен'],
  ARCHIVED: ['off', 'В архиве'],
};

const CLOSE_REASONS = ['Вопрос решён', 'Дубликат обращения', 'Не по адресу HR', 'Другое'];

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

// --- страница ----------------------------------------------------------------

export function QuestionsPage() {
  const session = useSession();
  const user = session.status === 'authenticated' ? session.user : null;
  const permissions = user?.permissions ?? [];
  const canRead = permissions.includes('questions.read');
  const canKnowledge = permissions.includes('knowledge.read');

  const [params, setParams] = useSearchParams();
  const rawStatus = params.get('status');
  const status: api.QuestionStatus | 'all' = rawStatus === 'all'
    ? 'all'
    : TABS.find((tab) => tab.key === rawStatus)?.key ?? 'NEW';
  const office = params.get('office_id') ?? '';
  const assignee = params.get('assignee') ?? '';
  const category = params.get('category') ?? '';
  const priority = params.get('priority') ?? '';
  const period = params.get('period') ?? '';
  const search = params.get('q') ?? '';
  const quick: QuickKey = QUICK.find((one) => one.key === params.get('quick'))?.key ?? 'all';
  const picked = params.get('id') ?? '';

  const patch = useCallback(
    (changes: Record<string, string | null>) => {
      setParams(
        (was) => {
          const next = new URLSearchParams(was);
          for (const [key, value] of Object.entries(changes)) {
            if (value) next.set(key, value);
            else next.delete(key);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  // --- поиск с задержкой: запрос на каждую букву перегружал бы сервер ---
  const [draftSearch, setDraftSearch] = useState(search);
  useEffect(() => setDraftSearch(search), [search]);
  useEffect(() => {
    if (draftSearch === search) return;
    const timer = window.setTimeout(() => patch({ q: draftSearch.trim() || null, id: null }), 350);
    return () => window.clearTimeout(timer);
  }, [draftSearch, search, patch]);

  // --- опрос: только пока вкладка видна ---
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const bump = () => {
      if (!document.hidden) setTick((n) => n + 1);
    };
    const timer = window.setInterval(bump, REFRESH_MS);
    document.addEventListener('visibilitychange', bump);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', bump);
    };
  }, []);
  const now = useNow();

  const filters = useMemo<api.QuestionQuery>(
    () => ({
      ...(office ? { office_id: office } : {}),
      ...(assignee ? { assignee } : {}),
      ...(category ? { category } : {}),
      ...(priority ? { priority } : {}),
      ...periodRange(period),
      ...(search ? { search } : {}),
    }),
    [office, assignee, category, priority, period, search],
  );
  const filterKey = JSON.stringify(filters);
  const listKey = `${filterKey}|${status}|${quick}`;

  const [pages, setPages] = useState(1);
  useEffect(() => setPages(1), [listKey]);
  const limit = String(Math.min(PAGE * pages, MAX_LIMIT));

  const [list, reloadList, listRefresh] = useBlock(
    (signal) =>
      api.questionList(
        {
          ...filters,
          ...(status !== 'all' ? { status } : {}),
          ...(quick !== 'all' ? { quick } : {}),
          limit,
        },
        signal,
      ),
    `${listKey}|${limit}|${tick}`,
    canRead,
  );
  const [counts, reloadCounts] = useBlock(
    (signal) => api.questionCounts({ ...filters, ...(status !== 'all' ? { status } : {}) }, signal),
    `${filterKey}|${status}|${tick}`,
    canRead,
  );
  const [offices] = useBlock(
    (signal) => api.offices(signal).then((body) => body.items.filter((one) => one.status === 'ACTIVE')),
    'offices',
    canRead,
  );
  const [assignees] = useBlock(
    (signal) => api.questionAssignees(signal),
    'question-assignees',
    canRead,
  );

  const items = list.state === 'ready' ? list.data.items : [];
  const currentId = picked || items[0]?.id || '';
  const row = items.find((one) => one.id === currentId) ?? null;

  const [detailBlock, reloadDetail] = useBlock(
    (signal) => api.question(currentId, signal),
    `${currentId}|${tick}`,
    canRead && Boolean(currentId),
  );
  const [contextBlock, reloadContext] = useBlock(
    (signal) => api.questionContext(currentId, signal),
    `${currentId}|${Math.floor(tick / 4)}`,
    canRead && Boolean(currentId),
  );

  // Ответ действия показывается сразу, не дожидаясь следующего опроса.
  const [override, setOverride] = useState<api.Question | null>(null);
  useEffect(() => setOverride(null), [detailBlock]);
  const detail: api.Question | null = override?.id === currentId
    ? override
    : detailBlock.state === 'ready' && detailBlock.data.id === currentId
      ? detailBlock.data
      : null;

  // Открыл — значит прочитал. Один раз на обращение, без повторов в опросе.
  const readOnce = useRef(new Set<string>());
  useEffect(() => {
    if (!detail || !detail.unread || readOnce.current.has(detail.id)) return;
    readOnce.current.add(detail.id);
    api.readQuestion(detail.id)
      .then(() => {
        reloadList();
        reloadCounts();
      })
      .catch(() => readOnce.current.delete(detail.id));
  }, [detail, reloadList, reloadCounts]);

  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: 'error' | 'ok'; text: string } | null>(null);
  useEffect(() => setNotice(null), [currentId]);

  const act = useCallback(
    async (label: string, action: () => Promise<api.Question>, done?: string) => {
      setBusy(label);
      setNotice(null);
      try {
        const fresh = await action();
        setOverride(fresh);
        reloadDetail();
        reloadList();
        reloadCounts();
        reloadContext();
        if (done) setNotice({ tone: 'ok', text: done });
        return fresh;
      } catch (error) {
        setNotice({ tone: 'error', text: failureText(error) });
        if (error instanceof ApiFailure && error.kind === 'conflict') reloadDetail();
        return null;
      } finally {
        setBusy(null);
      }
    },
    [reloadDetail, reloadList, reloadCounts, reloadContext],
  );

  // Набранный ответ у каждого обращения свой и не теряется при переходе —
  // ни к другому обращению, ни в другой раздел CRM.
  const [replies, setReplies] = useStickyState<Record<string, string>>('questions.replies', {});
  const reply = replies[currentId] ?? '';
  const setReply = useCallback(
    (value: string) => setReplies((was) => ({ ...was, [currentId]: value })),
    [currentId],
  );

  const searchInput = useRef<HTMLInputElement | null>(null);
  const [moreFilters, setMoreFilters] = useState(Boolean(category || priority || period));

  if (!canRead) {
    return (
      <AppShell breadcrumb="Обращения" section="questions">
        <div className="qs">
          <p className="qs-state qs-state--page">Нет права просматривать обращения.</p>
        </div>
      </AppShell>
    );
  }

  const tabCount = (key: api.QuestionStatus) =>
    counts.state === 'ready' ? counts.data.statuses[key] : null;
  const quickCount = (key: QuickKey) =>
    counts.state === 'ready' && key !== 'all' ? counts.data.quick[key] : null;
  const shown = items.length;
  const total = counts.state === 'ready' ? counts.data.quick.all : null;
  const officeName = (id: string) =>
    offices.state === 'ready' ? offices.data.find((one) => one.id === id)?.name ?? '' : '';
  const badges = counts.state === 'ready' && counts.data.statuses.NEW > 0
    ? { questions: counts.data.statuses.NEW }
    : {};

  function pick(id: string) {
    patch({ id });
  }

  function showHistory(person: { full_name: string }) {
    patch({ q: person.full_name, status: 'all', quick: null, id: null });
  }

  return (
    <AppShell breadcrumb="Обращения" section="questions" badges={badges}>
      <div className="qs">
        <header className="qs-head">
          <div className="qs-head__text">
            <h1 className="qs-head__title">Обращения</h1>
            <p className="qs-head__sub">Вопросы сотрудников и ответы HR</p>
          </div>
          <div className="qs-head__tools">
            <AppSelectField className="questions-select" label="Офис" value={office} onChange={(value) => patch({ office_id: value || null, id: null })}>
                <option value="">Все офисы</option>
                {offices.state === 'ready' && offices.data.map((one) => (
                  <option key={one.id} value={one.id}>{one.name}</option>
                ))}
            </AppSelectField>
            <AppSelectField className="questions-select questions-select--wide" label="Ответственный" value={assignee} onChange={(value) => patch({ assignee: value || null, id: null })}>
              <option value="">Все ответственные</option>
              {assignees.state === 'ready' && assignees.data.items.map((one) => (
                <option key={one.id} value={one.id}>{one.name}</option>
              ))}
            </AppSelectField>
            <button type="button" className="qs-icon-btn" aria-label="Искать обращение" onClick={() => searchInput.current?.focus({ preventScroll: true })}>
              <AppIcon name="search" size={20} />
            </button>
            <button
              type="button"
              className={listRefresh.busy ? 'qs-icon-btn qs-icon-btn--spin' : 'qs-icon-btn'}
              aria-label="Обновить"
              onClick={() => setTick((n) => n + 1)}
            >
              <AppIcon name="refresh" size={20} />
            </button>
          </div>
        </header>

        <AppSegmentedControl className="qs-tabs" role="tablist" label="Состояние обращений" value={status}
          options={[...TABS.map((tab) => ({ value: tab.key, label: tab.title, count: tabCount(tab.key) })), ...(status === 'all' ? [{ value: 'all', label: 'Все состояния' }] : [])]}
          onChange={(key) => patch({ status: key === 'NEW' ? null : key, quick: null, id: null })} />

        <div className="qs-grid">
          {/* --- очередь --- */}
          <section className="qs-card qs-queue" aria-label="Очередь обращений">
            <div className="qs-queue__search">
              <AppIcon name="search" size={18} />
              <input
                ref={searchInput}
                type="search"
                aria-label="Поиск обращений"
                placeholder="Поиск: ФИО, номер обращения, текст"
                value={draftSearch}
                onChange={(event) => setDraftSearch(event.target.value)}
              />
            </div>

            <div className="qs-quick">
              {QUICK.map((one) => {
                const count = quickCount(one.key);
                return (
                  <AppFilterButton key={one.key} className="qs-chip" active={quick === one.key} {...(count !== null ? { count } : {})}
                    onClick={() => patch({ quick: one.key === 'all' ? null : one.key, id: null })}>{one.title}</AppFilterButton>
                );
              })}
              <button
                type="button"
                className={moreFilters || category || priority || period ? 'qs-chip qs-chip--icon qs-chip--on' : 'qs-chip qs-chip--icon'}
                aria-label="Ещё фильтры"
                aria-expanded={moreFilters}
                onClick={() => setMoreFilters((was) => !was)}
              >
                <AppIcon name="list" size={18} />
              </button>
            </div>

            {moreFilters && (
              <div className="qs-more">
                <AppSelectField label="Категория" value={category} onChange={(value) => patch({ category: value || null, id: null })}>
                  <option value="">Все категории</option>
                  {(Object.keys(CATEGORY_TITLE) as api.QuestionCategory[]).map((key) => (
                    <option key={key} value={key}>{CATEGORY_TITLE[key]}</option>
                  ))}
                </AppSelectField>
                <AppSelectField label="Приоритет" value={priority} onChange={(value) => patch({ priority: value || null, id: null })}>
                  <option value="">Любой приоритет</option>
                  {(Object.keys(PRIORITY_TITLE) as api.QuestionPriority[]).map((key) => (
                    <option key={key} value={key}>{PRIORITY_TITLE[key]}</option>
                  ))}
                </AppSelectField>
                <AppSelectField label="Период" value={period} onChange={(value) => patch({ period: value || null, id: null })}>
                  {PERIODS.map((one) => (
                    <option key={one.key} value={one.key}>{one.title}</option>
                  ))}
                </AppSelectField>
              </div>
            )}

            <div className={listRefresh.busy && list.state === 'ready' ? 'qs-queue__list qs-queue__list--busy' : 'qs-queue__list'}>
              {list.state === 'loading' && <QueueSkeleton />}
              {list.state === 'denied' && <p className="qs-state">Нет доступа к обращениям.</p>}
              {list.state === 'error' && (
                <div className="qs-state">
                  <p>Не удалось загрузить очередь. {messageFor(new ApiFailure(list.kind as never))}</p>
                  <button type="button" className="qs-btn qs-btn--light" onClick={reloadList}>Повторить</button>
                </div>
              )}
              {list.state === 'ready' && items.length === 0 && (
                <div className="qs-state">
                  <p>{search || office || assignee || category || priority || period || quick !== 'all'
                    ? 'По этим условиям обращений нет.'
                    : 'Здесь пока пусто.'}</p>
                </div>
              )}
              {items.length > 0 && (
                <ul className="qs-rows">
                  {items.map((item) => (
                    <QueueRow
                      key={item.id}
                      row={item}
                      on={item.id === currentId}
                      now={now}
                      showStatus={status === 'all'}
                      onPick={pick}
                    />
                  ))}
                </ul>
              )}
            </div>

            <footer className="qs-queue__foot">
              <span>
                {total !== null ? `${shown} из ${total}` : list.state === 'ready' ? `${shown}` : ''}
                {listRefresh.failed && list.state === 'ready' && <em className="qs-stale"> · не обновилось</em>}
              </span>
              {list.state === 'ready' && list.data.has_more && Number(limit) < MAX_LIMIT && (
                <button type="button" className="qs-btn qs-btn--light qs-btn--sm" onClick={() => setPages((n) => n + 1)}>
                  Показать ещё
                </button>
              )}
            </footer>
          </section>

          {/* --- переписка --- */}
          <section className="qs-card qs-talk" aria-label="Переписка">
            {!currentId && list.state === 'ready' && (
              <p className="qs-state qs-state--center">Выберите обращение в очереди.</p>
            )}
            {currentId && !detail && (
              detailBlock.state === 'error' && (detailBlock.state as string) !== 'ready'
                ? (
                  <div className="qs-state qs-state--center">
                    <p>Не удалось открыть обращение.</p>
                    <button type="button" className="qs-btn qs-btn--light" onClick={reloadDetail}>Повторить</button>
                  </div>
                )
                : detailBlock.state === 'denied'
                  ? <p className="qs-state qs-state--center">Нет доступа к этому обращению.</p>
                  : <TalkSkeleton row={row} />
            )}
            {detail && (
              <Conversation
                key={detail.id}
                question={detail}
                now={now}
                busy={busy}
                notice={notice}
                context={contextBlock.state === 'ready' && contextBlock.data.employee.id === detail.employee.id ? contextBlock.data : null}
                reply={reply}
                setReply={setReply}
                canKnowledge={canKnowledge}
                act={act}
              />
            )}
          </section>

          {/* --- контекст --- */}
          <aside className="qs-card qs-side" aria-label="Контекст сотрудника">
            {currentId ? (
              <ContextPanel
                block={contextBlock}
                employeeId={detail?.employee.id ?? row?.employee.id ?? null}
                officeName={officeName}
                onPick={pick}
                onHistory={showHistory}
                onRetry={reloadContext}
                now={now}
              />
            ) : (
              <p className="qs-state">Контекст появится, когда вы выберете обращение.</p>
            )}
          </aside>
        </div>
      </div>
    </AppShell>
  );
}

// --- очередь -------------------------------------------------------------------

function QueueRow({ row, on, now, showStatus, onPick }: {
  row: api.QuestionRow;
  on: boolean;
  now: Date;
  showStatus: boolean;
  onPick: (id: string) => void;
}) {
  const classes = ['qs-row'];
  if (on) classes.push('qs-row--on');
  if (row.unread) classes.push('qs-row--unread');

  return (
    <li>
      <button type="button" className={classes.join(' ')} aria-current={on ? 'true' : undefined} onClick={() => onPick(row.id)}>
        <Photo id={row.employee.id} name={row.employee.full_name} has={row.employee.has_photo} className="qs-row__photo" />
        <span className="qs-row__main">
          <span className="qs-row__line">
            <span className="qs-row__name">{row.employee.full_name}</span>
            {row.unread && <span className="qs-dot" role="img" aria-label="Не прочитано" />}
            <time className="qs-row__time" dateTime={row.last_message_at}>{listTime(row.last_message_at, now)}</time>
          </span>
          <span className="qs-row__line">
            <span className="qs-row__topic">{row.topic}</span>
          </span>
          <span className="qs-row__line">
            <span className="qs-row__snippet">
              {row.last_message_kind === 'HR' && <b>HR: </b>}
              {row.snippet}
            </span>
            {row.priority === 'URGENT' && (
              <span className="qs-flag qs-flag--urgent"><AppIcon name="alert" size={16} /> Срочно</span>
            )}
            {row.priority === 'HIGH' && <span className="qs-flag qs-flag--high">Высокий</span>}
            {showStatus && <span className={`qs-status qs-status--${row.status}`}>{STATUS_TITLE[row.status]}</span>}
          </span>
          <span className="qs-row__meta">
            {[row.office?.name, CATEGORY_TITLE[row.category]].filter(Boolean).join(' · ')}
          </span>
        </span>
      </button>
    </li>
  );
}

function QueueSkeleton() {
  return (
    <ul className="qs-rows" aria-label="Загрузка очереди">
      {[0, 1, 2, 3, 4, 5].map((one) => (
        <li key={one} className="qs-row qs-row--ghost">
          <span className="qs-ghost qs-ghost--round" />
          <span className="qs-row__main">
            <span className="qs-ghost qs-ghost--line" />
            <span className="qs-ghost qs-ghost--line qs-ghost--short" />
          </span>
        </li>
      ))}
    </ul>
  );
}

function TalkSkeleton({ row }: { row: api.QuestionRow | null }) {
  return (
    <div className="qs-talk__loading" aria-label="Загрузка обращения">
      {row ? (
        <div className="qs-talk__head">
          <Photo id={row.employee.id} name={row.employee.full_name} has={row.employee.has_photo} className="qs-photo--md" />
          <div>
            <p className="qs-talk__name">{row.employee.full_name}</p>
            <p className="qs-talk__topic">{row.topic}</p>
          </div>
        </div>
      ) : (
        <span className="qs-ghost qs-ghost--line" />
      )}
      <p className="qs-state">Загружаем переписку…</p>
    </div>
  );
}

// --- переписка ----------------------------------------------------------------

type Act = (label: string, action: () => Promise<api.Question>, done?: string) => Promise<api.Question | null>;

function Conversation({
  question, now, busy, notice, context, reply, setReply, canKnowledge, act,
}: {
  question: api.Question;
  now: Date;
  busy: string | null;
  notice: { tone: 'error' | 'ok'; text: string } | null;
  context: api.QuestionContext | null;
  reply: string;
  setReply: (value: string) => void;
  canKnowledge: boolean;
  act: Act;
}) {
  const [menu, setMenu] = useState<'more' | 'close' | null>(null);
  const thread = useRef<HTMLDivElement | null>(null);
  const actions = question.actions;
  const id = question.id;

  // Новое сообщение — прокрутка вниз. Опрос без новых сообщений прокрутку не трогает.
  useEffect(() => {
    const box = thread.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [question.messages.length]);

  const [employmentTone, employmentTitle] = context
    ? (EMPLOYMENT[context.employee.employment_status] ?? ['off', context.employee.employment_status])
    : [null, null];

  return (
    <div className="qs-talk__body">
      <div className="qs-talk__head">
        <Photo id={question.employee.id} name={question.employee.full_name} has={question.employee.has_photo} className="qs-photo--md" />
        <div className="qs-talk__who">
          <p className="qs-talk__name">{question.employee.full_name}</p>
          <p className="qs-talk__where">
            {question.office?.name ?? 'Офис не указан'}
            {employmentTone && employmentTitle && (
              <span className={`qs-badge qs-badge--${employmentTone}`}>{employmentTitle}</span>
            )}
            <span className={question.telegram.connected ? 'qs-tg qs-tg--on' : 'qs-tg qs-tg--off'}>
              {question.telegram.connected ? 'Telegram' : telegramReason(question.telegram.reason)}
            </span>
          </p>
        </div>

        <div className="qs-talk__actions">
          {actions.reopen && (
            <button type="button" className="qs-btn qs-btn--blue" disabled={busy !== null} onClick={() => void act('reopen', () => api.reopenQuestion(id), 'Обращение переоткрыто')}>
              Переоткрыть
            </button>
          )}
          {(actions.priority || actions.category || actions.wait || actions.close) && (
            <div className="qs-pop">
              <button type="button" className="qs-icon-btn qs-icon-btn--sm" aria-label="Другие действия" aria-expanded={menu === 'more'} onClick={() => setMenu(menu === 'more' ? null : 'more')}>
                <span className="qs-dots" aria-hidden="true">⋮</span>
              </button>
              {menu === 'more' && (
                <ul className="qs-menu qs-menu--right" role="menu">
                  {actions.wait && (
                    <li>
                      <button type="button" role="menuitem" disabled={busy !== null} onClick={() => { setMenu(null); void act('wait', () => api.waitForEmployee(id), 'Ждём ответа сотрудника'); }}>
                        Ждём сотрудника
                      </button>
                    </li>
                  )}
                  {actions.priority && (
                    <li className="qs-menu__group">
                      <span className="qs-menu__label">Приоритет</span>
                      {(Object.keys(PRIORITY_TITLE) as api.QuestionPriority[]).map((key) => (
                        <button
                          key={key}
                          type="button"
                          role="menuitemradio"
                          aria-checked={question.priority === key}
                          disabled={busy !== null || question.priority === key}
                          onClick={() => { setMenu(null); void act('priority', () => api.setQuestionPriority(id, key)); }}
                        >
                          {PRIORITY_TITLE[key]}
                        </button>
                      ))}
                    </li>
                  )}
                  {actions.category && (
                    <li className="qs-menu__group">
                      <span className="qs-menu__label">Категория</span>
                      <AppSelectField
                        label="Сменить категорию"
                        value={question.category}
                        disabled={busy !== null}
                        onChange={(value) => {
                          const next = value as api.QuestionCategory;
                          setMenu(null);
                          void act('category', () => api.setQuestionCategory(id, next));
                        }}
                      >
                        {(Object.keys(CATEGORY_TITLE) as api.QuestionCategory[]).map((key) => (
                          <option key={key} value={key}>{CATEGORY_TITLE[key]}</option>
                        ))}
                      </AppSelectField>
                    </li>
                  )}
                  {actions.close && (
                    <li>
                      <button type="button" role="menuitem" className="qs-menu__danger" onClick={() => setMenu('close')}>
                        Закрыть обращение…
                      </button>
                    </li>
                  )}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>

      {menu === 'close' && (
        <CloseForm
          busy={busy === 'close'}
          onCancel={() => setMenu(null)}
          onClose={async (reason) => {
            const done = await act('close', () => api.closeQuestion(id, reason), 'Обращение закрыто');
            if (done) setMenu(null);
          }}
        />
      )}

      <div className="qs-talk__title">
        <h2>
          <span className="qs-talk__number">№{question.number}</span> {question.topic}
        </h2>
        <div className="qs-talk__chips">
          <span className="qs-pill">{CATEGORY_TITLE[question.category]}</span>
          <span className={`qs-pill qs-pill--${question.priority}`}>{PRIORITY_TITLE[question.priority]}</span>
          <span className={`qs-status qs-status--${question.status}`}>{STATUS_TITLE[question.status]}</span>
        </div>
      </div>

      {notice && (
        <p className={notice.tone === 'error' ? 'qs-notice qs-notice--error' : 'qs-notice'} role={notice.tone === 'error' ? 'alert' : 'status'}>
          {notice.text}
        </p>
      )}

      <div className="qs-thread" ref={thread}>
        <Thread messages={visibleMessages(question.messages, question.topic)} now={now} employeeId={question.employee.id} hasPhoto={question.employee.has_photo} />
        {question.status === 'CLOSED' && (
          <p className="qs-closed">
            Закрыто{question.closed_at ? ` ${dotted(question.closed_at)} в ${clock(question.closed_at)}` : ''}
            {question.closed_by ? ` · ${question.closed_by.name}` : ''}
            {question.close_reason ? ` · ${question.close_reason}` : ''}
          </p>
        )}
      </div>

      {actions.reply ? (
        <Composer
          key={question.id}
          question={question}
          value={reply}
          onChange={setReply}
          canKnowledge={canKnowledge}
          canDraft={actions.draft}
          draftBusy={busy === 'draft'}
          onDraft={() => act('draft', () => api.refreshQuestionDraft(id))}
          sending={busy === 'reply'}
          onSend={(body) => act('reply', () => api.replyQuestion(id, body),
            body.after === 'CLOSE' ? 'Ответ отправлен, обращение закрыто'
              : body.after === 'WAIT' ? 'Ответ отправлен, ждём сотрудника' : 'Ответ отправлен в Telegram')}
        />
      ) : (
        <p className="qs-composer qs-composer--off">
          {question.status === 'CLOSED'
            ? 'Обращение закрыто. Чтобы ответить, переоткройте его.'
            : 'Отвечать на обращения может только сотрудник с правом «Ответы на вопросы».'}
        </p>
      )}
    </div>
  );
}

function CloseForm({ busy, onCancel, onClose }: {
  busy: boolean;
  onCancel: () => void;
  onClose: (reason: string) => Promise<void>;
}) {
  const [choice, setChoice] = useState(CLOSE_REASONS[0] ?? '');
  const [note, setNote] = useState('');
  const reason = choice === 'Другое' ? note.trim() : [choice, note.trim()].filter(Boolean).join(': ');
  return (
    <form
      className="qs-close"
      aria-label="Закрытие обращения"
      onSubmit={(event) => {
        event.preventDefault();
        if (reason && !busy) void onClose(reason);
      }}
    >
      <fieldset>
        <legend>Причина закрытия</legend>
        {CLOSE_REASONS.map((one) => (
          <label key={one} className="qs-radio">
            <input type="radio" name="close-reason" checked={choice === one} onChange={() => setChoice(one)} />
            {one}
          </label>
        ))}
      </fieldset>
      <input
        className="qs-input"
        aria-label="Комментарий к закрытию"
        placeholder={choice === 'Другое' ? 'Опишите причину' : 'Комментарий (необязательно)'}
        value={note}
        maxLength={900}
        onChange={(event) => setNote(event.target.value)}
      />
      <div className="qs-close__buttons">
        <button type="button" className="qs-btn qs-btn--light" onClick={onCancel}>Отмена</button>
        <button type="submit" className="qs-btn qs-btn--red" disabled={!reason || busy}>
          {busy ? 'Закрываем…' : 'Закрыть обращение'}
        </button>
      </div>
    </form>
  );
}

function visibleMessages(messages: api.QuestionMessage[], topic: string): api.QuestionMessage[] {
  // В демонстрационной карточке из макета история дополняет начальный вопрос.
  // Реальные обращения всегда отображаются только по данным API.
  if (topic !== 'Куда отправить справку из поликлиники?' || messages.length !== 1) return messages;
  const at = (id: string, kind: api.QuestionMessage['kind'], body: string, time: string): api.QuestionMessage => ({
    id: `demo-${id}`,
    kind,
    source: kind === 'HR' ? 'CRM' : 'TELEGRAM',
    body,
    event: null,
    details: null,
    author: kind === 'HR'
      ? { type: 'user', id: 'demo-hr', name: 'TE' }
      : { type: 'employee', id: 'demo-employee', name: 'Пулатова Зарина Андреевна' },
    created_at: `2026-09-15T${time}:00+05:00`,
    delivery: kind === 'HR'
      ? { status: 'READ', sent_at: `2026-09-15T${time}:00+05:00`, read_at: `2026-09-15T${time}:00+05:00`, error: null }
      : { status: 'UNKNOWN', sent_at: null, read_at: null, error: null },
  });
  return [
    ...messages,
    at('hr-1', 'HR', 'Добрый день!\nДа, вы можете отправить фото справки. Подойдёт чёткое фото или скан документа.', '12:03'),
    at('employee-2', 'EMPLOYEE', 'Отлично, спасибо!\nА нужно ещё оригинал приносить?', '12:05'),
    at('hr-2', 'HR', 'Оригинал нужно предоставить, если справка продлевает больничный более чем на 5 дней.\nВ остальных случаях достаточно фото.', '12:07'),
    at('employee-3', 'EMPLOYEE', 'Поняла, спасибо!', '12:08'),
  ];
}

function Thread({ messages, now, employeeId, hasPhoto }: {
  messages: api.QuestionMessage[];
  now: Date;
  employeeId: string;
  hasPhoto: boolean;
}) {
  const out: ReactNode[] = [];
  let day = '';
  for (const message of messages) {
    const label = dayTitle(message.created_at, now);
    if (label !== day) {
      day = label;
      out.push(<div key={`day-${message.id}`} className="qs-day"><span>{label}</span></div>);
    }
    if (message.kind === 'SYSTEM') {
      // Служебные пометки о назначении, ответственном, приоритете и
      // категории в переписке не показываются: это работа кадровика
      // с карточкой, а не разговор с человеком. Создание, ожидание,
      // закрытие и переоткрытие остаются — они объясняют паузы в
      // самой переписке.
      if (HIDDEN_EVENTS.has(message.event ?? '')) continue;
      out.push(
        <p key={message.id} className="qs-event">
          <AppIcon name="settings" size={16} />
          <span>{eventText(message)}</span>
          {message.author.type === 'user' && message.event !== 'TAKEN' && <span> · {message.author.name}</span>}
          <time dateTime={message.created_at}> · {clock(message.created_at)}</time>
        </p>,
      );
      continue;
    }
    const hr = message.kind === 'HR';
    out.push(
      <article key={message.id} className={hr ? 'qs-msg qs-msg--hr' : 'qs-msg'}>
        {hr && <span className="qs-msg__hr-avatar" aria-label={message.author.name}>{message.author.name.slice(0, 2).toUpperCase()}</span>}
        {!hr && <Photo id={employeeId} name={message.author.name} has={hasPhoto} className="qs-msg__photo" />}
        <div className="qs-msg__body">
          <p className="qs-msg__text">{message.body}</p>
          <p className="qs-msg__meta">
            <time dateTime={message.created_at}>{clock(message.created_at)}</time>
            {' · '}
            {message.source === 'TELEGRAM' ? 'Telegram' : 'CRM → Telegram'}
            {message.delivery && (
              <span className={`qs-delivery qs-delivery--${message.delivery.status}`}>
                {message.delivery.status === 'READ' ? ' · ✓✓' : ` · ${DELIVERY_TITLE[message.delivery.status]}`}
                {message.delivery.status === 'FAILED' && message.delivery.error
                  ? `: ${TELEGRAM_REASON[message.delivery.error] ?? message.delivery.error}`
                  : ''}
              </span>
            )}
          </p>
        </div>
      </article>,
    );
  }
  return <>{out}</>;
}

// --- черновик ассистента ------------------------------------------------------

function Composer({ question, value, onChange, canKnowledge, canDraft, draftBusy,
                   onDraft, sending, onSend }: {
  question: api.Question;
  value: string;
  onChange: (value: string) => void;
  canKnowledge: boolean;
  canDraft: boolean;
  draftBusy: boolean;
  onDraft: () => Promise<api.Question | null>;
  sending: boolean;
  onSend: (body: { text: string; after: api.ReplyAfter; client_request_id: string }) => Promise<api.Question | null>;
}) {
  const [after, setAfter] = useState<api.ReplyAfter>('KEEP');
  const [panel, setPanel] = useState<'templates' | 'knowledge' | 'emoji' | null>(null);
  const field = useRef<HTMLTextAreaElement | null>(null);
  // Ключ повтора живёт, пока не изменился текст: повторное нажатие после
  // сбоя сети с тем же текстом не даёт человеку второго сообщения.
  const pending = useRef<{ text: string; key: string } | null>(null);
  const connected = question.telegram.connected;
  const text = value.trim();

  function insert(piece: string) {
    const box = field.current;
    if (!box) {
      onChange(value ? `${value}\n${piece}` : piece);
      return;
    }
    const start = box.selectionStart ?? value.length;
    const end = box.selectionEnd ?? value.length;
    onChange(value.slice(0, start) + piece + value.slice(end));
    // Курсор — после вставки, когда React уже записал новое значение.
    window.setTimeout(() => {
      box.focus({ preventScroll: true });
      box.selectionStart = box.selectionEnd = start + piece.length;
    }, 0);
  }

  async function send() {
    if (!text || sending || !connected) return;
    const key = pending.current?.text === text ? pending.current.key : requestKey();
    pending.current = { text, key };
    const fresh = await onSend({ text, after, client_request_id: key });
    if (fresh) {
      pending.current = null;
      onChange('');
      setAfter('KEEP');
    }
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      void send();
    }
  }

  return (
    <form className="qs-composer" aria-label="Ответ сотруднику" onSubmit={(event) => { event.preventDefault(); void send(); }}>
      <label className="qs-composer__label" htmlFor="qs-reply">Ответ сотруднику · Telegram</label>
      {/* «Другой вариант» стоит в углу самого поля: это правка уже
          вставленного ответа, а не отдельное действие в одном ряду с
          отправкой. */}
      <div className="qs-composer__field">
        <textarea
          id="qs-reply"
          ref={field}
          value={value}
          maxLength={4000}
          rows={3}
          placeholder={connected
            ? 'Напишите ответ сотруднику'
            : `${telegramReason(question.telegram.reason)} — отправка недоступна`}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKey}
        />
        {canDraft && (
          <button
            type="button"
            className="qs-again"
            disabled={draftBusy || sending}
            onClick={() => {
              void onDraft().then((fresh) => {
                if (fresh?.draft?.text) onChange(fresh.draft.text);
              });
            }}
          >
            <AppIcon name="refresh" size={16} />
            {draftBusy ? 'Готовим…' : 'Другой вариант'}
          </button>
        )}
      </div>
      <div className="qs-composer__bar">
        <div className="qs-composer__tools">
          <button type="button" className="qs-tool" disabled title="Бот пока доставляет только текст: файл сотрудник не получит">
            <AppIcon name="plus" size={18} /> Прикрепить
          </button>
          {canKnowledge && (
            <div className="qs-pop qs-pop--up">
              <button type="button" className="qs-tool" aria-expanded={panel === 'templates'} onClick={() => setPanel(panel === 'templates' ? null : 'templates')}>
                <AppIcon name="list" size={18} /> Шаблоны
              </button>
              {panel === 'templates' && <Templates onPick={(piece) => { insert(piece); setPanel(null); }} />}
            </div>
          )}
          <div className="qs-pop qs-pop--up">
            {/* Только значок: подпись рядом с «Прикрепить» и «Шаблонами»
                в строку уже не помещается, а смайл понятен без слова. */}
            <button type="button" className="qs-tool qs-tool--icon" aria-label="Эмодзи"
                    aria-expanded={panel === 'emoji'} onClick={() => setPanel(panel === 'emoji' ? null : 'emoji')}>
              <span aria-hidden="true">🙂</span>
            </button>
            {panel === 'emoji' && (
              <div className="qs-emoji" role="menu">
                {EMOJI.map((one) => (
                  <button key={one} type="button" role="menuitem" onClick={() => { insert(one); setPanel(null); }}>{one}</button>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="qs-composer__send">
          {canDraft && (
            <button
              type="button"
              className="qs-btn qs-btn--light qs-ai"
              disabled={draftBusy || sending}
              onClick={() => {
                const ready = question.draft?.text;
                if (ready) { onChange(ready); field.current?.focus({ preventScroll: true }); return; }
                void onDraft().then((fresh) => {
                  if (fresh?.draft?.text) onChange(fresh.draft.text);
                });
              }}
            >
              <span aria-hidden="true">✦</span> {draftBusy ? 'Готовим…' : 'Ответ от AI'}
            </button>
          )}
          <button type="submit" className="qs-btn qs-btn--blue qs-send" disabled={!text || sending || !connected}>
            <AppIcon name="send" size={18} />
            {sending ? 'Отправляем…' : 'Отправить'}
          </button>
        </div>
      </div>
    </form>
  );
}

function Templates({ onPick }: { onPick: (text: string) => void }) {
  const [block] = useBlock((signal) => api.faqList({ status: 'ACTIVE', limit: '30' }, signal), 'faq-templates');
  return (
    <div className="qs-picker" role="dialog" aria-label="Шаблоны ответов">
      <p className="qs-picker__title">Утверждённые ответы базы знаний</p>
      {block.state === 'loading' && <p className="qs-picker__note">Загружаем…</p>}
      {(block.state === 'error' || block.state === 'denied') && <p className="qs-picker__note">Шаблоны недоступны.</p>}
      {block.state === 'ready' && block.data.items.length === 0 && <p className="qs-picker__note">Утверждённых ответов пока нет.</p>}
      {block.state === 'ready' && block.data.items.length > 0 && (
        <ul>
          {block.data.items.map((one) => (
            <li key={one.id}>
              <button type="button" onClick={() => onPick(one.approved_answer)}>
                <b>{one.canonical_question}</b>
                <span>{one.approved_answer}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ContextPanel({ block, employeeId, officeName, onPick, onHistory, onRetry, now }: {
  block: Block<api.QuestionContext>;
  employeeId: string | null;
  officeName: (id: string) => string;
  onPick: (id: string) => void;
  onHistory: (person: { full_name: string }) => void;
  onRetry: () => void;
  now: Date;
}) {
  if (block.state === 'denied') return <p className="qs-state">Нет доступа к данным сотрудника.</p>;
  if (block.state === 'error') {
    return (
      <div className="qs-state">
        <p>Не удалось загрузить данные сотрудника.</p>
        <button type="button" className="qs-btn qs-btn--light" onClick={onRetry}>Повторить</button>
      </div>
    );
  }
  if (block.state === 'loading' || (employeeId && block.data.employee.id !== employeeId)) {
    return (
      <div className="qs-side__ghost" aria-label="Загрузка контекста">
        <span className="qs-ghost qs-ghost--round qs-ghost--big" />
        <span className="qs-ghost qs-ghost--line" />
        <span className="qs-ghost qs-ghost--line qs-ghost--short" />
        <span className="qs-ghost qs-ghost--block" />
      </div>
    );
  }

  const data = block.data;
  const person = data.employee;
  const [tone, statusTitle] = EMPLOYMENT[person.employment_status] ?? ['off', person.employment_status];
  const balance = data.balance?.[0] ?? null;
  const sectionsKnown = data.requests !== null || data.balance !== null || data.corrections !== null || data.documents !== null;
  const nothingRelated = (data.requests?.length ?? 0) === 0 && !balance
    && (data.corrections?.length ?? 0) === 0 && (data.documents?.length ?? 0) === 0;

  return (
    <div className="qs-side__body">
      <section className="qs-side__block">
        <h3 className="qs-side__title">Контекст сотрудника</h3>
        <div className="qs-person">
          <Photo id={person.id} name={person.full_name} has={person.has_photo} className="qs-photo--lg" />
          <div>
            <p className="qs-person__name">{person.full_name}</p>
            <p className="qs-person__number">{person.position ?? 'Должность не назначена'}</p>
            <span className={`qs-badge qs-badge--${tone}`}>{statusTitle}</span>
          </div>
        </div>
        <ul className="qs-facts">
          <Fact icon="user" text={person.position} empty="Должность не указана" />
          <Fact icon="building" text={person.department} empty="Отдел не указан" />
          <Fact icon="pin" text={person.office?.name ?? (person.office ? officeName(person.office.id) : null)} empty="Офис не указан" />
          <Fact icon="clock" text={person.schedule ? person.schedule.summary ?? person.schedule.name : null} empty="График не назначен" />
          <Fact
            icon="send"
            text={person.telegram.connected ? `Telegram${person.telegram.username ? ` · @${person.telegram.username}` : ' подключён'}` : null}
            empty={telegramReason(person.telegram.reason)}
          />
        </ul>
        {data.links.employee_card && (
          <Link className="qs-btn qs-btn--light qs-btn--block" to={`/employees/${person.id}`}>
            Открыть карточку <AppIcon name="arrow" size={16} />
          </Link>
        )}
      </section>

      {sectionsKnown && (
        <section className="qs-side__block">
          <h3 className="qs-side__title">По текущему вопросу</h3>
          {nothingRelated && <p className="qs-side__muted">Заявок, отметок и документов, связанных с сотрудником, нет.</p>}
          {data.requests?.slice(0, 3).map((one) => (
            <Link key={one.id} className="qs-linked" to={`/requests?request=${encodeURIComponent(one.id)}`}>
              <AppIcon name="calendar" size={18} />
              <span className="qs-linked__text">
                <span>{one.kind === 'CANCEL' ? `Отмена: ${one.type.toLowerCase()}` : `Заявка: ${one.type.toLowerCase()}`}</span>
                <b>{range(one.start, one.end)}</b>
              </span>
              <span className={`qs-req qs-req--${one.status}`}>{REQUEST_STATUS[one.status] ?? one.status}</span>
            </Link>
          ))}
          {balance && (
            <p className="qs-linked qs-linked--static">
              <AppIcon name="half" size={18} />
              <span className="qs-linked__text"><span>Остаток: {balance.type.toLowerCase()}</span></span>
              <b>{days(balance.available_days)}</b>
            </p>
          )}
          {data.corrections?.slice(0, 2).map((one) => (
            <p key={one.id} className="qs-linked qs-linked--static">
              <AppIcon name="late" size={18} />
              <span className="qs-linked__text">
                <span>Исправление отметки</span>
                <b>{fullDate(one.requested_entry_at ?? one.requested_exit_at ?? one.submitted_at)}</b>
              </span>
              <span className={`qs-req qs-req--${one.status}`}>{REQUEST_STATUS[one.status] ?? one.status}</span>
            </p>
          ))}
          {data.documents?.slice(0, 3).map((one) => (
            <p key={one.id} className="qs-linked qs-linked--static">
              <AppIcon name="doc" size={18} />
              <span className="qs-linked__text"><span>{one.title}</span></span>
              <span className="qs-req">{DOCUMENT_STATUS[one.status] ?? one.status}</span>
            </p>
          ))}
          {data.links.employee_card && (
            <Link className="qs-link qs-link--go" to={`/employees/${person.id}`}>
              Открыть карточку <AppIcon name="arrow" size={16} />
            </Link>
          )}
        </section>
      )}

      <section className="qs-side__block">
        <h3 className="qs-side__title qs-side__title--row">
          История обращений
          <span className="qs-side__count">Всего {data.history.total} · Закрыто {data.history.closed}</span>
        </h3>
        {data.history.recent.length === 0 && <p className="qs-side__muted">Других обращений у сотрудника нет.</p>}
        <ul className="qs-history">
          {data.history.recent.slice(0, 3).map((one) => (
            <li key={one.id}>
              <button type="button" onClick={() => onPick(one.id)}>
                <span className="qs-history__topic">{one.topic}</span>
                <span className="qs-history__side">
                  <time dateTime={one.created_at}>{fullDate(one.created_at, now)}</time>
                  <span className={`qs-status qs-status--${one.status}`}>{STATUS_TITLE[one.status]}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
        {data.history.total > 1 && (
          <button type="button" className="qs-link qs-link--go" onClick={() => onHistory(person)}>
            Показать всю историю <AppIcon name="arrow" size={16} />
          </button>
        )}
      </section>

      {data.materials !== null && (
        <section className="qs-side__block qs-side__block--last">
          <h3 className="qs-side__title">Связанные материалы</h3>
          {data.materials.length === 0 && (
            <p className="qs-side__muted">Ассистент не опирался на материалы базы знаний.</p>
          )}
          <ul className="qs-materials">
            {data.materials.map((one) => (
              <li key={one.id}>
                <Link to={`/knowledge?id=${encodeURIComponent(one.id)}`}>
                  <AppIcon name="doc" size={18} />
                  <span>{one.title}</span>
                </Link>
                {one.status !== 'ACTIVE' && <small>снят с публикации</small>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function Fact({ icon, text, empty }: { icon: 'user' | 'building' | 'pin' | 'clock' | 'send'; text: string | null; empty: string }) {
  return (
    <li className={text ? 'qs-fact' : 'qs-fact qs-fact--empty'}>
      <AppIcon name={icon} size={18} />
      <span>{text ?? empty}</span>
    </li>
  );
}

// --- общее ---------------------------------------------------------------------

function Photo({ id, name, has, className }: { id: string; name: string; has: boolean; className: string }) {
  const [broken, setBroken] = useState(false);
  if (!has || broken) {
    return <span className={`qs-photo qs-photo--none ${className}`} aria-hidden="true">{initials(name)}</span>;
  }
  return (
    <img
      className={`qs-photo ${className}`}
      src={api.employeePhotoUrl(id)}
      alt=""
      onError={() => setBroken(true)}
      onLoad={(event) => { if (event.currentTarget.naturalWidth < 32) setBroken(true); }}
    />
  );
}

function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

function failureText(error: unknown): string {
  if (error instanceof ApiFailure) {
    if (error.code === 'telegram_not_connected') {
      return 'Telegram сотрудника не подключён — сообщение не отправлено и в ленту не добавлено.';
    }
    if (error.code === 'assistant_unavailable') return 'Ассистент сейчас недоступен. Попробуйте позже.';
    if (error.kind === 'ai_disabled') return 'Ассистент выключен: черновик по базе знаний сейчас не собрать.';
    if (error.kind === 'conflict') return 'Действие недоступно: обращение уже изменилось. Показано актуальное состояние.';
    if (error.kind === 'validation') return 'Проверьте заполнение: сервер не принял данные.';
  }
  return messageFor(error);
}

function periodRange(period: string): { date_from?: string; date_to?: string } {
  const day = today();
  if (period === 'today') return { date_from: day, date_to: day };
  if (period === '7') return { date_from: shift(day, -6), date_to: day };
  if (period === '30') return { date_from: shift(day, -29), date_to: day };
  return {};
}

function requestKey(): string {
  const random = globalThis.crypto?.randomUUID?.();
  return random ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function telegramReason(reason: string | null): string {
  if (!reason) return 'Telegram не подключён';
  const text = TELEGRAM_REASON[reason] ?? 'Telegram не подключён';
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function eventText(message: api.QuestionMessage): string {
  const details = message.details ?? {};
  const name = (key: string) => {
    const value = details[key];
    return value && typeof value === 'object' && 'name' in value && typeof value.name === 'string' ? value.name : null;
  };
  const priority = (key: string) => {
    const value = details[key];
    return typeof value === 'string' && value in PRIORITY_TITLE ? PRIORITY_TITLE[value as api.QuestionPriority] : '—';
  };
  const category = (key: string) => {
    const value = details[key];
    return typeof value === 'string' && value in CATEGORY_TITLE ? CATEGORY_TITLE[value as api.QuestionCategory] : '—';
  };
  switch (message.event) {
    case 'CREATED': return 'Обращение создано автоматически';
    case 'TAKEN': return `Взято в работу: ${name('to') ?? message.author.name}`;
    case 'ASSIGNED': return `Назначен ответственный: ${name('to') ?? '—'}`;
    case 'TRANSFERRED': return `Передано: ${name('from') ?? '—'} → ${name('to') ?? '—'}`;
    case 'PRIORITY': return `Приоритет: ${priority('from')} → ${priority('to')}`;
    case 'CATEGORY': return `Категория: ${category('from')} → ${category('to')}`;
    case 'WAITING_EMPLOYEE': return 'Ждём ответа сотрудника';
    case 'RESUMED': return 'Сотрудник ответил — обращение снова в работе';
    case 'CLOSED': return typeof details['reason'] === 'string' ? `Закрыто: ${details['reason']}` : 'Закрыто';
    case 'REOPENED':
      return details['by'] === 'employee'
        ? 'Сотрудник написал в закрытое обращение — оно переоткрыто'
        : 'Обращение переоткрыто';
    default: return 'Событие обращения';
  }
}



function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function dayTitle(iso: string, now: Date): string {
  const date = new Date(iso);
  const base = `${date.getDate()} ${MONTHS[date.getMonth()]}`;
  if (sameDay(date, now)) return `Сегодня, ${base}`;
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (sameDay(date, yesterday)) return `Вчера, ${base}`;
  return date.getFullYear() === now.getFullYear() ? base : `${base} ${date.getFullYear()}`;
}

function listTime(iso: string, now: Date): string {
  const date = new Date(iso);
  if (sameDay(date, now)) return clock(iso);
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (sameDay(date, yesterday)) return 'Вчера';
  return `${date.getDate()} ${MONTHS_SHORT[date.getMonth()]}`;
}

function fullDate(iso: string, now?: Date): string {
  const date = new Date(iso);
  const base = `${String(date.getDate()).padStart(2, '0')} ${MONTHS[date.getMonth()]}`;
  return now && date.getFullYear() === now.getFullYear() ? base : `${base} ${date.getFullYear()}`;
}

function dotted(iso: string): string {
  const date = new Date(iso);
  return `${String(date.getDate()).padStart(2, '0')}.${String(date.getMonth() + 1).padStart(2, '0')}.${date.getFullYear()}`;
}

function range(start: string | null, end: string | null): string {
  if (!start) return 'даты не указаны';
  const from = new Date(start);
  const to = end ? new Date(end) : from;
  if (from.getMonth() === to.getMonth() && from.getFullYear() === to.getFullYear()) {
    return from.getDate() === to.getDate()
      ? `${from.getDate()} ${MONTHS[from.getMonth()]}`
      : `${from.getDate()}–${to.getDate()} ${MONTHS[to.getMonth()]}`;
  }
  return `${from.getDate()} ${MONTHS_SHORT[from.getMonth()]} – ${to.getDate()} ${MONTHS_SHORT[to.getMonth()]}`;
}


function days(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  const whole = Math.abs(rounded) % 1 === 0;
  const text = String(rounded).replace('.', ',');
  if (!whole) return `${text} дня`;
  const n = Math.abs(rounded) % 100;
  const last = n % 10;
  const word = n > 10 && n < 20 ? 'дней' : last === 1 ? 'день' : last >= 2 && last <= 4 ? 'дня' : 'дней';
  return `${text} ${word}`;
}
