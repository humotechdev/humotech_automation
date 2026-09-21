/**
 * Страница «Заявки»: отпуска, больничные, исправления отметок и отмены.
 *
 * Вёрстка повторяет эталон 1672×941. Размеры — в `styles/requests.css`,
 * классы с префиксом `rq-`.
 *
 * Список и подробности стоят рядом: кадровик разбирает очередь, и терять
 * её из виду на каждой заявке — значит каждый раз искать место, где он
 * остановился. Состояние — в адресе: вкладка, фильтры, страница и
 * открытая заявка переживают обновление и ссылку коллеге.
 *
 * Очередь приходит одним адресом `/requests`: склейка двух списков на
 * клиенте дала бы не очередь, а произвольную смесь её половин.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { messageFor } from '../api/errors';
import { AppShell, initials } from '../components/AppShell';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import { AppSegmentedControl, Dropdown } from '../components/AppSelect';
import { AppDateRangePicker } from '../components/DateRangePicker';
import { formatTime, today, useBlock, type Block } from '../features/dashboard/data';
import '../styles/requests.css';

const OPEN = 'SUBMITTED,IN_REVIEW';

/** Вкладки. Каждая — настоящий фильтр сервера, а не срез показанной страницы. */
const TABS = [
  { key: 'open', title: 'Требуют решения', params: { status: OPEN } as api.QueueQuery },
  { key: 'all', title: 'Все', params: {} as api.QueueQuery },
  { key: 'leave', title: 'Отпуска', params: { kind: 'absence', type: 'ANNUAL_LEAVE,UNPAID_LEAVE', request_kind: 'CREATE,EXTEND' } },
  { key: 'sick', title: 'Больничные', params: { kind: 'absence', type: 'SICK_LEAVE', request_kind: 'CREATE,EXTEND' } },
  { key: 'fixes', title: 'Исправления', params: { kind: 'correction' } },
  { key: 'cancel', title: 'Отмена', params: { kind: 'absence', request_kind: 'CANCEL' } },
] as const;

const STATUS_OPTIONS = [
  { id: OPEN, name: 'На рассмотрении' },
  { id: 'APPROVED', name: 'Одобрена' },
  { id: 'REJECTED', name: 'Отклонена' },
  { id: 'CANCELLED', name: 'Отменена' },
];

const STEP_TITLE: Record<string, string> = {
  CREATED: 'Заявка создана',
  SUBMITTED: 'Заявка подана',
  TAKEN_IN_REVIEW: 'Взята на рассмотрение',
  APPROVED: 'Заявка одобрена',
  REJECTED: 'Заявка отклонена',
  CANCELLED: 'Заявка отменена',
  DOCUMENT_ATTACHED: 'Справка загружена',
  DOCUMENT_VERIFIED: 'Справка проверена',
  COMMENTED: 'Комментарий',
};

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
// Larger cursor pages mean fewer trips through the queue; the list itself
// remains independently scrollable inside its panel.
const PAGE = '20';
const COMMENT_MAX = 500;

