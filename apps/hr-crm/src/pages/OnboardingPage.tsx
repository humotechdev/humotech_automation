/**
 * Первичное ознакомление: кто где остановился, тексты разделов и
 * обязательные документы.
 *
 * Три вкладки, потому что это три разных предмета.
 *
 * **Сотрудники** — единственное место, где видно, что человек ещё не
 * работает с ботом. Рядом с прогрессом стоит состояние привязки: без
 * него «0 из 10» одинаково выглядит и у того, кто ленится, и у того,
 * кому ссылку просто некуда отправить, — а делать в этих случаях надо
 * разное.
 *
 * **Разделы** — десять информационных карточек. Правка текста поднимает
 * редакцию, но никого не возвращает к чтению: карточка сообщает, а не
 * обязывает.
 *
 * **Документы** — то, что обязывает. Редакция публикуется отдельным
 * действием и с этой секунды возвращает к подтверждению всех, кто её
 * не принял. Поэтому публикация спрашивает подтверждение и называет
 * последствие, а не «уверены ли вы».
 *
 * Доступа к боту ознакомление не закрывает ни в каком состоянии.
 * Незавершённое — это работа кадровика: напомнить, дослать ссылку,
 * поговорить с отказавшимся. Поэтому страница устроена как очередь, а
 * не как список нарушителей.
 */

import { useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell } from '../components/AppShell';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import {
  Confirm,
  Empty,
  Failed,
  Field,
  Loading,
  Refusal,
  SectionBar,
  SidePanel,
  useSaving,
} from '../components/admin/Parts';
import { useBlock } from '../features/dashboard/data';
import { useSession } from '../features/auth/session';
import { moment } from '../features/time/zone';
import '../styles/admin.css';
import '../styles/onboarding.css';

type Tab = 'people' | 'sections' | 'documents';

/**
 * Сколько строк берём за раз. Потолок сервера — 200; отбор и поиск
 * работают по этой странице, а числа на вкладках приходят отдельно и
 * считают всех. Когда страница упирается в потолок, под таблицей
 * появляется прямая оговорка — молчаливое усечение было бы враньём.
 */
const PAGE = 200;

/** Состояния в порядке прохождения — так же они стоят и во вкладках отбора. */
const STATE: Record<api.OnboardingStatus, string> = {
  NOT_STARTED: 'Не начал',
  IN_PROGRESS: 'Читает разделы',
  INFO_COMPLETED: 'Ждёт согласия',
  POLICIES_IN_PROGRESS: 'Подтверждает документы',
  COMPLETED: 'Завершил',
  UPDATE_REQUIRED: 'Требуется ознакомление',
  BLOCKED_BY_DECLINED_POLICY: 'Отказался',
};

/**
 * Состояния, с которыми кадровик что-то делает сам.
 *
 * Отказ разбирают разговором, новую редакцию — напоминанием адресно.
 * Остальное незавершённое бот доводит сам: раз в несколько дней он
 * напоминает, и вмешиваться в это не нужно.
 */
const ATTENTION: api.OnboardingStatus[] = [
  'BLOCKED_BY_DECLINED_POLICY', 'UPDATE_REQUIRED',
];

/**
 * Вкладки отбора. «Ожидает согласия» склеивает два состояния —
 * «дочитал» и «подтвердил часть»: для кадровика это одно и то же
 * положение дел, человек сидит на документах.
 */
const FILTERS: Array<{ key: string; title: string; match: api.OnboardingStatus[] }> = [
  { key: 'all', title: 'Все', match: [] },
  { key: 'attention', title: 'Требуют внимания', match: ATTENTION },
  { key: 'NOT_STARTED', title: 'Не начали', match: ['NOT_STARTED'] },
  { key: 'IN_PROGRESS', title: 'В процессе', match: ['IN_PROGRESS'] },
  {
    key: 'POLICIES',
    title: 'Ожидают согласия',
    match: ['INFO_COMPLETED', 'POLICIES_IN_PROGRESS'],
  },
  {
    key: 'UPDATE_REQUIRED',
    title: 'Нужна новая редакция',
    match: ['UPDATE_REQUIRED'],
  },
  { key: 'COMPLETED', title: 'Завершили', match: ['COMPLETED'] },
  {
    key: 'BLOCKED_BY_DECLINED_POLICY',
    title: 'Отказались',
    match: ['BLOCKED_BY_DECLINED_POLICY'],
  },
];

const TELEGRAM: Record<string, string> = {
  ACTIVE: 'Привязан',
  PENDING: 'Ждёт подтверждения',
  REVOKED: 'Отключён',
  BLOCKED: 'Заблокирован',
  NOT_LINKED: 'Не привязан',
};

