/**
 * Одна заявка: бумаги сотрудника слева, проверка кадровика справа.
 *
 * Отдельная страница, а не шторка поверх очереди. Здесь принимают
 * решение, от которого зависит табель и деньги, и делать это в окне,
 * которое закрывается мимолётным нажатием, нельзя. У страницы есть
 * адрес: ссылку на спорную заявку отправляют коллеге.
 *
 * Разделение ролей выдержано буквально: загрузка файла тут не
 * появляется ни в каком виде. Справку приносит сотрудник из своего
 * приложения — кадровик, подгрузивший её за него, снимает с человека
 * ответственность за то, что он принёс.
 *
 * Отказ по справке и отказ по заявке — разные вещи и стоят в разных
 * местах: первый возвращает бумагу на замену и оставляет заявку живой,
 * второй завершает её. Рядом их не ставят.
 */

import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import * as api from '../api/crm';
import { ApiFailure, reasonFor } from '../api/errors';
import { AppShell } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { DatePicker } from '../components/DatePicker';
import { todayIso } from '../features/employee/model';
import { refreshBadgesSoon } from '../features/shell/badges';
import { placeOf } from '../features/shell/places';
import {
  BAD_STEPS,
  Face,
  FileRow,
  STEP_TITLE,
  calendarDaysWord,
  certificateOf,
  certificateState,
  dateTime,
  dayTime,
  isClosed,
  isOpen,
  kindOf,
  periodOf,
  personOf,
  range,
  shortName,
  size,
  statusOf,
} from '../features/requests/model';
import '../styles/requests.css';

/**
 * Действие кадровика: одно на всю страницу.
 *
 * Пока летит «Отклонить», «Одобрить» нажать нельзя, и двойной клик не
 * отправляет второй запрос: замок ставится синхронно, до перерисовки.
 * Снимается он только после того, как пришла обновлённая заявка, —
 * иначе между ответом на решение и перезапросом на экране стоит старое
 * «на рассмотрении» с живыми кнопками.
 */
export type Act = (
  name: string,
  work: () => Promise<unknown>,
) => Promise<{ ok: true } | { ok: false; error: unknown; message: string }>;

/**
 * Что сказать, когда действие не прошло.
 *
 * Обрыв связи — не «ошибка»: запрос мог дойти, и решение могло
 * примениться. Успеха не показываем, повторно не отправляем — страница
 * перезапрашивает заявку и показывает, что на самом деле.
 */
function actionFailure(error: unknown): { message: string; recheck: boolean } {
  if (error instanceof ApiFailure) {
    if (error.kind === 'offline') {
      return {
        message: 'Связь прервалась, и неизвестно, дошло ли действие до сервера. На странице — актуальное состояние заявки; повторите, только если решение не применилось.',
        recheck: true,
      };
    }
    if (error.kind === 'conflict') {
      return { message: `${reasonFor(error)} Показано актуальное состояние заявки.`, recheck: true };
    }
    if (error.kind === 'session') {
      return { message: `${reasonFor(error)} Показано актуальное состояние заявки.`, recheck: true };
    }
    if (error.kind === 'server') {
      return { message: `${reasonFor(error)} Показано актуальное состояние заявки.`, recheck: true };
    }
  }
  return { message: reasonFor(error), recheck: false };
}

