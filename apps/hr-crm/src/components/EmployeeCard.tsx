/**
 * Карточка сотрудника — выдвижная панель справа.
 *
 * Четыре раздела: обзор, назначения, график, Telegram. Данные берутся
 * теми же endpoint'ами, что уже есть у backend; ни один из них не
 * вызывается «на всякий случай» — приглашение Telegram создаётся ТОЛЬКО
 * по нажатию, иначе открытие карточки раздавало бы ссылки доступа.
 *
 * Ссылка приглашения показывается один раз и никуда не сохраняется: это
 * рабочий секрет, пока она жива.
 */

import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';

import * as api from '../api/crm';
import { AppIcon } from './AppIcon';
import { initials } from './AppShell';
import { messageFor } from '../api/errors';
import { useBlock, type Block } from '../features/dashboard/data';

const TABS = ['Обзор', 'Назначения', 'График', 'Telegram'] as const;

/** Кадровое состояние человеческими словами. Код показывается, только
 *  если он незнаком — молча прятать его хуже, чем показать как есть. */
const STATUS_TITLE: Record<string, string> = {
  ACTIVE: 'Активен',
  PROBATION: 'Испытательный срок',
  SUSPENDED: 'Приостановлен',
  TERMINATED: 'Уволен',
  ARCHIVED: 'В архиве',
};

const STATE_TITLE: Record<string, string> = {
  ACTIVE: 'Привязан',
  PENDING: 'Ожидает HR',
  PENDING_CONFIRMATION: 'Ожидает HR',
  NONE: 'Не привязан',
  NOT_LINKED: 'Не привязан',
  REVOKED: 'Отозван',
  BLOCKED: 'Заблокирован',
};

export function EmployeeCard({ id, onClose }: { id: string; onClose: () => void }) {
  const location = useLocation();
  const [tab, setTab] = useState<(typeof TABS)[number]>('Обзор');

  const [card] = useBlock(
    (signal) =>
      Promise.all([
        api.employee(id, signal),
        api.employeeAssignments(id, signal),
        api.employeeSchedules(id, signal),
        api.employeeTelegram(id, signal).catch(() => null),
      ]).then(([person, assignments, schedules, telegram]) => ({
        person,
        assignments: assignments.items,
        schedules: schedules.items,
        telegram,
      })),
    `card|${id}`,
  );

  return (
    <>
      <div className="veil" onClick={onClose} aria-hidden="true" />
      <aside className="drawer" role="dialog" aria-label="Карточка сотрудника">
        <Body block={card}>
          {(data) => {
            const person = data.person as Record<string, string | null>;
            const name = String(person['full_name'] ?? '');
            return (
              <>
                <header className="drawer__head">
                  <span className="avatar avatar--big">{initials(name)}</span>
                  <span className="drawer__who">
                    <span className="drawer__name">{name}</span>
                    <span className="drawer__id">
                      {[person['position_name'], person['office_name']]
                        .filter(Boolean).join(' · ') || 'Должность не назначена'}
                    </span>
                  </span>
                  {/* Переход в полную карточку, а не второй редактор
                      того же человека: быстрые действия остаются здесь,
                      подробности живут по своему адресу. */}
                  <Link className="link drawer__more"
                        to={`/employees/${id}?back=${encodeURIComponent(
                          location.pathname + location.search,
                        )}`}>
                    Открыть карточку <AppIcon name="arrow" size={16} />
                  </Link>
                  <button type="button" className="tool" aria-label="Закрыть" onClick={onClose}>
                    <AppIcon name="close" size={16} />
                  </button>
                </header>

                <div className="tabs tabs--drawer" role="tablist">
                  {TABS.map((item) => (
                    <button
                      key={item}
                      type="button"
                      role="tab"
                      aria-selected={item === tab}
                      className={item === tab ? 'tab tab--on' : 'tab'}
                      onClick={() => setTab(item)}
                    >
                      {item}
                    </button>
                  ))}
                </div>

                <div className="drawer__body">
                  {tab === 'Обзор' && <Overview person={person} />}
                  {tab === 'Назначения' && <History rows={data.assignments} />}
                  {tab === 'График' && <Schedules rows={data.schedules} />}
                  {tab === 'Telegram' && <Telegram id={id} link={data.telegram} />}
                </div>
              </>
            );
          }}
        </Body>
      </aside>
    </>
  );
}