export function RequestsPage() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.find((one) => one.key === params.get('tab')) ?? TABS[0];
  const search = params.get('search') ?? '';
  const region = params.get('region_id') ?? '';
  const office = params.get('office_id') ?? '';
  const status = params.get('status') ?? '';
  const from = params.get('date_from') ?? '';
  const to = params.get('date_to') ?? '';
  const cursor = params.get('cursor') ?? '';
  const opened = params.get('request') ?? '';
  // Заявки одного сотрудника: приходит ссылкой с главной, своего поля нет.
  const employee = params.get('employee_id') ?? '';

  const [draft, setDraft] = useState(search);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [ordered, setOrdered] = useState<string | null>(null);
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

  // Общие фильтры — без вкладки: по ним же считаются счётчики вкладок.
  const common: api.QueueQuery = useMemo(
    () => ({
      ...(search ? { search } : {}),
      ...(region ? { region_id: region } : {}),
      ...(office ? { office_id: office } : {}),
      ...(employee ? { employee_id: employee } : {}),
      ...(from ? { date_from: from } : {}),
      ...(to ? { date_to: to } : {}),
    }),
    [search, region, office, employee, from, to],
  );
  const base = `${search}|${region}|${office}|${employee}|${from}|${to}|${attempt}`;

  const [list, reload] = useBlock(
    (signal) =>
      api.queue(
        { ...common, ...tab.params, ...(status ? { status } : {}), limit: PAGE, ...(cursor ? { cursor } : {}) },
        signal,
      ).then((body) => {
        setUpdated(new Date());
        return body;
      }),
    `${tab.key}|${status}|${cursor}|${base}`,
  );

  const [counts] = useBlock((signal) => api.queueCounts(common, signal), `counts|${base}`);

  const [directory] = useBlock(
    (signal) =>
      Promise.all([api.regions(signal), api.offices(signal)]).then(([r, o]) => ({
        regions: r.items,
        offices: o.items.filter((one) => one.status === 'ACTIVE'),
      })),
    'directory',
  );

  const offices = useMemo(() => {
    if (directory.state !== 'ready') return [];
    return region ? directory.data.offices.filter((one) => one.region_id === region) : directory.data.offices;
  }, [directory, region]);

  const items = list.state === 'ready' ? list.data.items : [];
  // Шторка открывается ТОЛЬКО по выбору. Прежде без выбора открывалась
  // первая заявка очереди — это было верно для постоянной колонки
  // рядом со списком, но окно поверх страницы, которое появляется само,
  // закрывает собой очередь, ради которой страницу открыли.
  const current = opened ? items.find((item) => item.id === opened) ?? null : null;
  const first = items[0];
  const employeeName = employee
    ? first?.absence?.employee.full_name ?? first?.correction?.employee?.full_name ?? null
    : null;
  const dirty = Boolean(search || region || office || employee || status || from || to);
  const waiting = counts.state === 'ready' ? counts.data['open'] ?? 0 : null;

  async function order() {
    setOrdered(null);
    try {
      await api.orderExport({
        kind: 'absences', fmt: 'xlsx',
        ...(from ? { date_from: from } : {}),
        ...(to ? { date_to: to } : {}),
        ...(office ? { office_id: office } : region ? { region_id: region } : {}),
      });
      setOrdered('Выгрузка поставлена в очередь');
    } catch (error) {
      setOrdered(messageFor(error));
    }
  }

  return (
    <AppShell breadcrumb="Заявки" section="requests">
      <div className="rq">
        <header className="rq-head">
          <div>
            <h1 className="rq-head__title">Заявки</h1>
            <p className="rq-head__sub">Отпуска, больничные и исправления отметок</p>
            <p className="rq-head__meta">
              {waiting !== null && <span>{waiting} {waitingWord(waiting)} внимания</span>}
              {waiting !== null && updated && <i>·</i>}
              {updated ? <span>обновлено в {formatTime(updated)}</span> : <span>загружаем…</span>}
            </p>
          </div>
          <div className="rq-head__actions">
            <button type="button" className="rq-btn rq-btn--light" onClick={() => setAttempt((n) => n + 1)}>
              <AppIcon name="refresh" size={20} />
              Обновить
            </button>
            <button type="button" className="rq-btn rq-btn--light rq-btn--blue-text" onClick={() => void order()}>
              <AppIcon name="download" size={20} />
              Экспорт
            </button>
          </div>
        </header>

        {ordered && (
          <p className="rq-note" role="status">
            {ordered} — <Link to="/reports">файл появится в отчётах</Link>
          </p>
        )}

        <div className={current ? 'rq-grid rq-grid--open' : 'rq-grid'}>
          <section className="rq-list" aria-label="Очередь заявок">
            <AppSegmentedControl className="rq-tabs" role="tablist" label="Вид заявок" value={tab.key}
              options={TABS.map((item) => ({ value: item.key, label: item.title, count: counts.state === 'ready' && item.key !== 'all' ? counts.data[item.key] ?? 0 : null }))}
              onChange={(key) => patch({ tab: key === 'open' ? null : key, request: null, status: null })} />

            {/* Одна строка отбора. Чипов с офисом, статусом, датами и поиском
                здесь нет: они повторяли то, что и так видно в полях. Метка
                остаётся только у условий без своего поля — сотрудник,
                пришедший по ссылке, и регион, — и рядом «Сбросить». */}
            <div className="rq-filters">
              <label className="rq-search">
                <AppIcon name="search" size={16} />
                <input type="search" value={draft} placeholder="Поиск сотрудника"
                       aria-label="Поиск сотрудника"
                       onChange={(event) => setDraft(event.target.value)} />
              </label>
              <Select label="Офис" empty="Все офисы" value={office} options={offices}
                      onChange={(value) => patch({ office_id: value || null })} />
              <Select label="Статус" empty="Все статусы" value={status} options={STATUS_OPTIONS}
                      onChange={(value) => patch({ status: value || null })} />
              {/* Период фильтрует даты самого отсутствия, а не дату подачи. */}
              <AppDateRangePicker className="rq-dates" label="Даты отсутствия" now={today()} from={from} to={to}
                onFromChange={(value) => patch({ date_from: value || null })}
                onToChange={(value) => patch({ date_to: value || null })} />
              {region && !office && directory.state === 'ready' && (
                <Chip text={directory.data.regions.find((one) => one.id === region)?.name ?? 'Регион'}
                      onClear={() => patch({ region_id: null })} />
              )}
              {employee && (
                <Chip text={employeeName ? `Заявки: ${employeeName}` : 'Заявки одного сотрудника'}
                      onClear={() => patch({ employee_id: null })} />
              )}
              {dirty && (
                <button type="button" className="rq-reset" onClick={() =>
                  patch({ search: null, region_id: null, office_id: null, employee_id: null,
                          status: null, date_from: null, date_to: null })}>
                  Сбросить
                </button>
              )}
            </div>

            <div className="rq-rows">
              {/* Шапка колонок стоит внутри области прокрутки и прилипает к
                  её верху: так у неё та же ширина, что у строк, и полоса
                  прокрутки не сдвигает подписи относительно столбцов. */}
              <div className="rq-cols" aria-hidden="true">
                <span />
                <span>Сотрудник</span>
                <span>Тип заявки</span>
                <span>Период</span>
                <span>Документ</span>
                <span>Подана</span>
                <span>Статус</span>
                <span />
              </div>
              <Rows block={list}>
                {(data) => data.items.length === 0 ? (
                  <p className="rq-empty">{dirty ? 'По этим условиям заявок нет.' : 'Очередь пуста.'}</p>
                ) : (
                  data.items.map((item) => (
                    <Row key={item.id} item={item} on={item.id === current?.id}
                         onOpen={() => patch({ request: item.id }, true)} />
                  ))
                )}
              </Rows>
            </div>

            <footer className="rq-pager">
              <button type="button" className="rq-pager__btn" disabled={!cursor}
                      onClick={() => patch({ cursor: null })}>
                <AppIcon name="back" size={16} />
                В начало
              </button>
              <button type="button" className="rq-pager__btn"
                      disabled={list.state !== 'ready' || !list.data.has_more}
                      onClick={() => list.state === 'ready' && patch({ cursor: list.data.next_cursor }, true)}>
                Далее
                <AppIcon name="next" size={16} />
              </button>
            </footer>
          </section>

          {current && (
            <>
              {/* Затемнение под шторкой: нажатие мимо закрывает её.
                  Кнопкой, а не слоем: закрыть заявку — это действие, и
                  оно должно быть доступно и с клавиатуры. */}
              <button
                type="button"
                className="rq-scrim"
                aria-label="Закрыть заявку"
                onClick={() => patch({ request: null }, true)}
              />
              <Details key={current.id} item={current}
                       onClose={() => patch({ request: null }, true)}
                       onDone={reload} />
            </>
          )}
        </div>
      </div>
    </AppShell>
  );
}

