/**
 * Главный экран: где человек сейчас и что у него сегодня.
 *
 * Собран из независимых карточек. Каждая грузится сама и сама же
 * показывает свой сбой: упавшая статистика недели не повод прятать
 * статус, ради которого приложение и открывают.
 *
 * Открытая сессия и «часы сегодня» — разные строки, и сведены они
 * не будут. В три часа ночи у зашедшего в 22:00 сегодняшних часов
 * честно ноль, а в офисе он пять часов; одно число вместо двух соврало
 * бы в одном из мест.
 *
 * Полоса рабочего дня показывает, сколько прошло от смены по часам
 * офиса. Это утверждение о времени суток, а не о выполнении нормы, —
 * и подписана она соответственно.
 */

import { useEffect, useMemo, useState } from 'react';

import type { AbsenceRequest, Day, Note, OpenSession, Status } from '../api';
import { duration, time } from '../format';
import type { Section } from '../sections';
import {
  BarsIcon,
  ChatIcon,
  ChevronRightIcon,
  CorrectionIcon,
  ListIcon,
  MedicalIcon,
  MegaphoneIcon,
  PlaneIcon,
  QrIcon,
  RefreshIcon,
  RingsIcon,
} from '../ui/icons';
import {
  clock,
  firstEntry,
  punches,
  requestLabel,
  requestTone,
  shiftProgress,
  week as weekDays,
  type WeekDay,
} from './home-model';

/** Первое имя из «Фамилия Имя Отчество». Одно слово — оно и есть имя. */
export function firstName(fullName: string): string {
  const words = fullName.trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) return words[1] ?? words[0] ?? '';
  return words[0] ?? '';
}

/** «Доброе утро» до полудня и так далее — по часам офиса. */
export function greeting(hour: number): string {
  if (hour < 6) return 'Доброй ночи';
  if (hour < 12) return 'Доброе утро';
  if (hour < 18) return 'Добрый день';
  return 'Добрый вечер';
}

export interface WeekData {
  days: Day[];
  /** Сессии недели по дням: из них рисуются интервалы. */
  sessions: Record<string, OpenSession[]>;
}

export interface TodayData {
  status: Status;
  sessions: OpenSession[];
}

export function Home({
  fullName,
  office,
  today,
  week,
  requests,
  notes,
  onScan,
  onHistory,
  onRequests,
  onNewRequest,
  onCorrection,
  onQuestion,
  onNote,
  onWeek,
  canCorrect = false,
  canAsk = false,
}: {
  fullName: string;
  office: string;
  today: Section<TodayData>;
  week: Section<WeekData>;
  requests: Section<AbsenceRequest[]>;
  notes: Section<{ unread: number; items: Note[] }>;
  onScan: () => void;
  onHistory: () => void;
  onRequests: () => void;
  onNewRequest: (kind: 'SICK_LEAVE' | 'ANNUAL_LEAVE') => void;
  onCorrection: () => void;
  onQuestion: () => void;
  onNote: (note: Note) => void;
  onWeek: () => void;
  /** Есть ли куда вести плитку исправления. Пока некуда. */
  canCorrect?: boolean;
  /** То же про вопросы. */
  canAsk?: boolean;
}) {
  const status = today.data?.status ?? null;
  const tz = status?.timezone ?? 'UTC';

  return (
    <>
      <section className="hello">
        <h1 className="hello-title">
          {greeting(officeHour(tz))}, {firstName(fullName)}
        </h1>
        {/* Офис подписью, а не переключателем: сотрудник закреплён
            за одним офисом, и права сменить его у него нет. Выпадающий
            список предлагал бы действие, которого не существует. */}
        <p className="hello-office">{office}</p>
      </section>

      <StatusBlock section={today} onScan={onScan} />
      <TodayBlock section={today} onHistory={onHistory} />
      {/* «Сегодня» приходит из статуса — по часам офиса, не телефона.
          Пока его нет, неделю рисовать нечем: без опорной даты каждый
          день оказался бы будущим и вся неделя посерела бы. */}
      <WeekBlock
        section={week}
        today={status?.day ?? null}
        timeZone={tz}
        onDetails={onWeek}
      />

      <section className="card quick">
        <h2 className="card-title">Быстрые действия</h2>
        <div className="quick-grid">
          <QuickAction
            icon={<PlaneIcon size={22} />}
            label="Отпуск"
            onClick={() => onNewRequest('ANNUAL_LEAVE')}
          />
          <QuickAction
            icon={<MedicalIcon size={22} />}
            label="Больничный"
            onClick={() => onNewRequest('SICK_LEAVE')}
          />
          {/* Две плитки выключены, а не ведут в соседний раздел.
              Исправления отметок и вопросов у сотрудника в API нет:
              в `/me/` есть профиль, статус, статистика, история,
              сканирование, отсутствия и уведомления — и всё. Плитка,
              открывающая не то, что обещает, хуже серой. */}
          <QuickAction
            icon={<CorrectionIcon size={22} />}
            label="Исправить отметку"
            onClick={onCorrection}
            disabled={!canCorrect}
            hint="Скоро"
          />
          <QuickAction
            icon={<ChatIcon size={22} />}
            label="Задать вопрос"
            onClick={onQuestion}
            disabled={!canAsk}
            hint="Скоро"
          />
        </div>
      </section>

      <div className="tiles">
        <RequestTile section={requests} onOpen={onRequests} />
        <NoteTile section={notes} onOpen={onNote} />
      </div>
    </>
  );
}

