/**
 * Обращения: вопросы сотрудников и ответы HR.
 *
 * ВАЖНО про переписку. У backend нет цепочки сообщений: `EmployeeQuestion`
 * хранит один вопрос сотрудника, необязательный ответ ИИ и один ответ
 * кадровика. Значит, диалога из нескольких реплик здесь быть не может, и
 * рисовать его — значит показывать людям переписку, которой не было.
 *
 * Поэтому в центральной панели ровно то, что есть: вопрос, ответ (когда
 * он дан) и системные события, выведенные из настоящих полей —
 * «назначено», «отвечено», «закрыто». Ни одно сообщение не придумано.
 *
 * Ответ доставляется сотруднику: сервис кладёт уведомление в очередь той
 * же транзакцией, и бот его отправляет. Но очередь — это ещё не доставка,
 * поэтому подтверждение говорит «поставлен в очередь», а не «отправлено».
 */

import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell, initials } from '../components/AppShell';
import { Icon } from '../components/nav-icons';
import { messageFor } from '../api/errors';
import { useBlock, type Block } from '../features/dashboard/data';
import { useSession } from '../features/auth/session';

/** Вкладки. Одна вкладка может покрывать несколько состояний модели. */
const TABS = [
  { key: 'all', title: 'Все', statuses: ['NEW', 'AI_ANSWERED', 'ESCALATED_TO_HR', 'HR_ANSWERED', 'CLOSED'] },
  { key: 'new', title: 'Новые', statuses: ['NEW', 'ESCALATED_TO_HR'] },
  { key: 'work', title: 'В работе', statuses: ['AI_ANSWERED'] },
  { key: 'done', title: 'Закрытые', statuses: ['HR_ANSWERED', 'CLOSED'] },
] as const;

const STATUS_TITLE: Record<string, string> = {
  NEW: 'Новое',
  AI_ANSWERED: 'Есть ответ ИИ',
  ESCALATED_TO_HR: 'Ждёт ответа',
  HR_ANSWERED: 'Отвечено',
  CLOSED: 'Закрыто',
};

/** Состояния, из которых сервер разрешает отвечать. */
const ANSWERABLE = ['NEW', 'AI_ANSWERED', 'ESCALATED_TO_HR'];