// --- строка ----------------------------------------------------------------------

function Row({ item, on, onOpen }: { item: api.QueueItem; on: boolean; onOpen: () => void }) {
  const person = personOf(item);
  const state = statusOf(item);
  const kind = kindOf(item);
  const doc = documentState(item);
  const period = periodOf(item);
  return (
    <div className={on ? 'rq-row rq-row--on' : 'rq-row'} role="button" tabIndex={0}
         aria-pressed={on} onClick={onOpen}
         onKeyDown={(event) => { if (event.key === 'Enter') onOpen(); }}>
      <Face id={person?.id ?? ''} name={person?.full_name ?? ''} className="rq-row__face" />
      <span className="rq-row__who">
        <b>{shortName(person?.full_name)}</b>
        <small>{item.place?.office_name ?? '—'}</small>
      </span>
      <span className={`rq-row__kind rq-row__kind--${kind.tone}`}>
        <AppIcon name={kind.icon} size={20} />
        <span>{kind.title}</span>
      </span>
      <span className="rq-row__period">
        <span>{period.text}</span>
        {period.days !== null && <small>{period.days} {daysWord(period.days)}</small>}
      </span>
      <span className={`rq-row__doc rq-row__doc--${doc.tone}`}>
        {doc.icon && <AppIcon name={doc.icon} size={20} />}
        <span>{doc.title}</span>
      </span>
      <span className="rq-row__when">{submittedOf(item, true)}</span>
      <span className={`rq-status rq-status--${state.tone}`}>{state.title}</span>
      <AppIcon name="next" size={20} className="rq-row__go" />
    </div>
  );
}