/** Местный час в поясе офиса — не в поясе телефона. */
function officeHour(timeZone: string, now: Date = new Date()): number {
  const text = now.toLocaleString('ru-RU', {
    timeZone,
    hour: '2-digit',
    hour12: false,
  });
  const hour = Number.parseInt(text, 10);
  return Number.isNaN(hour) ? now.getHours() : hour;
}

// --- главная карточка статуса ----------------------------------------------

function StatusBlock({
  section,
  onScan,
}: {
  section: Section<TodayData>;
  onScan: () => void;
}) {
  if (section.loading) return <CardSkeleton lines={3} tall />;
  if (!section.data) {
    return <CardFailure message={section.error} onRetry={section.reload} />;
  }

  const { status, sessions } = section.data;
  const tz = status.timezone;
  const open = status.open_session;
  const inside = status.state === 'IN_OFFICE';
  const shift = shiftProgress(status);
  const first = firstEntry(sessions);

  // Подпись под заголовком — последнее НАСТОЯЩЕЕ событие, а не вывод
  // из состояния: «не в офисе» бывает и до первой отметки, и после
  // выхода, и это разные вещи.
  const lastEntry = status.last_entry_at ? Date.parse(status.last_entry_at) : 0;
  const lastExit = status.last_exit_at ? Date.parse(status.last_exit_at) : 0;
  const lastWasEntry = lastEntry >= lastExit;
  const lastAt = lastWasEntry ? status.last_entry_at : status.last_exit_at;

  return (
    <section className="card status" aria-busy={section.refreshing}>
      <div className="status-head">
        <span
          className={`dot dot-${inside ? 'success' : status.state === 'WORKDAY_MISSED' ? 'warning' : 'idle'}`}
          aria-hidden="true"
        />
        <h2 className="status-title">{inside ? 'В офисе' : 'Не в офисе'}</h2>
        {section.refreshing && <Spinner />}
        {/* Единственный путь к отметке с этого экрана. Ручного входа
            и выхода здесь нет и не будет: отметку делает сканирование,
            а не нажатие на кнопку «я пришёл». */}
        <button type="button" className="status-scan" onClick={onScan}>
          <QrIcon size={18} />
          <span>Отметиться</span>
        </button>
      </div>

      <p className="status-sub">
        {lastAt
          ? `Последний QR: ${lastWasEntry ? 'вход' : 'выход'} в ${time(lastAt, tz)}`
          : 'Отметок по QR ещё не было'}
      </p>

      <dl className="status-metrics">
        <div>
          <dt>Первый вход сегодня</dt>
          <dd>{first ? time(first, tz) : '—'}</dd>
        </div>
        <div>
          <dt>В офисе сегодня</dt>
          <dd>{duration(status.seconds_today)}</dd>
        </div>
      </dl>

      {shift ? (
        <div className="shift">
          <div
            className="shift-track"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={shift.total}
            aria-valuenow={shift.elapsed}
            aria-label={`Прошло ${duration(shift.elapsed * 60)} от смены ${clock(
              status.scheduled_start,
            )}–${clock(status.scheduled_end)}`}
          >
            <span
              className="shift-fill"
              style={{ width: `${(shift.elapsed / shift.total) * 100}%` }}
            />
          </div>
          <div className="shift-legend">
            <span>{clock(status.scheduled_start)}</span>
            <span>{clock(status.scheduled_end)}</span>
          </div>
          <p className="shift-left">
            {shift.left > 0
              ? `До конца дня ${duration(shift.left * 60)}`
              : 'Рабочий день по графику закончился'}
          </p>
        </div>
      ) : (
        <p className="status-empty">{noShift(status)}</p>
      )}

      {open && open.day !== status.day && (
        <p className="status-empty">
          Открытая сессия началась вчера в {time(open.started_at, tz)}.
        </p>
      )}
    </section>
  );
}

