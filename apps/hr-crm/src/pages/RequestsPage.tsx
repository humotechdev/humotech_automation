/**
 * Заявки: отпуска, больничные и исправления отметок в одной очереди.
 *
 * Список и подробности стоят рядом, а не одно поверх другого: кадровик
 * разбирает очередь, и терять её из виду на каждой заявке — значит
 * заставлять его каждый раз искать место, где он остановился.
 *
 * Очередь приходит одним адресом `/requests`. Склеивать две страницы —
 * отсутствий и исправлений — на клиенте нельзя: получилась бы не очередь,
 * а произвольная смесь двух её половин, в которой часть свежих записей
 * не показана вовсе.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell, initials } from '../components/AppShell';
import { Icon } from '../components/nav-icons';
import { messageFor } from '../api/errors';
import { formatTime, longDate, useBlock, type Block } from '../features/dashboard/data';

const TABS = [
  { key: 'all', title: 'Все', params: {} as api.QueueQuery },
  { key: 'sick', title: 'Больничные', params: { kind: 'absence', type: 'SICK_LEAVE' } },
  { key: 'leave', title: 'Отпуска', params: { kind: 'absence', type: 'ANNUAL_LEAVE,UNPAID_LEAVE' } },
  { key: 'fixes', title: 'Исправления отметок', params: { kind: 'correction' } },
] as const;

/** Состояния рассмотрения. Показываются словами, а не цветом. */
const STATUS_TITLE: Record<string, string> = {
  SUBMITTED: 'На рассмотрении',
  PENDING: 'На рассмотрении',
  IN_REVIEW: 'На рассмотрении',
  APPROVED: 'Подтверждена',
  REJECTED: 'Отклонена',
  CANCELLED: 'Отменена',
  DRAFT: 'Черновик',
};

/** Состояния документа — отдельная величина, не смешивается со статусом. */
const DOCUMENT_TITLE: Record<string, string> = {
  PENDING: 'Справка на проверке',
  VERIFIED: 'Справка проверена',
  REJECTED: 'Справка отклонена',
};

const OPEN_STATUSES = 'SUBMITTED,IN_REVIEW,PENDING';