function Overview({ person }: { person: Record<string, string | null> }) {
  const rows: [string, string | null][] = [
    ['Статус', title(person['employment_status'])],
    ['Принят', person['hire_date'] ?? null],
    ['Уволен', person['termination_date'] ?? null],
    ['Телефон', person['phone'] ?? null],
    ['Рабочая почта', person['corporate_email'] ?? null],
    ['Язык', person['preferred_language'] ?? null],
  ];
  return (
    <dl className="facts">
      {rows.map(([title, value]) => (
        <div key={title} className="facts__row">
          <dt>{title}</dt>
          <dd>{value ?? <span className="muted">не указано</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

const title = (code: string | null | undefined) =>
  code ? STATUS_TITLE[code] ?? code : '—';

function History({ rows }: { rows: api.Assignment[] }) {
  if (rows.length === 0) return <p className="empty">Назначений пока нет.</p>;
  return (
    <ul className="timeline">
      {rows.map((row) => (
        <li key={row.id}>
          <p className="timeline__title">
            {row.position_name ?? '—'}
            {row.is_primary && <span className="chip">основное</span>}
          </p>
          <p className="timeline__note">
            {[row.office_name, row.department_name].filter(Boolean).join(' · ') || '—'}
          </p>
          <p className="timeline__when">
            с {row.valid_from ?? '—'}
            {row.valid_to ? ` по ${row.valid_to}` : ' — по настоящее время'}
          </p>
        </li>
      ))}
    </ul>
  );
}

function Schedules({ rows }: { rows: Record<string, unknown>[] }) {
  if (rows.length === 0) {
    return (
      <p className="empty">
        График не назначен. Пустое место здесь означает именно это, а не
        «работает как все».
      </p>
    );
  }
  return (
    <ul className="timeline">
      {rows.map((row, index) => (
        <li key={String(row['id'] ?? index)}>
          <p className="timeline__title">{String(row['schedule_name'] ?? row['name'] ?? '—')}</p>
          <p className="timeline__when">
            с {String(row['valid_from'] ?? '—')}
            {row['valid_to'] ? ` по ${String(row['valid_to'])}` : ' — по настоящее время'}
          </p>
        </li>
      ))}
    </ul>
  );
}

function Telegram({ id, link }: { id: string; link: api.TelegramLink | null }) {
  const [invite, setInvite] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const state = link?.state ?? 'NONE';
  const linked = state === 'ACTIVE';

  async function ask() {
    if (sending) return;
    setSending(true);
    setFailed(null);
    try {
      const created = await api.inviteToTelegram(id);
      // Ссылка живёт только на экране: в хранилище браузера ей не место,
      // пока она действует — это рабочий доступ к данным человека.
      setInvite(created.link ?? created.url ?? created.token ?? null);
    } catch (error) {
      setFailed(messageFor(error));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="tg">
      <p className="tg__state">
        <AppIcon name="send" size={16} />
        {STATE_TITLE[state] ?? state}
      </p>
      {link?.account?.telegram_username && (
        <p className="muted">@{link.account.telegram_username}</p>
      )}

      {!linked && (
        <>
          <button type="button" className="btn btn--dark" onClick={ask} disabled={sending}>
            {sending ? 'Создаём…' : 'Пригласить'}
          </button>
          <p className="muted">
            Ссылка одноразовая и показывается один раз. Передайте её человеку лично.
          </p>
        </>
      )}

      {failed && <p className="empty empty--bad">{failed}</p>}

      {invite && (
        <div className="tg__link">
          <code>{invite}</code>
          <button type="button" className="btn"
                  onClick={() => void navigator.clipboard?.writeText(invite)}>
            Скопировать
          </button>
        </div>
      )}
    </div>
  );
}

function Body<T>({ block, children }: { block: Block<T>; children: (data: T) => React.ReactNode }) {
  if (block.state === 'loading') return <p className="empty">Загружаем карточку…</p>;
  if (block.state === 'denied') return <p className="empty">Нет доступа к карточке.</p>;
  if (block.state === 'error') return <p className="empty empty--bad">Не удалось загрузить карточку.</p>;
  return <>{children(block.data)}</>;
}