/**
 * Почему полосы рабочего дня сегодня нет.
 *
 * Причин три, и они разные. Выходной по графику — график есть, просто
 * сегодня не рабочий день. Отпуск или больничный — то же самое, но по
 * другому поводу, и повод человеку важнее. И только третий случай —
 * когда графика действительно нет.
 *
 * Написать «График не назначен» в выходной значило бы соврать: график
 * назначен, и в понедельник он снова заработает.
 */
export function noShift(status: Status): string {
  if (status.absence_name) return status.absence_name;
  if (status.state === 'DAY_OFF') return 'Сегодня выходной по графику';
  return 'График не назначен';
}

// --- сегодняшние входы и выходы --------------------------------------------

const SHOWN_PUNCHES = 3;

function TodayBlock({
  section,
  onHistory,
}: {
  section: Section<TodayData>;
  onHistory: () => void;
}) {
  if (section.loading) return <CardSkeleton lines={3} />;
  if (!section.data) return null;

  const { status, sessions } = section.data;
  const all = punches(sessions);
  const shown = all.slice(0, SHOWN_PUNCHES);

  return (
    <section className="card" aria-busy={section.refreshing}>
      <button type="button" className="card-head" onClick={onHistory}>
        <h2 className="card-title">Сегодняшние входы и выходы</h2>
        {section.refreshing ? <Spinner /> : <ChevronRightIcon size={20} />}
      </button>

      {shown.length === 0 ? (
        <p className="card-empty">Сегодня отметок ещё нет</p>
      ) : (
        <ol className="log-list">
          {shown.map((punch) => (
            <li key={`${punch.sessionId}-${punch.kind}`}>
              <button type="button" className="log-row" onClick={onHistory}>
                <span
                  className={`dot dot-${punch.kind === 'entry' ? 'success' : 'steel'}`}
                  aria-hidden="true"
                />
                <span className="log-text">
                  <span className="log-title">
                    {punch.kind === 'entry' ? 'Вход' : 'Выход'} ·{' '}
                    {time(punch.at, status.timezone)}
                  </span>
                  <span className="log-place">{punch.point ?? 'Точка не указана'}</span>
                </span>
                <ChevronRightIcon size={20} />
              </button>
            </li>
          ))}
        </ol>
      )}

      <button type="button" className="card-more" onClick={onHistory}>
        <span>Показать все события</span>
        <ChevronRightIcon size={20} />
      </button>
    </section>
  );
}

// --- моя неделя -------------------------------------------------------------

type WeekMode = 'rings' | 'bars' | 'list';

const MODE_KEY = 'humotech.week-mode';

const MODES: Array<{ key: WeekMode; label: string; Icon: typeof RingsIcon }> = [
  { key: 'rings', label: 'Круговые показатели', Icon: RingsIcon },
  { key: 'bars', label: 'Интервалы рабочего времени', Icon: BarsIcon },
  { key: 'list', label: 'Текстовый список', Icon: ListIcon },
];