export function OnboardingPage() {
  const session = useSession();
  const zone = session.status === 'authenticated' ? session.user.timezone : '';

  const [params, setParams] = useSearchParams();
  const tab = (params.get('tab') as Tab) || 'people';
  const filter = params.get('state') || 'all';
  const picked = params.get('employee');
  const [search, setSearch] = useState('');
  const [attempt, setAttempt] = useState(0);

  const patch = useCallback(
    (next: Record<string, string | null>) => {
      setParams(
        (was) => {
          const copy = new URLSearchParams(was);
          for (const [key, value] of Object.entries(next)) {
            if (!value) copy.delete(key);
            else copy.set(key, value);
          }
          return copy;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const [people, reloadPeople] = useBlock(
    (signal) => api.onboardingProgress({ limit: String(PAGE) }, signal),
    `onboarding-people|${attempt}`,
    true,
  );
  /*
   * Числа на вкладках приходят с сервера, а не считаются по таблице.
   * Таблица — одна страница, и посчитанное по ней «Завершили 42»
   * означало бы «сорок два из первых двухсот», молча и без оговорки.
   * Два запроса дают два момента времени, но лучше чуть устаревшая
   * правда, чем свежая неправда.
   */
  const [counts] = useBlock(
    (signal) => api.onboardingCounts(signal),
    `onboarding-counts|${attempt}`,
    true,
  );
  const [sections, reloadSections] = useBlock(
    (signal) => api.onboardingSections(signal),
    `onboarding-sections|${attempt}`,
    true,
  );
  const [documents, reloadDocuments] = useBlock(
    (signal) => api.policyDocuments(signal),
    `onboarding-documents|${attempt}`,
    true,
  );

  const refresh = useCallback(() => {
    setAttempt((n) => n + 1);
    reloadPeople();
    reloadSections();
    reloadDocuments();
  }, [reloadPeople, reloadSections, reloadDocuments]);

  const rows = people.state === 'ready' ? people.data.items : [];
  const sectionRows = sections.state === 'ready' ? sections.data.items : [];
  const documentRows = documents.state === 'ready' ? documents.data.items : [];

  /**
   * Числа на вкладках. Пока сервер не ответил — по видимым строкам:
   * пустая вкладка читается как «никого нет», а это неправда.
   */
  const tally = useMemo(() => {
    if (counts.state === 'ready') {
      const server = counts.data;
      const made: Record<string, number> = { all: server['all'] ?? 0 };
      for (const one of FILTERS.slice(1)) {
        made[one.key] = one.match.reduce(
          (sum, status) => sum + (server[status] ?? 0), 0,
        );
      }
      return made;
    }
    const made: Record<string, number> = { all: rows.length };
    for (const one of FILTERS.slice(1)) {
      made[one.key] = rows.filter((row) => one.match.includes(row.status)).length;
    }
    return made;
  }, [rows, counts]);

  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const chosen = FILTERS.find((one) => one.key === filter);
    return rows.filter((row) => {
      if (chosen && chosen.match.length && !chosen.match.includes(row.status)) {
        return false;
      }
      if (!needle) return true;
      return (
        row.full_name.toLowerCase().includes(needle)
        || (row.employee_number ?? '').toLowerCase().includes(needle)
      );
    });
  }, [rows, filter, search]);

  const [sectionDraft, setSectionDraft] = useState<SectionDraft | null>(null);
  const [documentPick, setDocumentPick] = useState<string | null>(null);

  const open = picked !== null || sectionDraft !== null || documentPick !== null;

  return (
    <AppShell breadcrumb="Ознакомление" section="onboarding">
      <header className="head head--tight">
        <div>
          <h1 className="head__title">Ознакомление</h1>
          <p className="head__sub">
            Новый сотрудник читает материалы компании и подтверждает
            обязательные документы в Telegram. Бот напоминает сам, пока
            дело не доделано
          </p>
        </div>
        <div className="head__actions">
          <ExportButton />
        </div>
      </header>

      <div className="tabs tabs--top" role="tablist" aria-label="Разделы ознакомления">
        {([
          ['people', 'Сотрудники', rows.length],
          ['sections', 'Разделы', sectionRows.length],
          ['documents', 'Документы', documentRows.length],
        ] as Array<[Tab, string, number]>).map(([key, title, count]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key}
                  className={tab === key ? 'tab tab--on' : 'tab'}
                  onClick={() => patch({
                    tab: key === 'people' ? null : key,
                    employee: null,
                  })}>
            {title}
            <span className="tab__count">{count}</span>
          </button>
        ))}
      </div>

      <div className={open ? 'split split--open' : 'split'}>
        <section className="panel panel--list" aria-label="Ознакомление">
          {tab === 'people' && (
            <>
              <SectionBar search={search} onSearch={setSearch}
                          placeholder="Имя или табельный номер">
                <div className="ob-chips" role="group" aria-label="Состояние">
                  {FILTERS.map((one) => (
                    <button key={one.key} type="button"
                            className={filter === one.key ? 'ob-chip ob-chip--on' : 'ob-chip'}
                            onClick={() => patch({
                              state: one.key === 'all' ? null : one.key,
                            })}>
                      {one.title}
                      <span className="ob-chip__count">{tally[one.key] ?? 0}</span>
                    </button>
                  ))}
                </div>
              </SectionBar>

              {people.state === 'loading' && <Loading />}
              {people.state === 'error' && <Failed onRetry={reloadPeople} />}
              {people.state === 'ready' && shown.length === 0 && (
                <Empty
                  filtered={filter !== 'all' || search.trim() !== ''}
                  nothing="Под этот отбор никто не подходит."
                  none={
                    'Пока никого не позвали. Ознакомление назначается сотруднику '
                    + 'в его карточке — кнопкой «Отправить ознакомление».'
                  }
                />
              )}
              {people.state === 'ready' && shown.length > 0 && (
                <div className="scroller">
                  <table className="grid-table table-cards" aria-label="Прогресс ознакомления">
                    <thead>
                      <tr>
                        <th scope="col">Сотрудник</th>
                        <th scope="col">Telegram</th>
                        <th scope="col">Разделы</th>
                        <th scope="col">Документы</th>
                        <th scope="col">Состояние</th>
                        <th scope="col"><span className="visually-hidden">Открыть</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map((row) => (
                        <tr key={row.employee_id}
                            className={row.employee_id === picked ? 'row row--on' : 'row'}>
                          <td>
                            <button type="button" className="adm-name"
                                    onClick={() => patch({ employee: row.employee_id })}>
                              <span className="adm-name__title">{row.full_name}</span>
                              <span className="adm-name__sub">
                                {[row.office_name, row.position_name]
                                  .filter(Boolean).join(' · ') || '—'}
                              </span>
                            </button>
                          </td>
                          <td data-label="Telegram">
                            <span className={
                              row.telegram_state === 'ACTIVE'
                                ? 'ob-tg ob-tg--on' : 'ob-tg'
                            }>
                              {TELEGRAM[row.telegram_state] ?? row.telegram_state}
                            </span>
                          </td>
                          <td data-label="Разделы">
                            <Progress done={row.sections_done} total={row.sections_total} />
                          </td>
                          <td data-label="Документы">
                            <Progress done={row.policies_done} total={row.policies_total}
                                      word="документа" />
                          </td>
                          <td data-label="Состояние">
                            <span className={
                              row.status === 'BLOCKED_BY_DECLINED_POLICY'
                                ? 'state state--bad'
                                : row.status === 'UPDATE_REQUIRED'
                                  ? 'state state--warn'
                                  : row.completed ? 'state state--good' : 'state'
                            }>
                              <i className="state__dot" />
                              {STATE[row.status] ?? row.status}
                            </span>
                          </td>
                          <td className="num">
                            <button type="button" className="tool tool--ghost"
                                    aria-label={`Открыть ${row.full_name}`}
                                    onClick={() => patch({ employee: row.employee_id })}>
                              <AppIcon name="arrow" size={16} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {people.state === 'ready' && people.data.has_more && (
                    <p className="ob-more">
                      Показаны первые {PAGE}. Уточните отбор или воспользуйтесь
                      выгрузкой — в ней все.
                    </p>
                  )}
                </div>
              )}
            </>
          )}

          {tab === 'sections' && (
            <SectionList
              block={sections}
              rows={sectionRows}
              onRetry={reloadSections}
              onPick={setSectionDraft}
            />
          )}

          {tab === 'documents' && (
            <DocumentList
              block={documents}
              rows={documentRows}
              zone={zone}
              onRetry={reloadDocuments}
              onPick={setDocumentPick}
            />
          )}
        </section>

        {picked && (
          <PersonPanel
            id={picked}
            zone={zone}
            onClose={() => patch({ employee: null })}
            onChanged={refresh}
          />
        )}
        {sectionDraft && (
          <SectionPanel
            draft={sectionDraft}
            onClose={() => setSectionDraft(null)}
            onSaved={() => { setSectionDraft(null); refresh(); }}
          />
        )}
        {documentPick && (
          <DocumentPanel
            id={documentPick}
            rows={documentRows}
            zone={zone}
            onClose={() => setDocumentPick(null)}
            onChanged={refresh}
          />
        )}
      </div>
    </AppShell>
  );
}

/* --- прогресс ------------------------------------------------------------ */

/**
 * «7 из 10» с полоской.
 *
 * Число и полоска вместе, а не по отдельности: полоска показывает, много
 * ли осталось, с одного взгляда по всему столбцу, а число отвечает на
 * вопрос «сколько именно», когда взгляд остановился.
 */
function Progress({ done, total, word = 'раздела' }: {
  done: number; total: number; word?: string;
}) {
  if (total === 0) return <span className="ob-progress__none">—</span>;
  const share = Math.round((done / total) * 100);
  return (
    <span className="ob-progress" title={`${done} из ${total} ${word}`}>
      <span className="ob-progress__bar" aria-hidden="true">
        <i style={{ width: `${share}%` }}
           className={done >= total ? 'ob-progress__fill ob-progress__fill--done'
                                    : 'ob-progress__fill'} />
      </span>
      <b className="ob-progress__num">{done}/{total}</b>
    </span>
  );
}

/* --- карточка сотрудника ------------------------------------------------- */

const EVENT_ICON: Record<string, AppIconName> = {
  invited: 'send',
  linked: 'user',
  started: 'book',
  section: 'doc',
  info_completed: 'check',
  // Документ обязывает — замок, а не лист бумаги: в ленте это событие
  // надо отличать от прочитанной карточки с одного взгляда.
  policy: 'lock',
  completed: 'check',
  reminded: 'bell',
};

function PersonPanel({ id, zone, onClose, onChanged }: {
  id: string;
  zone: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [attempt, setAttempt] = useState(0);
  const [card, reload] = useBlock(
    (signal) => api.employeeOnboarding(id, signal),
    `onboarding-card|${id}|${attempt}`,
    true,
  );
  const again = useCallback(() => {
    setAttempt((n) => n + 1);
    reload();
    onChanged();
  }, [reload, onChanged]);

  return (
    <SidePanel title="Ознакомление сотрудника" onClose={onClose}>
      {card.state === 'loading' && <Loading />}
      {card.state === 'error' && <Failed onRetry={reload} />}
      {card.state === 'ready' && (
        <>
          <h3 className="ob-person">{card.data.full_name}</h3>
          <p className="ob-person__sub">
            {[card.data.office_name, card.data.department_name, card.data.position_name]
              .filter(Boolean).join(' · ') || 'Место работы не указано'}
          </p>

          <div className="ob-facts">
            <Fact label="Состояние" value={STATE[card.data.status] ?? card.data.status} />
            <Fact label="Разделы"
                  value={`${card.data.sections_done} из ${card.data.sections_total}`} />
            <Fact label="Документы"
                  value={`${card.data.policies_done} из ${card.data.policies_total}`} />
            <Fact label="Telegram"
                  value={TELEGRAM[card.data.telegram_state] ?? card.data.telegram_state} />
          </div>

          <Invitation row={card.data} zone={zone} onChanged={again} />

          <h4 className="ob-sub">Что происходило</h4>
          {card.data.timeline.length === 0 ? (
            <p className="empty">Пока ничего: приглашение ещё не выдавали.</p>
          ) : (
            <ol className="ob-line">
              {card.data.timeline.map((event, index) => (
                <li key={`${event.kind}-${index}`} className="ob-line__row">
                  <span className="ob-line__mark" aria-hidden="true">
                    <AppIcon name={EVENT_ICON[event.kind] ?? 'doc'} size={16} />
                  </span>
                  <span className="ob-line__body">
                    <b>{event.title}</b>
                    <span className="ob-line__when">
                      {moment(event.at, zone, true)}
                      {event.detail ? ` · ${event.detail}` : ''}
                    </span>
                  </span>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </SidePanel>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="ob-fact">
      <span className="ob-fact__label">{label}</span>
      <b className="ob-fact__value">{value}</b>
    </div>
  );
}

/**
 * Ссылка и то, что с ней можно сделать.
 *
 * Ссылка показывается РОВНО ОДИН раз — в ответе на её выдачу. В базе
 * лежит только хеш токена, и достать её оттуда нельзя даже
 * суперпользователю: потерянная отзывается и выдаётся заново. Поэтому
 * поле с адресом появляется сразу после нажатия и исчезает при закрытии
 * панели, а не хранится в карточке.
 */
function Invitation({ row, zone, onChanged }: {
  row: api.OnboardingCard;
  zone: string;
  onChanged: () => void;
}) {
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const saving = useSaving();
  const live = row.invitation_status === 'ACTIVE';
  const linked = row.telegram_state === 'ACTIVE';

  const issue = (again: boolean) =>
    saving.run(async () => {
      const made = await api.inviteToOnboarding(row.employee_id, again);
      setLink(made.link);
      setCopied(false);
      setNote(made.linked
        ? 'Ссылка не нужна: Telegram уже привязан. Ознакомление назначено.'
        : null);
    }, onChanged);

  return (
    <div className="ob-invite">
      <h4 className="ob-sub">Telegram и ознакомление</h4>
      <p className="ob-invite__state">
        {live
          ? `Ссылка выдана, действует до ${moment(row.invitation_expires_at ?? '', zone, false)}`
          : linked
            ? 'Telegram привязан: ссылка больше не нужна'
            : 'Ссылка не выдавалась'}
      </p>

      <div className="ob-invite__tools">
        {!live && !linked && (
          <button type="button" className="btn btn--primary" disabled={saving.busy}
                  onClick={() => issue(false)}>
            <AppIcon name="send" size={16} /> Создать приглашение
          </button>
        )}
        {live && (
          <>
            <button type="button" className="btn" disabled={saving.busy}
                    onClick={() => issue(true)}>
              Создать новую ссылку
            </button>
            <button type="button" className="btn" disabled={saving.busy}
                    onClick={() => saving.run(
                      () => api.revokeOnboardingInvite(row.employee_id),
                      () => { setLink(null); onChanged(); },
                    )}>
              Отозвать
            </button>
          </>
        )}
        {/*
          * «Отправить» доступно только при живой привязке — и это не
          * ограничение интерфейса, а правило Telegram: бот не может
          * написать первым. Пока человек не открыл бота сам, отправлять
          * сообщение некуда, и кнопка, которая молча ничего не делает,
          * хуже выключенной.
          */}
        <button type="button" className="btn" disabled={saving.busy || !linked}
                title={linked ? '' : 'Бот не может написать первым: сначала ссылка'}
                onClick={() => saving.run(
                  () => api.remindOnboarding(row.employee_id),
                  () => { setNote('Напоминание отправлено'); onChanged(); },
                )}>
          <AppIcon name="bell" size={16} /> Напомнить в Telegram
        </button>
      </div>

      {link && (
        <div className="ob-link">
          <p className="ob-link__hint">
            Ссылка персональная и одноразовая. Она показывается один раз —
            скопируйте её сейчас: восстановить её нельзя, только выпустить новую.
          </p>
          <div className="ob-link__row">
            <input className="ob-link__value" readOnly value={link}
                   aria-label="Ссылка на ознакомление"
                   onFocus={(event) => event.currentTarget.select()} />
            <button type="button" className="btn"
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(link);
                        setCopied(true);
                      } catch {
                        // Буфер обмена закрыт настройками браузера.
                        // Поле рядом уже выделяется по щелчку — этого
                        // достаточно, чтобы скопировать руками.
                        setCopied(false);
                      }
                    }}>
              {copied ? 'Скопировано' : 'Скопировать'}
            </button>
          </div>
        </div>
      )}

      {note && <p className="ob-note">{note}</p>}
      <Refusal text={saving.refusal} />
    </div>
  );
}

/* --- разделы ------------------------------------------------------------- */

type SectionDraft = {
  id: string | null;
  title: string;
  body: string;
  button_label: string;
};

function blankSection(): SectionDraft {
  return { id: null, title: '', body: '', button_label: 'Я ознакомился' };
}

function SectionList({ block, rows, onRetry, onPick }: {
  block: ReturnType<typeof useBlock<{ items: api.OnboardingSection[] }>>[0];
  rows: api.OnboardingSection[];
  onRetry: () => void;
  onPick: (draft: SectionDraft) => void;
}) {
  return (
    <>
      <div className="toolbar adm-bar">
        <p className="ob-about">
          Эти карточки сотрудник читает в Telegram по одной. Правка текста
          поднимает редакцию раздела, но перечитывать заново никого не
          заставляет — карточка рассказывает, а не обязывает.
        </p>
        <button type="button" className="btn btn--primary"
                onClick={() => onPick(blankSection())}>
          <AppIcon name="plus" size={16} /> Добавить раздел
        </button>
      </div>

      {block.state === 'loading' && <Loading />}
      {block.state === 'error' && <Failed onRetry={onRetry} />}
      {block.state === 'ready' && rows.length === 0 && (
        <Empty filtered={false} nothing=""
               none="Разделов нет. Наполнение ставится командой seed_onboarding." />
      )}
      {block.state === 'ready' && rows.length > 0 && (
        <ol className="ob-cards">
          {rows.map((row) => (
            <li key={row.id}>
              <button type="button" className="ob-card"
                      onClick={() => onPick({
                        id: row.id,
                        title: row.title,
                        body: row.body,
                        button_label: row.button_label,
                      })}>
                <span className="ob-card__no">{row.position}</span>
                <span className="ob-card__body">
                  <b className="ob-card__title">{row.title}</b>
                  <span className="ob-card__text">{row.body.slice(0, 140)}…</span>
                  <span className="ob-card__foot">
                    Кнопка: «{row.button_label}» · редакция {row.version}
                  </span>
                </span>
                <AppIcon name="arrow" size={16} />
              </button>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}

function SectionPanel({ draft, onClose, onSaved }: {
  draft: SectionDraft;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState(draft);
  const [archiving, setArchiving] = useState(false);
  const saving = useSaving();
  const archive = useSaving();

  const set = (key: keyof SectionDraft, value: string) =>
    setForm((was) => ({ ...was, [key]: value }));

  const valid = form.title.trim() !== '' && form.body.trim() !== '';

  return (
    <>
      <SidePanel title={draft.id ? 'Раздел ознакомления' : 'Новый раздел'}
                 onClose={onClose}>
        <Field label="Название">
          <input value={form.title} maxLength={255}
                 onChange={(event) => set('title', event.target.value)} />
        </Field>
        <Field label="Текст"
               hint="То, что человек увидит в чате. Разметка не поддерживается — обычный текст и списки.">
          <textarea className="ob-area" rows={16} value={form.body}
                    onChange={(event) => set('body', event.target.value)} />
        </Field>
        <Field label="Подпись кнопки"
               hint="Под правилами уместнее «С правилами ознакомился», под последним разделом — «Завершить ознакомление».">
          <input value={form.button_label} maxLength={100}
                 onChange={(event) => set('button_label', event.target.value)} />
        </Field>

        <Refusal text={saving.refusal} />
        <div className="adm-side__tools">
          {draft.id && (
            <button type="button" className="btn btn--danger"
                    onClick={() => setArchiving(true)}>
              Убрать из программы
            </button>
          )}
          <button type="button" className="btn" onClick={onClose}>Отмена</button>
          <button type="button" className="btn btn--primary"
                  disabled={!valid || saving.busy}
                  onClick={() => saving.run(
                    () => draft.id
                      ? api.updateOnboardingSection(draft.id, {
                        title: form.title.trim(),
                        body: form.body,
                        button_label: form.button_label.trim(),
                      })
                      : api.createOnboardingSection({
                        title: form.title.trim(),
                        body: form.body,
                        button_label: form.button_label.trim(),
                      }),
                    onSaved,
                  )}>
            {saving.busy ? 'Сохраняем…' : 'Сохранить'}
          </button>
        </div>
      </SidePanel>

      {archiving && draft.id && (
        <Confirm
          title="Убрать раздел"
          what={`Раздел «${draft.title}» перестанет показываться сотрудникам.`}
          consequence="Подтверждения тех, кто его уже прочитал, сохранятся: убрать раздел — не то же самое, что стереть факт прочтения."
          confirmLabel="Убрать"
          busy={archive.busy}
          refusal={archive.refusal}
          onCancel={() => setArchiving(false)}
          onConfirm={() => archive.run(
            () => api.archiveOnboardingSection(draft.id as string),
            onSaved,
          )}
        />
      )}
    </>
  );
}

/* --- документы ----------------------------------------------------------- */

function DocumentList({ block, rows, zone, onRetry, onPick }: {
  block: ReturnType<typeof useBlock<{ items: api.PolicyDocument[] }>>[0];
  rows: api.PolicyDocument[];
  zone: string;
  onRetry: () => void;
  onPick: (id: string) => void;
}) {
  return (
    <>
      <div className="toolbar adm-bar">
        <p className="ob-about">
          Эти документы сотрудник подтверждает отдельно от разделов.
          Публикация новой редакции возвращает к подтверждению всех, кто
          её ещё не принял: они получают состояние «Требуется
          ознакомление» и напоминание от бота.
        </p>
      </div>

      {block.state === 'loading' && <Loading />}
      {block.state === 'error' && <Failed onRetry={onRetry} />}
      {block.state === 'ready' && rows.length === 0 && (
        <Empty filtered={false} nothing=""
               none="Документов нет. Наполнение ставится командой seed_onboarding." />
      )}
      {block.state === 'ready' && rows.length > 0 && (
        <ul className="ob-docs">
          {rows.map((row) => (
            <li key={row.id}>
              <button type="button" className="ob-doc" onClick={() => onPick(row.id)}>
                <span className="ob-doc__body">
                  <b className="ob-doc__title">{row.title}</b>
                  <span className="ob-doc__sub">{row.description ?? row.code}</span>
                  <span className="ob-doc__foot">
                    {row.current_version ? (
                      <>
                        Действует редакция {row.current_version.version}
                        {row.current_version.published_at
                          ? ` · с ${moment(row.current_version.published_at, zone, false)}`
                          : ''}
                        {row.current_version.has_file ? ' · с файлом' : ''}
                      </>
                    ) : (
                      <span className="ob-doc__warn">
                        Не опубликован: никого ни к чему не обязывает
                      </span>
                    )}
                  </span>
                </span>
                <AppIcon name="arrow" size={16} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function DocumentPanel({ id, rows, zone, onClose, onChanged }: {
  id: string;
  rows: api.PolicyDocument[];
  zone: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const document = rows.find((one) => one.id === id);
  const [draft, setDraft] = useState<{ version: string; summary: string; body: string; agree_label: string } | null>(null);
  const [publishing, setPublishing] = useState<api.PolicyVersion | null>(null);
  const [showPending, setShowPending] = useState(false);
  const saving = useSaving();
  const publish = useSaving();

  const [pending, reloadPending] = useBlock(
    (signal) => api.policyPending(id, signal),
    `policy-pending|${id}|${showPending}`,
    showPending,
  );

  if (!document) return null;
  const drafts = document.versions.filter((one) => one.status === 'DRAFT');

  return (
    <>
      <SidePanel title={document.title} onClose={onClose}>
        <p className="ob-person__sub">{document.description ?? document.code}</p>

        {document.current_version ? (
          <div className="ob-version">
            <p className="ob-version__head">
              <b>Действующая редакция {document.current_version.version}</b>
              {document.current_version.published_at
                && ` · с ${moment(document.current_version.published_at, zone, false)}`}
            </p>
            <p className="ob-version__summary">{document.current_version.summary}</p>
            {document.current_version.has_file && (
              <a className="link" href={api.policyFileUrl(document.current_version.id)}
                 target="_blank" rel="noreferrer">
                <AppIcon name="doc" size={16} /> {document.current_version.file_name}
              </a>
            )}
          </div>
        ) : (
          <p className="empty">
            Опубликованной редакции нет. Пока её не выпустят, документ
            никого ни к чему не обязывает.
          </p>
        )}

        <div className="adm-side__tools ob-tools">
          <button type="button" className="btn"
                  onClick={() => setShowPending((was) => !was)}>
            {showPending ? 'Скрыть' : 'Кто не подтвердил'}
          </button>
          <button type="button" className="btn btn--primary"
                  onClick={() => setDraft({
                    version: nextVersion(document),
                    summary: document.current_version?.summary ?? '',
                    body: document.current_version?.body ?? '',
                    agree_label: document.current_version?.agree_label ?? 'Согласен',
                  })}>
            <AppIcon name="plus" size={16} /> Новая редакция
          </button>
        </div>

        {showPending && (
          <div className="ob-pending">
            {pending.state === 'loading' && <Loading />}
            {pending.state === 'error' && <Failed onRetry={reloadPending} />}
            {pending.state === 'ready' && pending.data.total === 0 && (
              <p className="empty">Действующую редакцию подтвердили все.</p>
            )}
            {pending.state === 'ready' && pending.data.total > 0 && (
              <ul className="ob-pending__list">
                {pending.data.items.map((one) => (
                  <li key={one.employee_id}>
                    {one.full_name}
                    {one.declined_at && (
                      <span className="ob-pending__no">
                        отказался {moment(one.declined_at, zone, false)}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {drafts.length > 0 && (
          <>
            <h4 className="ob-sub">Черновики</h4>
            <ul className="ob-drafts">
              {drafts.map((one) => (
                <li key={one.id} className="ob-drafts__row">
                  <span>
                    <b>Редакция {one.version}</b>
                    {one.has_file ? ` · ${one.file_name}` : ' · без файла'}
                  </span>
                  <span className="ob-drafts__tools">
                    <FileUpload versionId={one.id} onDone={onChanged} />
                    <button type="button" className="btn btn--primary"
                            onClick={() => setPublishing(one)}>
                      Опубликовать
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}

        {document.versions.length > 1 && (
          <>
            <h4 className="ob-sub">История редакций</h4>
            <ul className="ob-history">
              {document.versions.map((one) => (
                <li key={one.id}>
                  Редакция {one.version} — {VERSION_STATE[one.status] ?? one.status}
                  {one.published_at
                    ? ` · ${moment(one.published_at, zone, false)}`
                    : ''}
                </li>
              ))}
            </ul>
          </>
        )}

        <Refusal text={saving.refusal} />
      </SidePanel>

      {draft && (
        <VersionForm
          draft={draft}
          onChange={setDraft}
          busy={saving.busy}
          refusal={saving.refusal}
          onCancel={() => setDraft(null)}
          onSave={() => saving.run(
            () => api.createPolicyVersion(id, {
              version: draft.version.trim(),
              summary: draft.summary,
              body: draft.body,
              agree_label: draft.agree_label.trim(),
            }),
            () => { setDraft(null); onChanged(); },
          )}
        />
      )}

      {publishing && (
        <Confirm
          title="Опубликовать редакцию"
          what={`Редакция ${publishing.version} документа «${document.title}» станет действующей.`}
          consequence={
            'Всем, кто её не подтвердил, ознакомление откроется заново со '
            + 'состоянием «Требуется ознакомление», и бот начнёт напоминать. '
            + 'Доступ к боту при этом сохраняется, а перечитывать '
            + 'информационные разделы не придётся — только подтвердить этот '
            + 'документ.'
          }
          confirmLabel="Опубликовать"
          busy={publish.busy}
          refusal={publish.refusal}
          onCancel={() => setPublishing(null)}
          onConfirm={() => publish.run(
            () => api.publishPolicyVersion(publishing.id),
            () => { setPublishing(null); onChanged(); },
          )}
        />
      )}
    </>
  );
}

const VERSION_STATE: Record<string, string> = {
  DRAFT: 'черновик',
  PUBLISHED: 'действует',
  ARCHIVED: 'в архиве',
};

/** Следующий номер по умолчанию: 1.0 → 2.0. Правится руками. */
function nextVersion(document: api.PolicyDocument): string {
  const live = document.current_version?.version ?? '0.0';
  const major = Number.parseInt(live.split('.')[0] ?? '0', 10);
  return `${Number.isNaN(major) ? 1 : major + 1}.0`;
}

function VersionForm({ draft, onChange, busy, refusal, onCancel, onSave }: {
  draft: { version: string; summary: string; body: string; agree_label: string };
  onChange: (next: { version: string; summary: string; body: string; agree_label: string }) => void;
  busy: boolean;
  refusal: string | null;
  onCancel: () => void;
  onSave: () => void;
}) {
  const set = (key: keyof typeof draft, value: string) =>
    onChange({ ...draft, [key]: value });

  return (
    <div className="adm-ask" role="dialog" aria-modal="true" aria-label="Новая редакция">
      <div className="adm-ask__box adm-ask__box--wide">
        <h2 className="adm-ask__title">Новая редакция</h2>
        <p className="adm-ask__what">
          Создаётся черновиком. Пока она не опубликована, никого ни к чему
          не обязывает — текст можно править, файл заменять.
        </p>
        <Field label="Номер">
          <input value={draft.version} maxLength={20}
                 onChange={(event) => set('version', event.target.value)} />
        </Field>
        <Field label="Краткий текст в боте"
               hint="То, что человек увидит перед кнопками согласия.">
          <textarea className="ob-area" rows={5} value={draft.summary}
                    onChange={(event) => set('summary', event.target.value)} />
        </Field>
        <Field label="Полный текст"
               hint="Открывается кнопкой «Открыть полный документ». Можно оставить пустым, если приложите PDF.">
          <textarea className="ob-area" rows={12} value={draft.body}
                    onChange={(event) => set('body', event.target.value)} />
        </Field>
        <Field label="Подпись кнопки согласия">
          <input value={draft.agree_label} maxLength={100}
                 onChange={(event) => set('agree_label', event.target.value)} />
        </Field>
        <Refusal text={refusal} />
        <div className="adm-ask__tools">
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            Отмена
          </button>
          <button type="button" className="btn btn--primary" onClick={onSave}
                  disabled={busy || draft.version.trim() === '' || draft.summary.trim() === ''}>
            {busy ? 'Сохраняем…' : 'Сохранить черновик'}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Приложить утверждённый PDF к черновику редакции. */
function FileUpload({ versionId, onDone }: { versionId: string; onDone: () => void }) {
  const saving = useSaving();
  return (
    <label className="btn ob-file">
      {saving.busy ? 'Загружаем…' : 'Приложить PDF'}
      <input type="file" accept="application/pdf" hidden
             onChange={(event) => {
               const file = event.target.files?.[0];
               event.target.value = '';
               if (file) saving.run(() => api.uploadPolicyFile(versionId, file), onDone);
             }} />
    </label>
  );
}

/* --- выгрузка ------------------------------------------------------------ */

/**
 * Выгрузка состояния в CSV.
 *
 * Файл собирается в браузере из того же ответа, что показан на экране:
 * отдельный серверный экспорт означал бы второй способ посчитать одно и
 * то же и расхождение между таблицей и файлом.
 */
function ExportButton() {
  const saving = useSaving();
  return (
    <>
      <button type="button" className="btn" disabled={saving.busy}
              onClick={() => saving.run(async () => {
                const body = await api.onboardingExport();
                download(toCsv(body.items));
              })}>
        <AppIcon name="report" size={16} /> {saving.busy ? 'Готовим…' : 'Выгрузить'}
      </button>
      <Refusal text={saving.refusal} />
    </>
  );
}

const COLUMNS: Array<[string, string]> = [
  ['employee_number', 'Табельный номер'],
  ['full_name', 'Сотрудник'],
  ['office', 'Офис'],
  ['department', 'Отдел'],
  ['position', 'Должность'],
  ['telegram', 'Telegram'],
  ['status', 'Состояние'],
  ['sections', 'Разделы'],
  ['policies', 'Документы'],
  ['completed_at', 'Завершено'],
];

function toCsv(rows: Record<string, string | number | null>[]): string {
  const escape = (value: string | number | null) => {
    const text = value === null || value === undefined ? '' : String(value);
    return /[";\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const head = COLUMNS.map(([, title]) => title).join(';');
  const body = rows.map(
    (row) => COLUMNS.map(([key]) => escape(row[key] ?? null)).join(';'),
  );
  // BOM: без него Excel читает кириллицу в CSV как набор знаков вопроса.
  return `﻿${[head, ...body].join('\n')}`;
}

function download(csv: string): void {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = window.document.createElement('a');
  link.href = url;
  link.download = `ознакомление-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}