export function RequestPage() {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const [item, setItem] = useState<api.QueueItem | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  /** Перезапрос не удался, но заявка уже на экране: она остаётся. */
  const [stale, setStale] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [acting, setActing] = useState<string | null>(null);
  const lock = useRef(false);
  const current = useRef(id);
  current.current = id;
  /** Чей ответ ждём: смена заявки отменяет перезапрос прошлой. */
  const pending = useRef<AbortController | null>(null);
  const shownId = useRef(id);
  const shown = useRef<api.QueueItem | null>(null);
  shown.current = item;
  /**
   * Где человек работает.
   *
   * В ответе по заявке этого нет: место живёт в карточке сотрудника.
   * Запрашивается отдельно и не задерживает саму заявку — пока места
   * нет, строка просто пустая, а не с прочерком, который читался бы
   * как «нигде не числится».
   */
  const [place, setPlace] = useState<string>('');

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    // Другая заявка — с чистого листа: ни заголовка, ни места, ни
    // ошибки прошлого сотрудника под именем нового. Повтор той же
    // заявки показанное не стирает.
    if (shownId.current !== id) {
      shownId.current = id;
      setItem(null);
      setPlace('');
      setFailed(null);
      setStale(null);
    }
    const stop = new AbortController();
    pending.current?.abort();
    pending.current = stop;
    loadItem(id, stop.signal)
      .then((next) => {
        if (stop.signal.aborted) return;
        setItem(next);
        setFailed(null);
        setStale(null);
        const who = next.absence?.employee?.id;
        if (!who) return;
        api
          .employee(who, stop.signal)
          .then((card) => {
            if (stop.signal.aborted) return;
            const at = (card as { current_assignment?: {
              region_name?: string | null;
              office_name?: string | null;
            } | null }).current_assignment;
            // На одном регионе часто один офис, и названы они одинаково.
            // «Город Ташкент · город Ташкент» не говорит ничего сверх одного.
            const where = [at?.region_name, at?.office_name]
              .filter(Boolean)
              .filter((one, at2, all) => all.indexOf(one) === at2);
            setPlace(where.join(' · '));
          })
          // Место — украшение шапки, а не условие разбора заявки:
          // отказ по нему не должен закрывать страницу.
          .catch(() => undefined);
      })
      .catch((error) => {
        if (stop.signal.aborted) return;
        // Заявка уже на экране — она остаётся, рядом строка «не удалось
        // обновить». Страница целиком падает в ошибку только на первой
        // загрузке, когда показывать нечего.
        if (shown.current) setStale(reasonFor(error));
        else setFailed(reasonFor(error));
      });
    return () => stop.abort();
  }, [id, attempt]);

  /** Перезапросить заявку после действия; ждать ответа. */
  const refresh = useCallback(async (): Promise<void> => {
    const asked = current.current;
    const stop = new AbortController();
    pending.current?.abort();
    pending.current = stop;
    try {
      const next = await loadItem(asked, stop.signal);
      if (stop.signal.aborted || current.current !== asked) return;
      setItem(next);
      setStale(null);
    } catch (error) {
      if (stop.signal.aborted || current.current !== asked) return;
      setStale(reasonFor(error));
    }
  }, []);

  const act: Act = useCallback(async (name, work) => {
    if (lock.current) return { ok: false, error: null, message: '' };
    lock.current = true;
    setActing(name);
    try {
      try {
        await work();
      } catch (error) {
        const { message, recheck } = actionFailure(error);
        if (recheck) await refresh();
        return { ok: false, error, message };
      }
      // Решение показывается только тем, что прислал сервер: заявку
      // перезапрашиваем и ждём, а не красим статус заранее.
      await refresh();
      refreshBadgesSoon();
      return { ok: true };
    } finally {
      lock.current = false;
      setActing(null);
    }
  }, [refresh]);

  if (failed) {
    return (
      <AppShell breadcrumb="Заявки" section="requests">
        <div className="rq rq-one">
          <p className="rq-empty rq-empty--bad">{failed}</p>
          <button type="button" className="rq-btn rq-btn--light" onClick={retry}>Повторить</button>
          <Link className="rq-back" to={placeOf('/requests')}>
            <AppIcon name="back" size={18} />
            Заявки
          </Link>
        </div>
      </AppShell>
    );
  }

  if (!item) {
    return (
      <AppShell breadcrumb="Заявки" section="requests">
        <div className="rq rq-one">
          <span className="visually-hidden" role="status">Загружаем заявку…</span>
          <RequestSkeleton />
        </div>
      </AppShell>
    );
  }

  const person = personOf(item);
  const kind = kindOf(item);
  const state = statusOf(item);

  return (
    <AppShell breadcrumb="Заявки" section="requests">
      <div className="rq rq-one">
        {/* Путь стоит внутри карточки: наверху он говорит, в каком
            разделе человек, здесь — на какой именно заявке. */}
        <p className="rq-one__crumbs">
          <Link to="/">Рабочее пространство</Link>
          <i>/</i>
          {/* Назад — в очередь с теми же вкладкой и фильтрами, с какими
              из неё ушли, а не в пустой список. */}
          <Link to={placeOf('/requests')}>Заявки</Link>
          <i>/</i>
          <b>{kind.title}</b>
        </p>
        <header className="rq-one__head">
          <div>
            <h1 className="rq-head__title">{kind.title}</h1>
          </div>
          {person?.id && (
            <button
              type="button"
              className="rq-btn rq-btn--profile"
              onClick={() => navigate(`/employees/${person.id}`)}
            >
              <AppIcon name="user" size={20} />
              Открыть профиль
            </button>
          )}
        </header>

        {stale && (
          <p className="rq-stale" role="status">
            <AppIcon name="alert" size={18} />
            <span>Не удалось обновить заявку: {stale} Показано последнее полученное состояние.</span>
            <button type="button" className="rq-link" onClick={retry}>Повторить</button>
          </p>
        )}

        <section className="rq-who" aria-label="Сотрудник">
          <Face id={person?.id ?? ''} name={person?.full_name ?? ''} className="rq-who__face" />
          <div className="rq-who__name">
            <b>{shortName(person?.full_name)}</b>
            {place && <small>{place}</small>}
          </div>
          <span className="rq-who__split" aria-hidden="true" />
          <div className="rq-who__when">
            <small>Дата подачи</small>
            <b>{item.absence?.submitted_at ? dateTime(item.absence.submitted_at) : '—'}</b>
          </div>
          {/* Состояние с пояснением под ним: одно слово отвечает «что
              сейчас», вторая строка — «чем это кончится». «Нужны
              исправления» без неё читается как отказ. */}
          <div className={`rq-mark rq-mark--${state.tone}`}>
            <AppIcon name={state.tone === 'green' ? 'check' : 'alert'} size={18} />
            <span>
              <b>{state.title}</b>
              <small>{stateNote(item)}</small>
            </span>
          </div>
        </section>

        <div className="rq-one__grid">
          <Materials item={item} />
          {/* Ключ — заявка: открытый пункт, комментарий и отметки не
              переезжают на другую заявку. */}
          <Review key={item.id} item={item} act={act} acting={acting} />
        </div>

        <Finish key={item.id} item={item} act={act} acting={acting} />
      </div>
    </AppShell>
  );
}