// --- подробности -------------------------------------------------------------------

function Details({ item, onClose, onDone }: {
  item: api.QueueItem;
  onClose: () => void;
  onDone: () => void;
}) {
  // Escape закрывает шторку: она перекрывает часть страницы, и уйти из
  // неё нужно уметь не целясь в крестик.
  useEffect(() => {
    const stop = new AbortController();
    // `window.document`, а не `document`: ниже в этой же функции есть
    // своя переменная `document` — приложенный к заявке файл.
    window.document.addEventListener(
      'keydown',
      (event: KeyboardEvent) => {
        if (event.key === 'Escape') onClose();
      },
      { signal: stop.signal },
    );
    return () => stop.abort();
  }, [onClose]);

  const [comment, setComment] = useState('');
  const [sending, setSending] = useState<'approve' | 'reject' | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  // Решение по справке живёт отдельно от решения по заявке: одобренный
  // больничный с отклонённой справкой — законное состояние.
  const [docComment, setDocComment] = useState('');
  const [docAsking, setDocAsking] = useState(false);
  const [docSending, setDocSending] = useState<'accept' | 'reject' | null>(null);
  const [docFailed, setDocFailed] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const person = personOf(item);
  const state = statusOf(item);
  const kind = kindOf(item);
  const period = periodOf(item);
  const absence = item.absence;
  const document = absence?.documents?.[0];
  const open = ['SUBMITTED', 'IN_REVIEW'].includes(absence?.status ?? item.correction?.status ?? '');
  const reason = absence?.comment ?? item.correction?.reason ?? null;
  const steps = absence?.history ?? [];

  async function decideDocument(decision: 'accept' | 'reject') {
    if (docSending || !document) return;
    // Отказ без причины — тупик: человек принесёт ту же бумагу второй
    // раз и не поймёт, почему её опять не взяли.
    if (decision === 'reject' && !docComment.trim()) {
      setDocFailed('Напишите, что не так со справкой — это уйдёт сотруднику.');
      return;
    }
    setDocSending(decision);
    setDocFailed(null);
    try {
      await api.decideAbsenceDocument(
        item.id, document.id, decision,
        docComment.trim() || undefined,
      );
      setDocAsking(false);
      setDocComment('');
      onDone();
    } catch (error) {
      setDocFailed(messageFor(error));
    } finally {
      setDocSending(null);
    }
  }

  async function decide(decision: 'approve' | 'reject') {
    if (sending) return;
    // Отказ без причины — это заявка, которую подадут снова, и так по кругу.
    if (decision === 'reject' && !comment.trim()) {
      setFailed('Напишите причину отклонения — она уйдёт сотруднику.');
      return;
    }
    setSending(decision);
    setFailed(null);
    try {
      if (item.kind === 'absence') await api.decideAbsence(item.id, decision, comment.trim());
      else await api.decideCorrection(item.id, decision, comment.trim());
      setDone(decision === 'approve' ? 'Заявка одобрена' : 'Заявка отклонена');
      onDone();
    } catch (error) {
      // Комментарий остаётся в поле: набирать его заново из-за сбоя — худшее.
      setFailed(messageFor(error));
    } finally {
      setSending(null);
    }
  }

  return (
    <aside className="rq-side" aria-label="Подробности заявки">
      <header className="rq-side__head">
        <h2>Заявка №{requestNumber(item.id)}</h2>
        <span className={`rq-status rq-status--${state.tone}`}>{state.title}</span>
        <button type="button" className="rq-side__close" aria-label="Закрыть" onClick={onClose}>
          <AppIcon name="close" size={20} />
        </button>
      </header>

      <div className="rq-side__body">
        <div className="rq-person">
          <Face id={person?.id ?? ''} name={person?.full_name ?? ''} className="rq-person__face" />
          <div>
            <p className="rq-person__name">{shortName(person?.full_name)}</p>
            <p className="rq-person__place">
              {item.place?.office_name && <span><AppIcon name="pin" size={16} />{item.place.office_name}</span>}
              {item.place?.department_name && <span><AppIcon name="users" size={16} />{item.place.department_name}</span>}
            </p>
          </div>
        </div>

        <dl className="rq-facts">
          <Fact icon={kind.icon} title="Тип заявки" value={kind.title} />
          <Fact icon="calendar" title={item.kind === 'absence' ? 'Период' : 'Дата отметки'} value={period.long} />
          {period.days !== null && (
            <Fact icon="calendar" title="Количество дней" value={`${period.days} ${calendarDaysWord(period.days)}`} />
          )}
          <Fact icon="clock" title="Подано" value={submittedOf(item, false)} />
        </dl>

        {reason && (
          <section className="rq-block">
            <h3><AppIcon name="doc" size={20} />{item.kind === 'absence' ? 'Причина' : 'Причина исправления'}</h3>
            <p className="rq-block__text">{reason}</p>
          </section>
        )}

        {item.kind === 'absence' && (
          <section className="rq-block">
            <h3><AppIcon name="archive" size={20} />Документ</h3>
            {document ? (
              <div className="rq-doc">
                <div className="rq-doc__card">
                  <span className="rq-doc__type">{fileType(document.file.mime_type)}</span>
                  <span className="rq-doc__text">
                    <b>{document.file.name}</b>
                    <small>{size(document.file.size_bytes)} · Загружен {dateTime(document.file.uploaded_at)}</small>
                    <small className={`rq-doc__state rq-doc__state--${docTone(document.verification_status)}`}>
                      {docTitle(document.verification_status)}
                    </small>
                  </span>
                  <span className="rq-doc__actions">
                    <a href={api.absenceDocumentUrl(item.id, document.id)} target="_blank" rel="noreferrer">
                      <AppIcon name="eye" size={18} />Просмотреть
                    </a>
                    <a href={api.absenceDocumentUrl(item.id, document.id)} download={document.file.name}>
                      <AppIcon name="download" size={18} />Скачать
                    </a>
                  </span>
                </div>

                {/* Решение по бумаге. Пока справка ждёт проверки — две
                    кнопки; отказ спрашивает причину, потому что человеку
                    надо знать, что принести взамен. */}
                {document.verification_status === 'PENDING' && (
                  <div className="rq-doc__decide">
                    {docAsking ? (
                      <>
                        <textarea
                          className="input"
                          rows={2}
                          value={docComment}
                          placeholder="Что не так со справкой — это уйдёт сотруднику"
                          onChange={(event) => setDocComment(event.target.value)}
                        />
                        <span className="rq-doc__decideRow">
                          <button type="button" className="btn btn--danger"
                                  disabled={docSending !== null}
                                  onClick={() => void decideDocument('reject')}>
                            {docSending === 'reject' ? 'Возвращаем…' : 'Вернуть на доработку'}
                          </button>
                          <button type="button" className="btn"
                                  onClick={() => { setDocAsking(false); setDocFailed(null); }}>
                            Отмена
                          </button>
                        </span>
                      </>
                    ) : (
                      <span className="rq-doc__decideRow">
                        <button type="button" className="btn btn--primary"
                                disabled={docSending !== null}
                                onClick={() => void decideDocument('accept')}>
                          {docSending === 'accept' ? 'Принимаем…' : 'Принять справку'}
                        </button>
                        {/* «Вернуть», а не «Отклонить»: справку не
                            выбрасывают, за ней приходят снова — и глагол
                            должен говорить именно это. Заодно он не
                            путается с отклонением самой заявки, которое
                            стоит на этом же экране. */}
                        <button type="button" className="btn"
                                onClick={() => setDocAsking(true)}>
                          Вернуть справку
                        </button>
                      </span>
                    )}
                    {docFailed && (
                      <p className="rq-block__text rq-block__text--warn" role="alert">
                        {docFailed}
                      </p>
                    )}
                  </div>
                )}
                {document.verification_comment && (
                  <p className="rq-block__text">
                    Комментарий к справке: {document.verification_comment}
                  </p>
                )}
                {document.file.mime_type.startsWith('image/') ? (
                  <img className="rq-doc__preview" src={api.absenceDocumentUrl(item.id, document.id)} alt="" />
                ) : (
                  <span className="rq-doc__preview rq-doc__preview--file" aria-hidden="true">
                    <AppIcon name="doc" size={20} />
                    {fileType(document.file.mime_type)}
                  </span>
                )}
              </div>
            ) : absence?.requires_document ? (
              <p className="rq-block__text rq-block__text--warn">Ожидаем справку — документ ещё не предоставлен.</p>
            ) : (
              <p className="rq-block__text">Для этого типа заявки документ не требуется.</p>
            )}
          </section>
        )}

        {(steps.length > 0 || open) && (
          <section className="rq-block">
            <h3><AppIcon name="clock" size={20} />История</h3>
            <ol className="rq-history">
              {steps.map((step, at) => (
                <li key={`${step.at}-${at}`}>
                  <time>{dateTime(step.at, true)}</time>
                  <span>{STEP_TITLE[step.action] ?? step.action}{step.comment ? ` · ${step.comment}` : ''}</span>
                </li>
              ))}
              {open && (
                <li className="rq-history__now">
                  <time>Сейчас</time>
                  <span>Ожидает решения</span>
                </li>
              )}
            </ol>
          </section>
        )}
      </div>

      {/* Решение — внизу шторки и вне её прокрутки: одобрить или
          отклонить можно, не пролистывая документ и историю. */}
      <footer className="rq-side__foot">
        {failed && <p className="rq-alert" role="alert">{failed}</p>}
        {done && <p className="rq-done" role="status">{done}</p>}

        {open ? (
          <div className="rq-decide">
            <label className="rq-comment">
              <AppIcon name="chat" size={20} />
              <input
                type="text"
                value={comment}
                maxLength={COMMENT_MAX}
                placeholder="Комментарий при отклонении (обязательно)"
                aria-label="Комментарий HR"
                onChange={(event) => setComment(event.target.value)}
              />
              <small>{comment.length}/{COMMENT_MAX}</small>
            </label>
            <div className="rq-decide__buttons">
              <button type="button" className="rq-btn rq-btn--reject" disabled={sending !== null}
                      onClick={() => void decide('reject')}>
                <AppIcon name="cross" size={20} />
                {sending === 'reject' ? 'Отправляем…' : 'Отклонить'}
              </button>
              {/* Запроса справки у сотрудника в системе пока нет: кнопка
                  честно неактивна, а не отправляет пустоту. */}
              <button type="button" className="rq-btn rq-btn--light" disabled
                      title="Запрос документа у сотрудника пока не поддерживается">
                <AppIcon name="doc" size={20} />
                Запросить документ
              </button>
              <button type="button" className="rq-btn rq-btn--approve" disabled={sending !== null}
                      onClick={() => void decide('approve')}>
                <AppIcon name="check" size={20} />
                {sending === 'approve' ? 'Отправляем…' : 'Одобрить'}
              </button>
            </div>
          </div>
        ) : (
          <p className="rq-block__text rq-closed">
            Заявка уже рассмотрена. Повторное решение по ней не принимается.
            {(absence?.review_comment ?? item.correction?.review_comment) && (
              <> Комментарий: {absence?.review_comment ?? item.correction?.review_comment}</>
            )}
          </p>
        )}
      </footer>
    </aside>
  );
}