function readMode(): WeekMode {
  try {
    const saved = localStorage.getItem(MODE_KEY);
    if (saved === 'rings' || saved === 'bars' || saved === 'list') return saved;
  } catch {
    // Хранилище бывает недоступно: приватный режим, отключённые
    // куки. Это не повод падать — режим просто будет по умолчанию.
  }
  return 'rings';
}

function WeekBlock({
  section,
  today,
  timeZone,
  onDetails,
}: {
  section: Section<WeekData>;
  today: string | null;
  timeZone: string;
  onDetails: () => void;
}) {
  const [mode, setMode] = useState<WeekMode>(readMode);

  useEffect(() => {
    try {
      localStorage.setItem(MODE_KEY, mode);
    } catch {
      // См. выше: недоступное хранилище ничего не ломает.
    }
  }, [mode]);

  const days = useMemo(
    () => (section.data && today ? weekDays(section.data.days, today) : []),
    [section.data, today],
  );

  return (
    <section className="card week" aria-busy={section.refreshing}>
      <div className="card-head card-head-static">
        <h2 className="card-title">Моя неделя</h2>
        {section.refreshing && <Spinner />}
        <div className="modes" role="group" aria-label="Вид недели">
          {MODES.map(({ key, label, Icon }) => (
            <button
              key={key}
              type="button"
              className={`mode${key === mode ? ' mode-on' : ''}`}
              aria-label={label}
              aria-pressed={key === mode}
              onClick={() => setMode(key)}
            >
              <Icon size={18} />
            </button>
          ))}
        </div>
      </div>

      {/* Контейнер не пропадает при переключении: меняется только то,
          что внутри. Иначе карточка мигает и страница подпрыгивает. */}
      <div className="week-body">
        {section.loading || !today ? (
          <SkeletonRow />
        ) : !section.data ? (
          <CardFailure message={section.error} onRetry={section.reload} inline />
        ) : (
          <div key={mode} className="week-view">
            {mode === 'rings' && <Rings days={days} />}
            {mode === 'bars' && (
              <Bars
                days={days}
                sessions={section.data.sessions}
                timeZone={timeZone}
              />
            )}
            {mode === 'list' && <WeekList days={days} />}
          </div>
        )}
      </div>

      {/* Месяц и проценты живут на отдельном экране статистики. В макете
          такой ссылки нет, но без неё экран остался бы недостижимым:
          вкладки «Статистика» в панели из четырёх пунктов больше нет. */}
      <button type="button" className="card-more" onClick={onDetails}>
        <span>Подробная статистика</span>
        <ChevronRightIcon size={20} />
      </button>
    </section>
  );
}

/**
 * Подпись под днём: время либо прочерк. Ноль часов — не прочерк.
 *
 * Название отсутствия сюда не идёт: под кольцом на ширине 360 px
 * приходится 42 px, и «Ежегодный отпуск» рвётся на «Ежего дный отпус к».
 * Причина никуда не девается — она в подписи для диктора, во всплывающей
 * подсказке и целиком в текстовом виде недели.
 */
function dayValue(day: WeekDay): string {
  if (day.isFuture || day.norm === null) return '—';
  if (day.absence) return '—';
  if (day.kind === 'off' && day.seconds === 0) return '—';
  return duration(day.seconds);
}

function dayTitle(day: WeekDay): string {
  if (day.isFuture) return 'День ещё не наступил';
  if (day.norm === null) return 'График не назначен';
  if (day.absence) return day.absence;
  if (day.kind === 'off') return 'Выходной по графику';
  return `${duration(day.seconds)} из ${duration(day.norm)}`;
}

const RING = 2 * Math.PI * 15.5;