/** Заявка как строка очереди: те же функции разбора, что и в списке. */
async function loadItem(id: string, signal: AbortSignal): Promise<api.QueueItem> {
  const absence = await api.absenceRequest(id, signal);
  // Страница говорит на языке очереди: та же обёртка, те же
  // функции разбора. Две формы одного объекта разошлись бы.
  return { kind: 'absence', id, created_at: absence.submitted_at ?? '', place: null, absence };
}

/** Заглушка страницы заявки по форме настоящей: путь, шапка, сотрудник, два столбца. */
function RequestSkeleton() {
  return (
    <div className="rq-sk-page" aria-hidden="true">
      <i className="rq-sk rq-sk--crumbs" />
      <i className="rq-sk rq-sk--title" />
      <div className="rq-sk-who">
        <i className="rq-sk rq-sk--face" />
        <span><i className="rq-sk rq-sk--line" /><i className="rq-sk rq-sk--short" /></span>
      </div>
      <div className="rq-one__grid">
        {[0, 1].map((n) => (
          <div key={n} className="rq-card rq-sk-card">
            <i className="rq-sk rq-sk--line" />
            <i className="rq-sk rq-sk--block" />
            <i className="rq-sk rq-sk--short" />
            <i className="rq-sk rq-sk--block" />
          </div>
        ))}
      </div>
    </div>
  );
}

// --- материалы сотрудника ------------------------------------------------------

function Materials({ item }: { item: api.QueueItem }) {
  const paper = certificateOf(item);
  const closed = isClosed(item);
  const steps = item.absence?.history ?? [];

  return (
    <section className="rq-card" aria-label="Материалы сотрудника">
      <h2 className="rq-card__title">Материалы сотрудника</h2>

      {/* Бланк для печати. У закрытой заявки его нет: подписанное
          заявление по отклонённой потом всплывает в переписке как
          действующее. */}
      {!closed && (
        <div className="rq-part">
          <FileRow
            tone="blue"
            title={`Заявление на ${(item.absence?.absence_type?.name ?? 'отсутствие').toLowerCase()}`}
            note={applicationNote(item)}
            href={api.absenceApplicationUrl(item.id)}
            action="Скачать"
          />
        </div>
      )}

      {item.absence?.requires_document && (
        <div className="rq-part">
          <h3>Справка</h3>
          {paper ? (
            <div className="rq-paper">
              <FileRow
                tone={paper.verification_status === 'REJECTED' ? 'red' : 'blue'}
                title={paper.file.name}
                note={`${size(paper.file.size_bytes)} · ${dateTime(paper.file.uploaded_at)}`}
                href={api.absenceDocumentUrl(item.id, paper.id)}
                download={paper.file.name}
                action="Скачать"
              />
              {paper.verification_status === 'REJECTED' && (
                /* Не тревожный баннер, а строка под файлом: справку не
                   выбрасывают, за ней приходят снова. */
                <div className="rq-refused" role="status">
                  <AppIcon name="alert" size={18} />
                  <span>
                    <b>Справка не принята</b>
                    {paper.verification_comment && (
                      <small>HR: {paper.verification_comment}</small>
                    )}
                  </span>
                </div>
              )}
              {paper.verification_status === 'VERIFIED' && (
                <p className="rq-ok">
                  <AppIcon name="check" size={18} />
                  Справка принята
                </p>
              )}
            </div>
          ) : (
            /* Справки нет — но место под неё видно сразу, в такой же
               рамке, как у приложенной. Строчка серого текста на этом
               месте читалась как примечание к заявлению выше, а не как
               состояние самой справки. */
            <div className="rq-wait">
              <span className="rq-wait__mark" aria-hidden="true">
                <AppIcon name="doc" size={20} />
              </span>
              <span className="rq-wait__body">
                <b>{closed ? 'Справка приложена не была' : 'Справка пока не приложена'}</b>
                <small>
                  {closed
                    ? 'Заявку закрыли без неё.'
                    : 'Сотрудник может загрузить её в Mini App.'}
                </small>
              </span>
            </div>
          )}
        </div>
      )}

      <div className="rq-part">
        <h3>История заявки</h3>
        {/* Сначала последнее: разбирают заявку с того, что случилось
            только что, а не с её создания. Дальше — прокруткой: лента
            неизменяемая и может быть длинной. */}
        <ol className="rq-time">
          {[...steps].reverse().map((step, at) => (
            <li key={`${step.at}-${at}`} className={BAD_STEPS.has(step.action) ? 'is-bad' : ''}>
              <time>{dayTime(step.at)}</time>
              <span>
                <b>
                  {STEP_TITLE[step.action] ?? step.action}
                  {step.actor && <i> · {step.actor}</i>}
                </b>
                {step.comment && <small>{step.comment}</small>}
              </span>
            </li>
          ))}
          {steps.length === 0 && <li className="rq-time__none">Событий пока нет.</li>}
        </ol>
      </div>
    </section>
  );
}