function Fact({ icon, title, value }: { icon: AppIconName; title: string; value: string }) {
  return (
    <div className="rq-fact">
      <AppIcon name={icon} size={20} />
      <span>
        <dt>{title}</dt>
        <dd>{value}</dd>
      </span>
    </div>
  );
}

function Chip({ text, onClear }: { text: string; onClear: () => void }) {
  return (
    <button type="button" className="rq-chip" onClick={onClear} aria-label={`Снять фильтр: ${text}`}>
      {text}
      <AppIcon name="close" size={16} />
    </button>
  );
}

// --- разбор строки -------------------------------------------------------------------

function personOf(item: api.QueueItem) {
  return item.absence?.employee ?? item.correction?.employee ?? null;
}

function kindOf(item: api.QueueItem): { title: string; icon: AppIconName; tone: string } {
  if (item.kind === 'correction') return { title: 'Исправление отметок', icon: 'pencil', tone: 'blue' };
  if (item.absence?.kind === 'CANCEL') return { title: 'Отмена заявки', icon: 'cross', tone: 'red' };
  const code = item.absence?.absence_type?.code;
  if (code === 'SICK_LEAVE') return { title: 'Больничный', icon: 'doc', tone: 'blue' };
  if (code === 'ANNUAL_LEAVE') return { title: 'Ежегодный отпуск', icon: 'send', tone: 'blue' };
  return { title: item.absence?.absence_type?.name ?? 'Отсутствие', icon: 'calendar', tone: 'blue' };
}