function Rings({ days }: { days: WeekDay[] }) {
  return (
    <ul className="rings">
      {days.map((day) => (
        <li
          key={day.day}
          className={`ring-cell${day.isToday ? ' ring-cell-today' : ''}`}
          // Причина дня — во всплывающей подсказке: под кольцом для неё
          // нет ширины, а знать её иногда нужно.
          title={dayTitle(day)}
        >
          <span className="ring-label">{day.label}</span>
          <svg
            className={`ring ring-${day.kind}`}
            viewBox="0 0 36 36"
            width={40}
            height={40}
            role="img"
            aria-label={`${day.label}: ${dayTitle(day)}`}
          >
            <circle className="ring-track" cx="18" cy="18" r="15.5" />
            {day.ratio !== null && day.ratio > 0 && (
              <circle
                className="ring-fill"
                cx="18"
                cy="18"
                r="15.5"
                strokeDasharray={`${day.ratio * RING} ${RING}`}
              />
            )}
          </svg>
          <span className="ring-value">{dayValue(day)}</span>
        </li>
      ))}
    </ul>
  );
}

/** Границы полосы интервалов: от самого раннего входа до позднего выхода. */
export function span(
  sessions: Record<string, OpenSession[]>,
  timeZone: string,
): { from: number; to: number } {
  let from = 8 * 60;
  let to = 20 * 60;
  for (const list of Object.values(sessions)) {
    for (const session of list) {
      const start = minutesAt(session.started_at, timeZone);
      const end = session.ended_at ? minutesAt(session.ended_at, timeZone) : start;
      from = Math.min(from, Math.floor(start / 60) * 60);
      to = Math.max(to, Math.ceil(end / 60) * 60);
    }
  }
  return { from, to: Math.max(to, from + 60) };
}