export function RequestsPage() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.find((t) => t.key === params.get('tab')) ?? TABS[0];
  const search = params.get('search') ?? '';
  const region = params.get('region_id') ?? '';
  const office = params.get('office_id') ?? '';
  const status = params.get('status') ?? '';
  const from = params.get('date_from') ?? '';
  const to = params.get('date_to') ?? '';
  const cursor = params.get('cursor') ?? '';
  const opened = params.get('request') ?? '';

  const [draft, setDraft] = useState(search);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [attempt, setAttempt] = useState(0);
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

  const filters: api.QueueQuery = useMemo(
    () => ({
      ...tab.params,
      ...(search ? { search } : {}),
      ...(region ? { region_id: region } : {}),
      ...(office ? { office_id: office } : {}),
      ...(status === 'open' ? { status: OPEN_STATUSES } : status ? { status } : {}),
      ...(from ? { date_from: from } : {}),
      ...(to ? { date_to: to } : {}),
    }),
    [tab, search, region, office, status, from, to],
  );
  const key = `${tab.key}|${search}|${region}|${office}|${status}|${from}|${to}|${cursor}|${attempt}`;

  const [list, reload] = useBlock(
    (signal) =>
      api.queue({ ...filters, limit: '10', ...(cursor ? { cursor } : {}) }, signal).then(
        (body) => {
          setUpdated(new Date());
          return body;
        },
      ),
    key,
  );

  // Счётчики вкладок: по одному запросу на вкладку, без строк.
  const [counts] = useBlock(
    (signal) =>
      Promise.all(
        TABS.map((item) =>
          api
            .queue({ ...filters, ...item.params, limit: '1' }, signal)
            .then((body) => [item.key, body.items.length + (body.has_more ? 1 : 0)] as const)
            .catch(() => [item.key, 0] as const),
        ),
      ).then((pairs) => Object.fromEntries(pairs)),
    `counts|${key}`,
  );

  const [directory] = useBlock(
    (signal) =>
      Promise.all([api.regions(signal), api.offices(signal)]).then(([r, o]) => ({
        regions: r.items,
        offices: o.items,
      })),
    'directory',
  );

  const offices = useMemo(() => {
    if (directory.state !== 'ready') return [];
    const all = directory.data.offices.filter((o) => o.status === 'ACTIVE');
    return region ? all.filter((o) => o.region_id === region) : all;
  }, [directory, region]);

  const items = list.state === 'ready' ? list.data.items : [];
  const current = items.find((item) => item.id === opened) ?? null;
  const dirty = Boolean(search || region || office || status || from || to);

  return (
    <AppShell breadcrumb="Заявки" section="requests">
      <header className="head head--tight">
        <div>
          <h1 className="head__title">Заявки</h1>
          <p className="head__sub">Отпуска, больничные и исправления отметок</p>
        </div>
        <div className="head__filters">
          <button type="button" className="pick pick--icon" aria-label="Обновить"
                  onClick={() => setAttempt((n) => n + 1)}>
            <Icon name="refresh" size={16} />
          </button>
          <p className="head__updated">
            {updated ? `Обновлено в ${formatTime(updated)}` : 'Загружаем…'}
          </p>
        </div>
      </header>

      <div className="queue-grid">
        <section className="sheet">
          <div className="tabs" role="tablist">
            {TABS.map((item) => (
              <button
                key={item.key}
                type="button"
                role="tab"
                aria-selected={item.key === tab.key}
                className={item.key === tab.key ? 'tab tab--on' : 'tab'}
                onClick={() => patch({ tab: item.key === 'all' ? null : item.key, request: null })}
              >
                {item.title}
                {counts.state === 'ready' && (
                  <span className="tab__count">{counts.data[item.key] ?? 0}</span>
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
                placeholder="Поиск сотрудника или заявки"
                aria-label="Поиск сотрудника или заявки"
                onChange={(event) => setDraft(event.target.value)}
              />
            </label>
            <Picker label="Регион" value={region} empty="Все регионы"
                    options={directory.state === 'ready' ? directory.data.regions : []}
                    onChange={(value) => patch({ region_id: value || null, office_id: null })} />
            <Picker label="Офис" value={office} empty="Все офисы" options={offices}
                    onChange={(value) => patch({ office_id: value || null })} />
            <label className="pick">
              <span className="visually-hidden">Статус</span>
              <select value={status} onChange={(event) => patch({ status: event.target.value || null })}>
                <option value="">Любой статус</option>
                <option value="open">Требуют действия</option>
                <option value="APPROVED">Подтверждённые</option>
                <option value="REJECTED">Отклонённые</option>
                <option value="CANCELLED">Отменённые</option>
              </select>
            </label>
            <label className="pick pick--date">
              <Icon name="calendar" size={16} />
              <span className="visually-hidden">Отсутствие с</span>
              <input type="date" value={from} aria-label="Отсутствие с"
                     onChange={(event) => patch({ date_from: event.target.value || null })} />
            </label>
            <label className="pick pick--date">
              <span className="visually-hidden">Отсутствие по</span>
              <input type="date" value={to} aria-label="Отсутствие по"
                     onChange={(event) => patch({ date_to: event.target.value || null })} />
            </label>
            {dirty && (
              <button type="button" className="btn" onClick={() =>
                patch({ search: null, region_id: null, office_id: null, status: null,
                        date_from: null, date_to: null })}>
                Сбросить
              </button>
            )}
          </div>
          <p className="toolbar__note">Период фильтрует даты самого отсутствия, а не дату подачи.</p>

          <Rows block={list}>
            {(data) =>
              data.items.length === 0 ? (
                <p className="empty">
                  {dirty ? 'По этим условиям заявок нет.' : 'Очередь пуста.'}
                </p>
              ) : (
                <div className="scroller">
                  <table className="people">
                    <thead>
                      <tr>
                        <th>Сотрудник</th>
                        <th>Тип</th>
                        <th>Даты</th>
                        <th>Документ</th>
                        <th>Статус</th>
                        <th aria-label="Открыть" />
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((item) => (
                        <Row key={item.id} item={item} selected={item.id === opened}
                             onOpen={() => patch({ request: item.id }, true)} />
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            }
          </Rows>

          <div className="pager">
            <p className="pager__note">
              {list.state === 'ready' ? `Показано ${list.data.items.length}` : ''}
            </p>
            <div className="pager__tools">
              <button type="button" className="btn" disabled={!cursor}
                      onClick={() => patch({ cursor: null })}>
                Назад
              </button>
              <button
                type="button"
                className="btn btn--dark"
                disabled={list.state !== 'ready' || !list.data.has_more}
                onClick={() => list.state === 'ready' && patch({ cursor: list.data.next_cursor }, true)}
              >
                Далее
              </button>
            </div>
          </div>
        </section>

        {current && (
          <Details
            item={current}
            onClose={() => patch({ request: null }, true)}
            onDone={reload}
          />
        )}
      </div>
    </AppShell>
  );
}

// --- строка ----------------------------------------------------------------

function Row({ item, selected, onOpen }: {
  item: api.QueueItem; selected: boolean; onOpen: () => void;
}) {
  const person = item.absence?.employee ?? item.correction?.employee;
  const status = item.absence?.status ?? item.correction?.status ?? '';
  const document = item.absence?.documents?.[0];

  return (
    <tr className={selected ? 'is-picked' : undefined} tabIndex={0} onClick={onOpen}
        onKeyDown={(event) => event.key === 'Enter' && onOpen()}>
      <td>
        <span className="who">
          <span className="avatar">{initials(person?.full_name)}</span>
          <span className="who__text">
            <span className="who__name">{person?.full_name ?? '—'}</span>
            <span className="who__id">{person?.employee_number ?? '—'}</span>
          </span>
        </span>
      </td>
      <td>{kindTitle(item)}</td>
      <td>
        <span className="two">
          <span className="two__first">{dates(item)}</span>
        </span>
      </td>
      <td>
        {item.kind === 'correction' ? (
          <span className="muted">—</span>
        ) : document ? (
          <span className="pill">
            <Icon name="doc" size={14} />
            {DOCUMENT_TITLE[document.verification_status] ?? 'Загружена'}
          </span>
        ) : item.absence?.requires_document ? (
          <span className="muted">Нет справки</span>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td>
        <span className="pill">{STATUS_TITLE[status] ?? status}</span>
      </td>
      <td className="people__go"><Icon name="arrow" size={16} /></td>
    </tr>
  );
}

// --- подробности -----------------------------------------------------------

function Details({ item, onClose, onDone }: {
  item: api.QueueItem; onClose: () => void; onDone: () => void;
}) {
  const [comment, setComment] = useState('');
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const person = item.absence?.employee ?? item.correction?.employee;
  const status = item.absence?.status ?? item.correction?.status ?? '';
  const open = ['SUBMITTED', 'IN_REVIEW', 'PENDING'].includes(status);
  const document = item.absence?.documents?.[0];

  async function decide(decision: 'approve' | 'reject') {
    if (sending) return;
    setSending(true);
    setFailed(null);
    try {
      if (item.kind === 'absence') await api.decideAbsence(item.id, decision, comment);
      else await api.decideCorrection(item.id, decision, comment);
      setDone(decision === 'approve' ? 'Заявка подтверждена' : 'Заявка отклонена');
      onDone();
    } catch (error) {
      // Комментарий остаётся в поле: набирать его заново из-за сбоя сети —
      // худшее, что можно предложить человеку.
      setFailed(messageFor(error));
    } finally {
      setSending(false);
    }
  }

  return (
    <aside className="panel side-panel" aria-label="Подробности заявки">
      <header className="side-panel__head">
        <span className="side-panel__title">
          <Icon name="doc" size={18} />
          {kindTitle(item)}
        </span>
        <span className="pill">{STATUS_TITLE[status] ?? status}</span>
        <button type="button" className="tool" aria-label="Закрыть" onClick={onClose}>✕</button>
      </header>

      <div className="side-panel__body">
        <div className="who">
          <span className="avatar">{initials(person?.full_name)}</span>
          <span className="who__text">
            <span className="who__name">{person?.full_name ?? '—'}</span>
            <span className="who__id">{person?.employee_number ?? '—'}</span>
          </span>
        </div>

        <dl className="facts">
          <div className="facts__row">
            <dt>Подана</dt>
            <dd>{item.absence?.submitted_at ? longDate(item.absence.submitted_at.slice(0, 10)) : '—'}</dd>
          </div>
          <div className="facts__row">
            <dt>{item.kind === 'absence' ? 'Период отсутствия' : 'Дата отметки'}</dt>
            <dd>{dates(item)}</dd>
          </div>
        </dl>

        {item.absence?.comment && (
          <>
            <p className="side-panel__label">Комментарий сотрудника</p>
            <p className="side-panel__text">{item.absence.comment}</p>
          </>
        )}
        {item.correction?.reason && (
          <>
            <p className="side-panel__label">Причина</p>
            <p className="side-panel__text">{item.correction.reason}</p>
          </>
        )}

        {item.kind === 'absence' && (
          <>
            <p className="side-panel__label">Документ</p>
            {document ? (
              <div className="doc">
                <p className="doc__name">{document.file.name}</p>
                <p className="doc__meta">
                  {document.file.mime_type} · {size(document.file.size_bytes)} ·
                  {' '}загружен {longDate(document.file.uploaded_at.slice(0, 10))}
                </p>
                <p className="doc__state">
                  {DOCUMENT_TITLE[document.verification_status] ?? document.verification_status}
                </p>
              </div>
            ) : item.absence?.requires_document ? (
              <p className="empty">Ожидаем справку — документ ещё не предоставлен.</p>
            ) : (
              <p className="empty">Для этого типа заявки документ не требуется.</p>
            )}
          </>
        )}

        <p className="side-panel__label">Комментарий HR</p>
        <textarea
          className="area"
          value={comment}
          rows={3}
          placeholder="Добавить комментарий…"
          aria-label="Комментарий HR"
          onChange={(event) => setComment(event.target.value)}
        />

        {failed && <p className="empty empty--bad" role="alert">{failed}</p>}
        {done && <p className="empty" role="status">{done}</p>}

        {open ? (
          <div className="side-panel__actions">
            <button type="button" className="btn btn--dark" disabled={sending}
                    onClick={() => void decide('approve')}>
              {sending ? 'Отправляем…' : 'Подтвердить'}
            </button>
            <button type="button" className="btn" disabled={sending}
                    onClick={() => void decide('reject')}>
              Отклонить
            </button>
          </div>
        ) : (
          <p className="empty">
            Заявка уже рассмотрена. Повторное решение по ней не принимается.
          </p>
        )}
      </div>
    </aside>
  );
}

// --- мелочи ----------------------------------------------------------------

function kindTitle(item: api.QueueItem): string {
  if (item.kind === 'correction') return 'Исправление отметки';
  const code = item.absence?.absence_type?.code;
  return code === 'SICK_LEAVE' ? 'Больничный' : item.absence?.absence_type?.name ?? 'Отсутствие';
}

function dates(item: api.QueueItem): string {
  const first = item.absence?.first_day;
  const last = item.absence?.last_day;
  if (first && last) return first === last ? longDate(first) : `${first} — ${last}`;
  if (first) return longDate(first);
  const at = item.correction?.created_at ?? item.created_at;
  return at ? longDate(at.slice(0, 10)) : '—';
}

const size = (bytes: number) =>
  bytes < 1024 * 1024
    ? `${Math.round(bytes / 1024)} КБ`
    : `${Math.round((bytes / 1024 / 1024) * 10) / 10} МБ`;

function Picker({ label, value, empty, options, onChange }: {
  label: string; value: string; empty: string;
  options: { id: string; name: string }[]; onChange: (value: string) => void;
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
  if (block.state === 'loading') return <p className="empty">Загружаем очередь…</p>;
  if (block.state === 'denied') return <p className="empty">Нет доступа к заявкам.</p>;
  if (block.state === 'error') {
    return (
      <p className="empty empty--bad">
        Не удалось загрузить очередь. Это ошибка запроса, а не «заявок нет».
      </p>
    );
  }
  return <>{children(block.data)}</>;
}