function statusOf(item: api.QueueItem): { title: string; tone: string } {
  const status = item.absence?.status ?? item.correction?.status ?? '';
  if (status === 'SUBMITTED' || status === 'IN_REVIEW') {
    return item.absence?.kind === 'CANCEL'
      ? { title: 'Отмена запрошена', tone: 'grey' }
      : { title: 'На рассмотрении', tone: 'orange' };
  }
  if (status === 'APPROVED') return { title: 'Одобрена', tone: 'green' };
  if (status === 'REJECTED') return { title: 'Отклонена', tone: 'red' };
  if (status === 'CANCELLED') return { title: 'Отменена', tone: 'grey' };
  if (status === 'DRAFT') return { title: 'Черновик', tone: 'grey' };
  return { title: status || '—', tone: 'grey' };
}

/**
 * Состояние справки. «Загружена» и «проверена» — разные вещи: документ
 * не считается проверенным только потому, что он есть.
 */
function documentState(item: api.QueueItem): { title: string; icon: AppIconName | null; tone: string } {
  if (item.kind !== 'absence' || item.absence?.kind === 'CANCEL') return { title: '—', icon: null, tone: 'none' };
  const document = item.absence?.documents?.[0];
  if (document) {
    if (document.verification_status === 'REJECTED') return { title: 'Некорректный документ', icon: 'alert', tone: 'red' };
    if (document.verification_status === 'VERIFIED') return { title: 'Справка проверена', icon: 'doc', tone: 'plain' };
    return { title: 'Справка на проверке', icon: 'doc', tone: 'plain' };
  }
  if (item.absence?.requires_document) return { title: 'Нет справки', icon: 'alert', tone: 'orange' };
  return { title: '—', icon: null, tone: 'none' };
}