function minutesAt(iso: string, timeZone: string): number {
  const text = new Date(iso).toLocaleTimeString('ru-RU', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
  const [h, m] = text.split(':').map((part) => Number.parseInt(part, 10));
  return Number.isNaN(h) || Number.isNaN(m) ? 0 : h * 60 + m;
}

function Bars({
  days,
  sessions,
  timeZone,
}: {
  days: WeekDay[];
  sessions: Record<string, OpenSession[]>;
  timeZone: string;
}) {
  const { from, to } = span(sessions, timeZone);
  const width = to - from;

  return (
    <div className="bars">
      <ul className="bars-list">
        {days.map((day) => {
          const list = sessions[day.day] ?? [];
          return (
            <li key={day.day} className="bar-row">
              <span className={`bar-day${day.isToday ? ' bar-day-today' : ''}`}>
                {day.label}
              </span>
              <span className="bar-track" title={dayTitle(day)}>
                {list.map((session) => {
                  const start = minutesAt(session.started_at, timeZone);
                  const end = session.ended_at
                    ? minutesAt(session.ended_at, timeZone)
                    : start + Math.round(session.seconds / 60);
                  return (
                    <span
                      key={session.id}
                      className={`bar-piece${session.is_open ? ' bar-piece-open' : ''}`}
                      style={{
                        left: `${((start - from) / width) * 100}%`,
                        width: `${Math.max(((end - start) / width) * 100, 1.5)}%`,
                      }}
                    />
                  );
                })}
              </span>
              <span className="bar-value">{dayValue(day)}</span>
            </li>
          );
        })}
      </ul>
      <div className="bars-legend">
        <span>{stamp(from)}</span>
        <span>{stamp(to)}</span>
      </div>
    </div>
  );
}

function stamp(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

function WeekList({ days }: { days: WeekDay[] }) {
  return (
    <ul className="week-list">
      {days.map((day) => (
        <li key={day.day} className={day.isToday ? 'week-list-today' : undefined}>
          <span className="week-list-day">{day.label}</span>
          <span className="week-list-note">{dayTitle(day)}</span>
          <span className={`week-list-value week-list-${day.kind}`}>
            {dayValue(day)}
          </span>
        </li>
      ))}
    </ul>
  );
}

// --- быстрые действия -------------------------------------------------------

function QuickAction({
  icon,
  label,
  onClick,
  disabled,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  /** Короткая причина недоступности. Читается диктором вместе с меткой. */
  hint?: string;
}) {
  return (
    <button
      type="button"
      className="tile"
      onClick={onClick}
      disabled={disabled}
      aria-label={disabled && hint ? `${label}. ${hint}` : undefined}
    >
      {icon}
      <span className="tile-label">{label}</span>
      {disabled && hint ? (
        <span className="tile-hint">{hint}</span>
      ) : (
        <ChevronRightIcon size={18} />
      )}
    </button>
  );
}

// --- нижние карточки --------------------------------------------------------

/** Заявка, по которой ещё что-то происходит. Иначе показывать нечего. */
export function liveRequest(rows: AbsenceRequest[] | null): AbsenceRequest | null {
  if (!rows?.length) return null;
  const live = rows.filter((row) => row.status !== 'CANCELLED');
  return live[0] ?? null;
}

function RequestTile({
  section,
  onOpen,
}: {
  section: Section<AbsenceRequest[]>;
  onOpen: () => void;
}) {
  if (section.loading) return <div className="mini mini-skeleton" />;
  const request = liveRequest(section.data);
  if (!request) {
    // Пустого места на пол-экрана здесь не будет: карточки нет вовсе.
    return null;
  }

  const tone = requestTone(request.status, request.extension_pending);

  return (
    <button type="button" className="mini" onClick={onOpen}>
      {/* Стрелки здесь нет: карточка и так кнопка целиком, а на 360 px
          стрелка съедала те двадцать пикселей, из-за которых «Ежегодный»
          переставал помещаться в строку и рвался посередине слова. */}
      <span className="mini-head">
        <PlaneIcon size={20} />
        <span className="mini-title">{request.absence_type.name}</span>
      </span>
      <span className="mini-sub">{range(request)}</span>
      <span className={`pill pill-${tone}`}>
        {requestLabel(request.status, request.extension_pending)}
      </span>
    </button>
  );
}

function range(request: AbsenceRequest): string {
  if (!request.first_day) return 'Даты не указаны';
  if (!request.last_day || request.first_day === request.last_day) {
    return human(request.first_day);
  }
  return `${short(request.first_day)}–${human(request.last_day)}`;
}

const MONTHS = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

function human(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  return `${date.getDate()} ${MONTHS[date.getMonth()]}`;
}

function short(iso: string): string {
  return String(new Date(`${iso}T00:00:00`).getDate());
}

function NoteTile({
  section,
  onOpen,
}: {
  section: Section<{ unread: number; items: Note[] }>;
  onOpen: (note: Note) => void;
}) {
  if (section.loading) return <div className="mini mini-skeleton" />;
  const note = section.data?.items?.[0];
  if (!note) return null;

  return (
    <button type="button" className="mini" onClick={() => onOpen(note)}>
      <span className="mini-head">
        <MegaphoneIcon size={20} />
        <span className="mini-title">{note.title ?? 'Объявление'}</span>
        {!note.is_read && <span className="mini-dot" aria-label="Не прочитано" />}
      </span>
      <span className="mini-body">{note.body}</span>
    </button>
  );
}

// --- общие мелочи -----------------------------------------------------------

function Spinner() {
  return (
    <span className="spinner" role="status" aria-label="Обновляем">
      <RefreshIcon size={16} />
    </span>
  );
}

function CardSkeleton({ lines, tall }: { lines: number; tall?: boolean }) {
  return (
    <section className={`card card-skeleton${tall ? ' card-skeleton-tall' : ''}`} aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <span key={index} className="skeleton-line" />
      ))}
    </section>
  );
}

function SkeletonRow() {
  return <span className="skeleton-line skeleton-line-wide" aria-hidden="true" />;
}

function CardFailure({
  message,
  onRetry,
  inline,
}: {
  message: string | null;
  onRetry: () => void;
  inline?: boolean;
}) {
  const body = (
    <>
      <p className="card-empty">{message ?? 'Не удалось загрузить'}</p>
      <button type="button" className="card-retry" onClick={onRetry}>
        <RefreshIcon size={16} />
        <span>Ещё раз</span>
      </button>
    </>
  );
  return inline ? <div className="card-fail">{body}</div> : (
    <section className="card card-fail">{body}</section>
  );
}