/** Где человек числится: офис и отдел одной строкой. */
/**
 * Пояснение под состоянием заявки.
 *
 * «Нужны исправления» само по себе читается как отказ. Вторая строка
 * говорит то, чего в слове нет: заявка жива и лежит на рассмотрении.
 */
function stateNote(item: api.QueueItem): string {
  if (!isOpen(item)) return 'Решение по заявке принято';
  return 'Заявка на рассмотрении';
}

/** Подпись под бланком: имя файла и когда его собрали. */
function applicationNote(item: api.QueueItem): string {
  const at = item.absence?.submitted_at;
  if (!at) return 'Шаблон отправлен сотруднику в Telegram';
  const day = new Date(at);
  const name = [
    String(day.getDate()).padStart(2, '0'),
    String(day.getMonth() + 1).padStart(2, '0'),
    day.getFullYear(),
  ].join('-');
  // Веса здесь нет намеренно: бланк собирается на каждое обращение и
  // файлом нигде не лежит. Назвать размер значило бы его выдумать.
  return `Заявление_${name}.pdf · ${dateTime(at)}`;
}

// --- проверка кадровика ---------------------------------------------------------

function Review({ item, act, acting }: { item: api.QueueItem; act: Act; acting: string | null }) {
  const [open, setOpen] = useState<'paper' | 'period' | 'refuse' | null>(null);
  const busy = acting !== null;
  const [failed, setFailed] = useState<string | null>(null);
  const [comment, setComment] = useState('');
  const [first, setFirst] = useState('');
  const [last, setLast] = useState('');
  const [marks, setMarks] = useState<string[] | null>(null);
  const [approveNote, setApproveNote] = useState('');
  /**
   * Окно с причиной отказа по справке.
   *
   * Причину спрашивают отдельным окном, а не полем под кнопкой: текст
   * уходит сотруднику в чат, и писать его между делом, не отрываясь от
   * списка пунктов, — значит писать небрежно.
   */
  const [asking, setAsking] = useState(false);

  const absence = item.absence;
  const paper = certificateOf(item);
  const cert = certificateState(item);
  const missing = absence?.missing_for_approval ?? [];
  const live = isOpen(item);
  const vacation = !absence?.requires_document;
  const period = periodOf(item);
  const leave = absence?.leave_balance ?? null;
  const clash = absence?.overlap ?? null;
  /**
   * Период кадровик посмотрел.
   *
   * Признак — запись в истории, та же, по которой сервер считает
   * перенесённым период больничного. Отдельного поля нет намеренно:
   * «кто и когда поставил» и так записано, и второе место для того же
   * факта однажды разойдётся с первым.
   */
  const periodSeen = (absence?.history ?? []).some(
    (step) => step.action === 'PERIOD_SET',
  );

  /** Подпись кнопки на время её собственного действия. */
  const label = (name: string, idle: string, working: string) => (acting === name ? working : idle);

  async function run(name: string, work: () => Promise<unknown>): Promise<boolean> {
    setFailed(null);
    const result = await act(name, work);
    if (result.ok) {
      setOpen(null);
      setComment('');
      return true;
    }
    if (result.message) setFailed(result.message);
    return false;
  }

  async function approve(overrideMarks = false) {
    if (overrideMarks && !approveNote.trim()) {
      setFailed('Напишите, почему больничный утверждается поверх отметок.');
      return;
    }
    setFailed(null);
    const result = await act('approve', () =>
      api.decideAbsence(item.id, 'approve', approveNote.trim(), overrideMarks));
    if (result.ok) {
      setMarks(null);
      return;
    }
    const { error } = result;
    // Отметки внутри периода — не сбой, а вопрос к кадровику: список
    // дней считает сервер, и второго мнения о том, работал человек
    // или нет, у страницы быть не должно.
    if (error instanceof ApiFailure && error.reason === 'attendance_conflict') {
      setMarks((Array.isArray(error.details.days) ? error.details.days : []).map(String));
      setFailed(error.detail);
      return;
    }
    if (result.message) setFailed(result.message);
  }

  return (
    <section className="rq-card rq-card--side" aria-label="Проверка HR">
      <h2 className="rq-card__title">Проверка HR</h2>

      <ul className="rq-steps">
        {!vacation && (
          <Step
            done={cert.tone === 'green'}
            bad={cert.tone === 'red'}
            title="Справка"
            value={cert.title}
            can={live && paper?.verification_status === 'PENDING'}
            onOpen={() => setOpen(open === 'refuse' ? null : 'refuse')}
          >
            {open === 'refuse' && paper && (
              /* Два исхода проверки рядом, равной ширины: принять или
                 вернуть на исправление. Третьего у справки нет. */
              <div className="rq-pick">
                <div className="rq-pick__row">
                  <button
                    type="button"
                    className="rq-pick__btn rq-pick__btn--take"
                    disabled={busy}
                    aria-busy={acting === 'accept'}
                    onClick={() =>
                      void run('accept', () => api.decideAbsenceDocument(item.id, paper.id, 'accept'))
                    }
                  >
                    {label('accept', 'Принять справку', 'Принимаем…')}
                  </button>
                  <button
                    type="button"
                    className="rq-pick__btn rq-pick__btn--fix"
                    disabled={busy}
                    onClick={() => { setFailed(null); setAsking(true); }}
                  >
                    Отправить на исправление
                  </button>
                </div>
                <p className="rq-pick__hint">
                  При исправлении укажите комментарий сотруднику.
                </p>
              </div>
            )}
          </Step>
        )}
        {!vacation && (
          <Step
            done={Boolean(absence?.application_received_at)}
            title="Подписанное заявление"
            value={
              absence?.application_received_at
                ? `Подтверждено ${dateTime(absence.application_received_at, true)}`
                : 'Не подтверждено'
            }
            can={live}
            onOpen={() => setOpen(open === 'paper' ? null : 'paper')}
          >
            {open === 'paper' && (
              <div className="rq-act">
                <p>Подписанное заявление пришло на почту HR?</p>
                <div className="rq-act__row">
                  <button
                    type="button"
                    className="rq-pick__btn rq-pick__btn--take"
                    disabled={busy}
                    aria-busy={acting === 'received'}
                    onClick={() => void run('received', () => api.markApplicationReceived(item.id, true))}
                  >
                    {label('received', 'Получено', 'Сохраняем…')}
                  </button>
                  {absence?.application_received_at && (
                    <button
                      type="button"
                      className="rq-pick__btn"
                      disabled={busy}
                      aria-busy={acting === 'unreceived'}
                      onClick={() => void run('unreceived', () => api.markApplicationReceived(item.id, false))}
                    >
                      {label('unreceived', 'Снять отметку', 'Сохраняем…')}
                    </button>
                  )}
                </div>
              </div>
            )}
          </Step>
        )}
        <Step
          done={vacation ? periodSeen : !missing.includes('period')}
          title={vacation ? 'Период' : 'Фактические даты'}
          value={
            !vacation && missing.includes('period')
              ? cert.tone === 'red'
                ? 'Недоступны до принятия справки'
                : 'Не указаны'
              : period.days === null
                ? 'Не указан'
                : `${period.long} · ${period.days} ${calendarDaysWord(period.days)}`
          }
          can={live && cert.tone !== 'red'}
          onOpen={() => setOpen(open === 'period' ? null : 'period')}
        >
          {open === 'period' && (
              <div className="rq-act">
                <p>
                  {vacation
                    ? 'Оставьте даты сотрудника или поставьте свои — они и станут периодом в табеле.'
                    : 'Перенесите период из справки — он и станет периодом в табеле.'}
                </p>
                {/* Согласиться с уже выбранным — отдельное действие, а
                    не «перебить те же даты руками»: чаще всего кадровик
                    именно соглашается. */}
                {vacation && item.absence?.first_day && item.absence?.last_day && (
                  <button
                    type="button"
                    className="rq-pick__btn rq-pick__btn--take"
                    disabled={busy}
                    aria-busy={acting === 'keep-period'}
                    onClick={() => void run('keep-period', () =>
                      api.setAbsencePeriod(item.id, {
                        first_day: item.absence!.first_day!,
                        last_day: item.absence!.last_day!,
                      }),
                    )}
                  >
                    {label('keep-period', 'Оставить этот период', 'Сохраняем…')}
                  </button>
                )}
                {/* Календарь системы, а не поле браузера: там свой
                    вид, своя раскладка и «mm/dd/yyyy» вместо нашего
                    порядка дат. Концы периода подсвечивают друг друга,
                    поэтому обе даты знают про соседнюю. */}
                <div className="rq-act__dates">
                  <div>
                    <span>С какого дня</span>
                    <DatePicker
                      label="Период отсутствия: с какого дня"
                      value={first}
                      now={todayIso()}
                      onChange={setFirst}
                      allowEmpty
                      {...(last ? { max: last } : {})}
                      {...(first && last ? { rangeStart: first, rangeEnd: last } : {})}
                    />
                  </div>
                  <div>
                    <span>По какой день</span>
                    <DatePicker
                      label="Период отсутствия: по какой день"
                      value={last}
                      now={todayIso()}
                      onChange={setLast}
                      allowEmpty
                      {...(first ? { min: first } : {})}
                      {...(first && last ? { rangeStart: first, rangeEnd: last } : {})}
                    />
                  </div>
                </div>
                <button
                  type="button"
                  className={vacation ? 'rq-pick__btn' : 'rq-pick__btn rq-pick__btn--take'}
                  disabled={busy || !first || !last}
                  aria-busy={acting === 'period'}
                  onClick={() => void run('period', () =>
                    api.setAbsencePeriod(item.id, { first_day: first, last_day: last }),
                  )}
                >
                  {acting === 'period'
                    ? 'Сохраняем…'
                    : vacation ? 'Поставить другой период' : 'Подтвердить период'}
                </button>
              </div>
          )}
        </Step>

        {/* Остаток и пересечения — не действия, а показания: их считает
            сервер, и нажимать здесь не на что. Кадровику они нужны до
            решения, а не отказом в момент подтверждения. */}
        {vacation && (
          <Step
            done={Boolean(leave?.enough)}
            bad={Boolean(leave) && !leave?.enough}
            title="Баланс отпуска"
            value={leaveWords(leave)}
            can={false}
            onOpen={() => {}}
          />
        )}
        {vacation && (
          <Step
            done={period.days !== null && !clash}
            bad={Boolean(clash)}
            title="Пересечения"
            value={
              clash
                ? `${clash.absence_type_name}: ${range(clash.first_day, clash.last_day)}`
                : period.days === null
                  ? 'Проверим по периоду'
                  : 'Не обнаружены'
            }
            can={false}
            onOpen={() => {}}
          />
        )}
      </ul>

      {asking && paper && (
        <FixDialog
          busy={busy}
          sending={acting === 'fix'}
          comment={comment}
          failed={failed}
          onComment={setComment}
          onClose={() => { setAsking(false); setFailed(null); }}
          onSend={() => {
            if (!comment.trim()) {
              setFailed('Напишите, что не так со справкой — это уйдёт сотруднику.');
              return;
            }
            void (async () => {
              setFailed(null);
              const result = await act('fix', () =>
                api.decideAbsenceDocument(item.id, paper.id, 'reject', comment.trim()));
              if (result.ok) {
                setAsking(false);
                setOpen(null);
                setComment('');
                return;
              }
              // Ошибку в тексте исправляют в окне. Неизвестный исход и
              // конфликт — уже про заявку, а не про текст: окно
              // закрывается, сообщение и свежие данные — на странице.
              const known = result.error instanceof ApiFailure && result.error.kind === 'validation';
              if (!known) setAsking(false);
              if (result.message) setFailed(result.message);
            })();
          }}
        />
      )}

      {failed && !asking && <p className="rq-alert" role="alert">{failed}</p>}

      {marks && marks.length > 0 && (
        <div className="rq-marks" role="alert">
          <h4>
            <AppIcon name="alert" size={18} />
            В эти дни сотрудник отмечался
          </h4>
          <ul>{marks.map((day) => <li key={day}>{day}</li>)}</ul>
          <textarea
            className="input"
            rows={2}
            value={approveNote}
            aria-label="Причина решения поверх отметок"
            placeholder="Почему больничный утверждается поверх отметок"
            onChange={(event) => setApproveNote(event.target.value)}
          />
          <button type="button" className="btn" disabled={busy} aria-busy={acting === 'approve'}
                  onClick={() => void approve(true)}>
            {label('approve', 'Утвердить поверх отметок', 'Утверждаем…')}
          </button>
        </div>
      )}

      {live && (
        <>
          <p className="rq-hint">
            <AppIcon name="info" size={18} />
            {cert.tone === 'red'
              ? 'Сотрудник должен заменить справку. После этого продолжите проверку.'
              : vacation
                ? 'Одобрение доступно, пока заявка на согласовании.'
                : paper?.verification_status === 'PENDING'
                  /* Пока справка не разобрана, всё остальное ждёт её:
                     подсказка называет ближайший шаг, а не общее
                     правило про три пункта. */
                  ? 'Проверьте справку: примите её или отправьте сотруднику на исправление.'
                  : missing.length === 0
                    /* Обещать «станет доступно», когда уже доступно, —
                       значит заставить искать несуществующий пункт. */
                    ? 'Все пункты пройдены — больничный можно подтвердить.'
                    : 'Одобрение станет доступно после выполнения трёх пунктов.'}
          </p>
          <button
            type="button"
            className="rq-btn rq-btn--approve"
            disabled={busy || missing.length > 0}
            aria-busy={acting === 'approve'}
            onClick={() => void approve()}
          >
            <AppIcon name="check" size={20} />
            {label('approve', 'Одобрить', 'Одобряем…')}
          </button>
          {missing.length > 0 && (
            <p className="rq-foot-note">
              Все пункты проверки должны быть подтверждены.
            </p>
          )}
        </>
      )}

      {/*
        * Период внизу столбца.
        *
        * Под кнопкой оставалось пустое место, а главное число заявки
        * приходилось вычитывать из строки проверки. Здесь оно стоит
        * крупно и рядом с решением: со справкой слева сверяют его, а
        * правят в пункте «Фактические даты» выше.
        */}
      <footer className="rq-span">
        <span className="rq-span__mark" aria-hidden="true">
          <AppIcon name="calendar" size={20} />
        </span>
        <span className="rq-span__body">
          <small>Период</small>
          {period.days !== null ? (
            <b>
              {period.long}
              <i>{period.days} {calendarDaysWord(period.days)}</i>
            </b>
          ) : (
            <b className="rq-span--none">Даты не указаны</b>
          )}
        </span>
      </footer>

      {!live && (
        <p className="rq-hint">
          <AppIcon name="info" size={18} />
          Решение по заявке принято. Остались только история и причина.
        </p>
      )}
    </section>
  );
}