function periodOf(item: api.QueueItem): { text: string; long: string; days: number | null } {
  const first = item.absence?.first_day;
  const last = item.absence?.last_day ?? first;
  if (first && last) {
    const days = Math.round((Date.parse(`${last}T12:00:00Z`) - Date.parse(`${first}T12:00:00Z`)) / 86_400_000) + 1;
    return { text: range(first, last), long: range(first, last), days };
  }
  const at = item.correction?.requested_entry_at ?? item.correction?.requested_exit_at ?? null;
  if (at) {
    const day = at.slice(0, 10);
    return { text: dayLong(day), long: dayLong(day), days: 1 };
  }
  return { text: '—', long: '—', days: null };
}

function submittedOf(item: api.QueueItem, short: boolean): string {
  const at = item.absence?.submitted_at ?? item.correction?.submitted_at ?? item.created_at;
  return at ? dateTime(at, short) : '—';
}

// --- мелочи ------------------------------------------------------------------------------

function Face({ id, name, className }: { id: string; name: string; className: string }) {
  const [broken, setBroken] = useState(!id);
  if (broken) return <span className={`rq-face rq-face--none ${className}`} aria-hidden="true">{initials(name)}</span>;
  return (
    <img className={`rq-face ${className}`} src={api.employeePhotoUrl(id)} alt=""
         onError={() => setBroken(true)}
         onLoad={(event) => { if (event.currentTarget.naturalWidth < 32) setBroken(true); }} />
  );
}