export function QuestionsPage() {
  const session = useSession();
  const can = (code: string) =>
    session.status === 'authenticated' && session.user.permissions.includes(code);
  const me = session.status === 'authenticated' ? session.user.id : '';

  const [params, setParams] = useSearchParams();
  const tab = TABS.find((t) => t.key === params.get('tab')) ?? TABS[0];
  const search = params.get('search') ?? '';
  const office = params.get('office_id') ?? '';
  const mine = params.get('mine') === '1';
  const picked = params.get('id') ?? '';

  const [draft, setDraft] = useState(search);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => setDraft(search), [search]);

  const patch = useCallback(
    (changes: Record<string, string | null>) => {
      setParams(
        (was) => {
          const next = new URLSearchParams(was);
          for (const [key, value] of Object.entries(changes)) {
            if (value) next.set(key, value);
            else next.delete(key);
          }
          if (!('cursor' in changes)) next.delete('cursor');
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

  const cursor = params.get('cursor') ?? '';
  const key = `${tab.key}|${search}|${office}|${mine}|${cursor}|${attempt}`;

  const [list] = useBlock(
    (signal) =>
      api.escalationList(
        {
          status: tab.statuses.join(','),
          limit: '20',
          ...(search ? { search } : {}),
          ...(office ? { office_id: office } : {}),
          ...(mine ? { assigned_to_me: 'true' } : {}),
          ...(cursor ? { cursor } : {}),
        },
        signal,
      ),
    key,
  );

  const [counts] = useBlock(
    (signal) =>
      api.escalationCounts(
        { ...(office ? { office_id: office } : {}), ...(search ? { search } : {}) },
        signal,
      ),
    `counts|${search}|${office}|${attempt}`,
  );

  const [directory] = useBlock((signal) => api.offices(signal), 'offices');

  const rows = list.state === 'ready' ? list.data.items : [];
  const current = rows.find((row) => row.id === picked) ?? null;
  const badge = counts.state === 'ready' ? count(counts.data, TABS[1].statuses) : undefined;

  return (
    <AppShell breadcrumb="Обращения" section="questions"
              badges={badge ? { questions: badge } : {}}>
      <header className="head head--tight">
        <div>
          <h1 className="head__title">Обращения</h1>
          <p className="head__sub">Вопросы сотрудников и ответы HR</p>
        </div>
        <div className="head__actions">
          <label className="pick">
            <span className="visually-hidden">Офис</span>
            <select value={office} onChange={(event) => patch({ office_id: event.target.value || null })}>
              <option value="">Все офисы</option>
              {directory.state === 'ready' &&
                directory.data.items.map((item) => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
            </select>
          </label>
          <label className="pick">
            <span className="visually-hidden">Ответственный</span>
            <select value={mine ? 'me' : ''} onChange={(event) =>
              patch({ mine: event.target.value === 'me' ? '1' : null })}>
              <option value="">Все ответственные</option>
              <option value="me">Мои</option>
            </select>
          </label>
        </div>
      </header>

      <div className="tabs tabs--bare" role="tablist">
        {TABS.map((item) => (
          <button key={item.key} type="button" role="tab" aria-selected={item.key === tab.key}
                  className={item.key === tab.key ? 'tab tab--on' : 'tab'}
                  onClick={() => patch({ tab: item.key === 'all' ? null : item.key, id: null })}>
            {item.title}
            {counts.state === 'ready' && (
              <span className="tab__count">{count(counts.data, item.statuses)}</span>
            )}
          </button>
        ))}
      </div>

      <div className="talk-grid">
        <section className="sheet">
          <div className="toolbar">
            <label className="find find--wide">
              <Icon name="search" size={16} />
              <input type="search" value={draft} placeholder="Поиск обращений"
                     aria-label="Поиск обращений"
                     onChange={(event) => setDraft(event.target.value)} />
            </label>
          </div>
          <p className="toolbar__note">Сначала недавно обновлённые</p>

          <Rows block={list} name="обращения">
            {(data) =>
              data.items.length === 0 ? (
                <p className="empty">
                  {search || office || mine
                    ? 'По этим условиям обращений нет.'
                    : 'Обращений нет.'}
                </p>
              ) : (
                <ul className="threads">
                  {data.items.map((row) => (
                    <li key={row.id}>
                      <button
                        type="button"
                        className={row.id === picked ? 'thread thread--on' : 'thread'}
                        aria-current={row.id === picked ? 'true' : undefined}
                        onClick={() => patch({ id: row.id })}
                      >
                        <span className="avatar">{initials(row.employee.full_name)}</span>
                        <span className="thread__text">
                          <span className="thread__top">
                            <span className="thread__who">{row.employee.full_name}</span>
                            <span className="thread__time">{clockOf(row.updated_at)}</span>
                          </span>
                          <span className="thread__topic">
                            {row.normalized_topic || firstLine(row.question_text)}
                          </span>
                          <span className="thread__excerpt">{firstLine(row.question_text)}</span>
                          <span className="pill">{STATUS_TITLE[row.status] ?? row.status}</span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )
            }
          </Rows>

          <div className="pager">
            <p className="pager__note">
              {list.state === 'ready' ? `Показано ${rows.length}` : ''}
            </p>
            <div className="pager__tools">
              <button type="button" className="btn" disabled={!cursor}
                      onClick={() => patch({ cursor: null })}>
                Назад
              </button>
              <button type="button" className="btn btn--dark"
                      disabled={list.state !== 'ready' || !list.data.has_more}
                      onClick={() => list.state === 'ready' &&
                        patch({ cursor: list.data.next_cursor })}>
                Далее
              </button>
            </div>
          </div>
        </section>

        {current ? (
          <Thread
            key={current.id}
            row={current}
            canAnswer={can('questions.answer')}
            me={me}
            onChanged={() => setAttempt((n) => n + 1)}
          />
        ) : (
          <section className="panel talk-empty">
            <p className="empty">Выберите обращение слева.</p>
          </section>
        )}

        {current && <Context row={current} me={me} />}
      </div>
    </AppShell>
  );
}

// --- переписка -------------------------------------------------------------

function Thread({ row, canAnswer, me, onChanged }: {
  row: api.EscalationRow; canAnswer: boolean; me: string; onChanged: () => void;
}) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [queued, setQueued] = useState(false);
  const [busy, setBusy] = useState(false);

  const answerable = ANSWERABLE.includes(row.status);

  async function send() {
    if (sending || text.trim().length === 0) return;
    setSending(true);
    setFailed(null);
    try {
      await api.answerEscalation(row.id, text.trim());
      // Именно «в очереди»: сервис кладёт уведомление в outbox той же
      // транзакцией, отправляет его бот. Сказать «отправлено» до этого
      // значит подтвердить то, чего ещё не случилось.
      setQueued(true);
      setText('');
      onChanged();
    } catch (error) {
      // Набранное остаётся: терять текст ответа из-за сбоя нельзя.
      setFailed(messageFor(error));
    } finally {
      setSending(false);
    }
  }

  async function act(what: 'assign' | 'close') {
    if (busy) return;
    setBusy(true);
    setFailed(null);
    try {
      if (what === 'assign') await api.assignEscalation(row.id);
      else await api.closeEscalation(row.id);
      onChanged();
    } catch (error) {
      setFailed(messageFor(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel talk">
      <header className="talk__head">
        <span className="talk__title">
          {row.normalized_topic || firstLine(row.question_text)}
        </span>
        <span className="pill">{STATUS_TITLE[row.status] ?? row.status}</span>
        {canAnswer && (
          <span className="talk__actions">
            {row.assigned_to_user_id === null && answerable && (
              <button type="button" className="btn" disabled={busy}
                      onClick={() => void act('assign')}>
                Взять в работу
              </button>
            )}
            {row.status !== 'CLOSED' && (
              <button type="button" className="btn" disabled={busy}
                      onClick={() => void act('close')}>
                Закрыть
              </button>
            )}
          </span>
        )}
      </header>

      <div className="talk__body">
        <p className="talk__day">{dayOf(row.created_at)}</p>

        <Message who={row.employee.full_name} at={row.created_at} text={row.question_text} />

        {row.ai_answer_text && (
          <Message who="Ответ базы знаний" at={row.created_at} text={row.ai_answer_text} />
        )}

        {row.assigned_to_user_id && (
          <Event text={row.assigned_to_user_id === me
            ? 'Вы взяли обращение в работу'
            : 'Обращение взято в работу'} />
        )}

        {row.hr_answer_text && (
          <Message who="Ответ HR" at={row.answered_at ?? row.updated_at}
                   text={row.hr_answer_text} />
        )}

        {row.status === 'CLOSED' && <Event text="Обращение закрыто" />}
      </div>

      <footer className="talk__foot">
        {answerable && canAnswer ? (
          <>
            <p className="talk__label">Ответ сотруднику · Telegram</p>
            <textarea
              className="area"
              rows={3}
              value={text}
              placeholder="Ответ уйдёт сотруднику в чат"
              aria-label="Ответ сотруднику"
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) void send();
              }}
            />
            {failed && <p className="empty empty--bad" role="alert">{failed}</p>}
            {queued && (
              <p className="empty" role="status">
                Ответ сохранён и поставлен в очередь на отправку в Telegram.
              </p>
            )}
            <div className="talk__send">
              <span className="muted">Ctrl+Enter — отправить</span>
              <button type="button" className="btn btn--dark"
                      disabled={sending || text.trim().length === 0}
                      onClick={() => void send()}>
                <Icon name="send" size={16} />
                {sending ? 'Отправляем…' : 'Отправить'}
              </button>
            </div>
          </>
        ) : (
          <p className="empty">
            {row.hr_answer_text
              ? 'Ответ уже дан. Второй ответ по этому обращению сервер не примет.'
              : canAnswer
                ? 'Обращение закрыто — отвечать по нему нельзя.'
                : 'Нет права отвечать на обращения.'}
          </p>
        )}
      </footer>
    </section>
  );
}

function Message({ who, at, text }: { who: string; at: string; text: string }) {
  return (
    <article className="msg">
      <span className="avatar avatar--sm">{initials(who)}</span>
      <div className="msg__body">
        <p className="msg__head">
          <span className="msg__who">{who}</span>
          <span className="msg__time">{clockOf(at)}</span>
        </p>
        <p className="msg__text">{text}</p>
      </div>
    </article>
  );
}

/** Системное событие — не сообщение: у него нет автора и текста от людей. */
function Event({ text }: { text: string }) {
  return (
    <p className="talk__event">
      <Icon name="alert" size={14} />
      {text}
    </p>
  );
}

// --- правая панель ---------------------------------------------------------

function Context({ row, me }: { row: api.EscalationRow; me: string }) {
  const [card] = useBlock(
    (signal) => api.employee(row.employee.id, signal),
    `who|${row.employee.id}`,
  );

  return (
    <aside className="panel side-panel side-panel--static" aria-label="Контекст обращения">
      <div className="side-panel__body">
        <p className="side-panel__label">Сотрудник</p>
        <div className="who">
          <span className="avatar">{initials(row.employee.full_name)}</span>
          <span className="who__text">
            <span className="who__name">{row.employee.full_name}</span>
            <span className="who__id">{row.employee.employee_number ?? '—'}</span>
          </span>
        </div>

        <Body block={card}>
          {(person) => {
            const assignment = (person['current_assignment'] ?? null) as
              | Record<string, string | null>
              | null;
            return (
              <dl className="facts">
                <div className="facts__row">
                  <dt>Должность</dt>
                  <dd>{assignment?.['position_name'] ?? '—'}</dd>
                </div>
                <div className="facts__row">
                  <dt>Офис</dt>
                  <dd>{assignment?.['office_name'] ?? '—'}</dd>
                </div>
                <div className="facts__row">
                  <dt>Отдел</dt>
                  <dd>{assignment?.['department_name'] ?? '—'}</dd>
                </div>
              </dl>
            );
          }}
        </Body>

        <a className="btn" href={`/employees?employee=${row.employee.id}`}>
          Карточка сотрудника →
        </a>

        <p className="side-panel__label">Об обращении</p>
        <dl className="facts">
          <div className="facts__row">
            <dt>Статус</dt>
            <dd>{STATUS_TITLE[row.status] ?? row.status}</dd>
          </div>
          <div className="facts__row">
            <dt>Ответственный</dt>
            <dd>
              {row.assigned_to_user_id
                ? row.assigned_to_user_id === me
                  ? 'Вы'
                  : 'Назначен'
                : <span className="muted">Не назначен</span>}
            </dd>
          </div>
          <div className="facts__row"><dt>Канал</dt><dd>Telegram</dd></div>
          <div className="facts__row"><dt>Создано</dt><dd>{dayOf(row.created_at)}</dd></div>
          <div className="facts__row"><dt>Изменено</dt><dd>{dayOf(row.updated_at)}</dd></div>
        </dl>

        <p className="side-panel__label">Связанные записи</p>
        <p className="empty">
          Связей с заявками у обращения нет: backend их не хранит, а
          показывать выдуманную заявку нельзя.
        </p>
      </div>
    </aside>
  );
}

// --- мелочи ----------------------------------------------------------------

function count(counts: Record<string, number>, statuses: readonly string[]): number {
  return statuses.reduce((sum, key) => sum + (counts[key] ?? 0), 0);
}

const firstLine = (text: string) =>
  text.length > 90 ? `${text.slice(0, 90)}…` : text;

const clockOf = (at: string) => at.slice(11, 16);

const MONTHS = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

function dayOf(at: string): string {
  const [year, month, date] = at.slice(0, 10).split('-').map(Number);
  if (!year || !month || !date) return at;
  return `${date} ${MONTHS[month - 1]} ${year}`;
}

function Rows<T>({ block, name, children }: {
  block: Block<T>; name: string; children: (data: T) => React.ReactNode;
}) {
  if (block.state === 'loading') return <p className="empty">Загружаем {name}…</p>;
  if (block.state === 'denied') return <p className="empty">Нет доступа к обращениям.</p>;
  if (block.state === 'error') {
    return (
      <p className="empty empty--bad">
        Не удалось загрузить {name}. Это ошибка запроса, а не «их нет».
      </p>
    );
  }
  return <>{children(block.data)}</>;
}

function Body<T>({ block, children }: {
  block: Block<T>; children: (data: T) => React.ReactNode;
}) {
  if (block.state === 'loading') return <p className="empty">Загружаем…</p>;
  if (block.state === 'denied') return <p className="empty">Нет доступа к карточке.</p>;
  if (block.state === 'error') return <p className="empty empty--bad">Не удалось загрузить.</p>;
  return <>{children(block.data)}</>;
}