/**
 * Причина, по которой справку возвращают.
 *
 * Отдельным окном, а не полем в столбце проверки: текст уходит
 * сотруднику в чат и остаётся в истории заявки — это письмо, а не
 * пометка для себя. Окно закрывается щелчком по затемнению и Esc.
 */
function FixDialog({ busy, sending, comment, failed, onComment, onClose, onSend }: {
  busy: boolean;
  sending: boolean;
  comment: string;
  failed: string | null;
  onComment: (value: string) => void;
  onClose: () => void;
  onSend: () => void;
}) {
  const [leaving, setLeaving] = useState(false);
  const left = useRef(false);

  /**
   * Закрытие с короткой обратной анимацией. Повторное закрытие ничего
   * не делает, а снятие окна подстраховано таймером: затемнение не
   * останется висеть, даже если конец анимации не придёт (вкладка
   * скрыта, анимации отключены). Во время отправки окно не закрывается —
   * ответ сервера ещё не известен.
   */
  const close = useCallback(() => {
    if (left.current || sending) return;
    left.current = true;
    const quiet = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (quiet) {
      onClose();
      return;
    }
    setLeaving(true);
    window.setTimeout(onClose, 160);
  }, [onClose, sending]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [close]);

  // Фокус возвращается туда, откуда окно открыли. Кнопку запоминаем
  // ещё при отрисовке: к эффекту фокус уже у поля с autoFocus.
  const [opener] = useState(() => document.activeElement as HTMLElement | null);
  useEffect(() => () => {
    if (opener && opener.isConnected) opener.focus();
  }, [opener]);

  return (
    <div
      className={leaving ? 'rq-ask rq-ask--leaving' : 'rq-ask'}
      role="dialog"
      aria-modal="true"
      aria-label="Отправить справку на исправление"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div className="rq-ask__box">
        <header className="rq-ask__head">
          <h2>Отправить на исправление</h2>
          <button type="button" className="rq-ask__x" aria-label="Закрыть" onClick={close}
                  disabled={sending}>
            <AppIcon name="close" size={18} />
          </button>
        </header>
        <p className="rq-ask__note">
          Напишите, что не так со справкой. Это уйдёт сотруднику в чат — по
          этому тексту он поймёт, что переснять.
        </p>
        <textarea
          className="input"
          rows={4}
          value={comment}
          autoFocus
          readOnly={sending}
          aria-label="Причина отказа по справке"
          placeholder="В справке не видны даты периода…"
          onChange={(event) => onComment(event.target.value)}
        />
        {failed && <p className="rq-ask__bad" role="alert">{failed}</p>}
        <div className="rq-ask__tools">
          <button type="button" className="rq-btn rq-btn--light" disabled={busy}
                  onClick={close}>
            Отмена
          </button>
          <button type="button" className="rq-pick__btn rq-pick__btn--fix" disabled={busy}
                  aria-busy={sending} onClick={onSend}>
            {sending ? 'Отправляем…' : 'Отправить на исправление'}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Остаток отпуска словами.
 *
 * Считается в рабочих днях: суббота, попавшая в отпуск, остаток не
 * тратит, и сравнивать с ним календарные дни было бы враньём.
 */
function leaveWords(leave: api.AbsenceRow['leave_balance']): string {
  if (!leave) return '—';
  if (leave.available_days === null) return 'Остаток не начислен';
  if (leave.enough) return `Достаточно: ${leave.needed_days} из ${leave.available_days}`;
  return `Не хватает ${leave.needed_days - leave.available_days} дн.`;
}

function Step({ done, bad, title, value, can, onOpen, children }: {
  done: boolean;
  bad?: boolean;
  title: string;
  value: string;
  can: boolean;
  onOpen: () => void;
  children?: ReactNode;
}) {
  const tone = done ? 'done' : bad ? 'bad' : 'wait';
  const body = (
    <>
      <AppIcon name={done ? 'check' : bad ? 'alert' : 'clock'} size={20} />
      <b>{title}</b>
      <span>{value}</span>
      {can && <AppIcon name="next" size={18} className="rq-step__go" />}
    </>
  );
  return (
    <li className={`rq-step rq-step--${tone}`}>
      {can ? (
        <button type="button" onClick={onOpen}>{body}</button>
      ) : (
        <span className="rq-step__flat">{body}</span>
      )}
      {children}
    </li>
  );
}

// --- финальное отклонение --------------------------------------------------------

function Finish({ item, act, acting }: { item: api.QueueItem; act: Act; acting: string | null }) {
  const [asking, setAsking] = useState(false);
  const [comment, setComment] = useState('');
  const [failed, setFailed] = useState<string | null>(null);
  const busy = acting !== null;

  if (!isOpen(item)) return null;

  async function refuse() {
    if (!comment.trim()) {
      setFailed('Напишите причину отклонения — она уйдёт сотруднику.');
      return;
    }
    setFailed(null);
    const result = await act('reject', () => api.decideAbsence(item.id, 'reject', comment.trim()));
    if (!result.ok && result.message) setFailed(result.message);
  }

  return (
    <footer className="rq-finish">
      {asking ? (
        <div className="rq-finish__ask">
          <textarea
            className="input"
            rows={2}
            value={comment}
            readOnly={acting === 'reject'}
            aria-label="Причина отклонения заявки"
            placeholder="Причина отклонения — она уйдёт сотруднику"
            onChange={(event) => setComment(event.target.value)}
          />
          <div className="rq-act__row">
            <button type="button" className="rq-btn rq-btn--danger" disabled={busy}
                    aria-busy={acting === 'reject'} onClick={() => void refuse()}>
              {acting === 'reject' ? 'Отправляем…' : 'Подтвердить отклонение'}
            </button>
            <button type="button" className="btn" disabled={acting === 'reject'}
                    onClick={() => { setAsking(false); setFailed(null); }}>
              Отмена
            </button>
          </div>
          {failed && <p className="rq-alert" role="alert">{failed}</p>}
        </div>
      ) : (
        <>
          <button type="button" className="rq-btn rq-btn--danger" disabled={busy}
                  onClick={() => setAsking(true)}>
            <AppIcon name="trash" size={20} />
            Отклонить заявку
          </button>
          <span className="rq-finish__note">Отклонение заявки завершит процесс.</span>
        </>
      )}
    </footer>
  );
}