function Select({ label, empty, value, options, onChange }: {
  label: string;
  empty: string;
  value: string;
  options: { id: string; name: string }[];
  onChange: (value: string) => void;
}) {
  return <Dropdown label={label} empty={empty} value={value} options={options} onChange={onChange} />;
}

function Rows<T>({ block, children }: { block: Block<T>; children: (data: T) => React.ReactNode }) {
  if (block.state === 'loading') return <p className="rq-empty">Загружаем очередь…</p>;
  if (block.state === 'denied') return <p className="rq-empty">Нет доступа к заявкам.</p>;
  if (block.state === 'error') {
    return <p className="rq-empty rq-empty--bad">Не удалось загрузить очередь. Это ошибка запроса, а не «заявок нет».</p>;
  }
  return <>{children(block.data)}</>;
}

function shortName(full: string | null | undefined): string {
  return (full ?? '').split(' ').slice(0, 2).join(' ') || '—';
}

/** Номер для людей: начало идентификатора. По нему заявку находят в журнале. */
function requestNumber(id: string): string {
  return id.replace(/-/g, '').slice(0, 8).toUpperCase();
}

function parts(day: string): [number, number, number] {
  const [y = 0, m = 1, d = 1] = day.split('-').map(Number);
  return [y, m, d];
}

function dayLong(day: string): string {
  const [y, m, d] = parts(day);
  return `${String(d).padStart(2, '0')} ${MONTHS[m - 1] ?? ''} ${y}`;
}


/** «09 – 13 сентября 2026» или «28 сентября – 02 октября 2026». */
function range(first: string, last: string): string {
  const [y1, m1, d1] = parts(first);
  const [y2, m2, d2] = parts(last);
  if (first === last) return dayLong(first);
  const a = String(d1).padStart(2, '0');
  const b = String(d2).padStart(2, '0');
  if (y1 === y2 && m1 === m2) return `${a} – ${b} ${MONTHS[m2 - 1] ?? ''} ${y2}`;
  if (y1 === y2) return `${a} ${MONTHS[m1 - 1] ?? ''} – ${b} ${MONTHS[m2 - 1] ?? ''} ${y2}`;
  return `${dayLong(first)} – ${dayLong(last)}`;
}

/** «8 сен 2026, 18:42» (кратко) или «8 сентября 2026, 18:42». */
function dateTime(at: string, short = false): string {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return '—';
  const month = (short ? MONTHS_SHORT : MONTHS)[date.getMonth()] ?? '';
  return `${date.getDate()} ${month} ${date.getFullYear()}, ${formatTime(date)}`;
}

function daysWord(n: number): string {
  if (n % 10 === 1 && n % 100 !== 11) return 'день';
  if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) return 'дня';
  return 'дней';
}

function calendarDaysWord(n: number): string {
  if (n % 10 === 1 && n % 100 !== 11) return 'календарный день';
  if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) return 'календарных дня';
  return 'календарных дней';
}

function waitingWord(n: number): string {
  return n % 10 === 1 && n % 100 !== 11 ? 'требует' : 'требуют';
}

function fileType(mime: string): string {
  if (mime === 'application/pdf') return 'PDF';
  if (mime.startsWith('image/')) return mime.slice(6).toUpperCase().replace('JPEG', 'JPG');
  return 'Файл';
}

function docTitle(status: string): string {
  if (status === 'VERIFIED') return 'Справка проверена';
  if (status === 'REJECTED') return 'Документ отклонён';
  return 'Справка на проверке';
}

function docTone(status: string): string {
  if (status === 'VERIFIED') return 'green';
  if (status === 'REJECTED') return 'red';
  return 'orange';
}

const size = (bytes: number) =>
  bytes < 1024 * 1024
    ? `${Math.round(bytes / 1024)} КБ`
    : `${(Math.round((bytes / 1024 / 1024) * 10) / 10).toString().replace('.', ',')} МБ`;
