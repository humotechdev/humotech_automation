/** HR creates a dated field-work request only after the manager confirmed it.
 * The server owns eligibility, scope, scheduled hours and final attendance.
 *
 * Два вида одной формы. В правой панели «Отметка за день» (`inline`) форма
 * встроена в саму панель: второго окна и второго затемнения поверх панели
 * нет. На странице сотрудника панели нет, и там остаётся диалог. */
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import * as api from '../api/crm';
import { messageFor, reasonFor } from '../api/errors';
import '../styles/field-work.css';

const LABEL: Record<api.FieldWorkStatus, string> = {
  PENDING: 'Ожидает подтверждения сотрудника',
  CONFIRMED: 'Выездная работа подтверждена',
  DECLINED: 'Сотрудник не подтвердил выездную работу',
  CANCELLED: 'Запрос отменён',
  EXPIRED: 'Срок подтверждения истёк',
};
const terminal = (status: api.FieldWorkStatus | undefined) =>
  status === 'DECLINED' || status === 'CANCELLED' || status === 'EXPIRED';
const unmarked = (state: string) => state === 'NOT_COME' || state === 'LATE';

/** Доставка сообщения — отдельно от статуса запроса: POST ставит сообщение
 * в очередь, а не вручает его. «Получил» здесь не говорится никогда: у бота
 * есть только ответ Telegram «принято к отправке». */
function delivery(status: string | null | undefined): { text: string; bad?: boolean } | null {
  switch (status) {
    case 'PENDING': return { text: 'Сообщение в очереди на отправку в Telegram' };
    case 'RUNNING': return { text: 'Сообщение отправляется в Telegram' };
    case 'SENT':
    case 'READ': return { text: 'Сообщение отправлено в Telegram' };
    case 'FAILED': return { text: 'Не удалось отправить сообщение в Telegram', bad: true };
    default: return null;
  }
}

/** Длительность из секунд сервера — только подпись, не расчёт. */
function hoursText(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return minutes ? `${hours} ч ${minutes} мин` : `${hours} ч`;
}

/** Что с часами дня — словами сервера, без расчёта во frontend. */
function credit(request: api.FieldWorkRequest | null, status: api.FieldWorkStatus | undefined): string | null {
  if (status === 'PENDING') return 'Засчитано: 0 ч. До ответа сотрудника день не засчитывается.';
  if (status === 'CONFIRMED') {
    const norm = request?.norm_seconds ? ` (${hoursText(request.norm_seconds)})` : '';
    return `День засчитан по плановой норме графика${norm}. Это не присутствие в офисе и не вход или выход по QR.`;
  }
  if (terminal(status)) return 'Часы не начислены.';
  return null;
}

export function FieldWorkControls({ employeeId, employeeName, day, scheduledStart, scheduledEnd, firstEntryAt, state, summary, canCorrect, autoOpen = false, inline = false, onChanged }: {
  employeeId: string; employeeName: string; day: string;
  scheduledStart?: string | null | undefined; scheduledEnd?: string | null | undefined;
  firstEntryAt?: string | null | undefined;
  state: string; summary?: { id: string; status: api.FieldWorkStatus } | null | undefined;
  canCorrect: boolean; autoOpen?: boolean; inline?: boolean; onChanged: () => void;
}) {
  const [request, setRequest] = useState<api.FieldWorkRequest | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [managerConfirmed, setManagerConfirmed] = useState(false);
  const [location, setLocation] = useState('');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const firstFieldRef = useRef<HTMLInputElement>(null);
  const formRef = useRef<HTMLDivElement>(null);
  const startRef = useRef<HTMLButtonElement>(null);
  const previousStatus = useRef<string | null>(null);
  const didAutoOpen = useRef(false);
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const ids = useId();

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await api.fieldWorkRequests(employeeId, day, signal);
      if (signal?.aborted) return;
      const latest = data.items[0] ?? null;
      setRequest(latest);
      setError(null);
      const status = latest?.status ?? null;
      if (previousStatus.current !== null && status !== previousStatus.current) onChangedRef.current();
      previousStatus.current = status;
    } catch (failure) {
      if (!signal?.aborted) setError(messageFor(failure));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [employeeId, day]);

  useEffect(() => {
    const controller = new AbortController();
    previousStatus.current = null;
    setRequest(null);
    setLoading(true);
    void refresh(controller.signal);
    return () => controller.abort();
  }, [employeeId, day, refresh]);

  useEffect(() => {
    if (autoOpen && !loading && !error && (!request || terminal(request.status)) && canCorrect && unmarked(state) && !firstEntryAt && scheduledStart && !didAutoOpen.current) {
      didAutoOpen.current = true;
      setOpen(true);
    }
  }, [autoOpen, loading, error, request, canCorrect, state, firstEntryAt, scheduledStart]);

  useEffect(() => {
    if (request?.status !== 'PENDING' && summary?.status !== 'PENDING') return;
    const poll = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 20_000);
    const onFocus = () => { if (!document.hidden) void refresh(); };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onFocus);
    return () => {
      window.clearInterval(poll);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onFocus);
    };
  }, [request?.status, summary?.status, refresh]);

  // Диалог (страница сотрудника): фокус заперт внутри, Escape закрывает.
  useEffect(() => {
    if (!open || inline) return;
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busyRef.current) setOpen(false);
      if (event.key !== 'Tab') return;
      const dialog = closeRef.current?.closest('[role="dialog"]');
      const focusable = Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled)') ?? []);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('keydown', onKey); before?.focus(); };
  }, [open, inline]);

  // Встроенная форма: фокус на первое поле, форма видна в прокрутке панели.
  useEffect(() => {
    if (!open || !inline) return;
    firstFieldRef.current?.focus({ preventScroll: true });
    formRef.current?.scrollIntoView?.({ block: 'nearest' });
  }, [open, inline]);

  // Do not offer creation against a day with QR events, approved leave or no schedule.
  const eligible = (unmarked(state) && !firstEntryAt && Boolean(scheduledStart)) || state === 'FIELD_WORK_PENDING';
  const status = request?.status ?? summary?.status;
  const show = eligible || state === 'FIELD_WORK' || Boolean(status);
  if (!show) return null;

  // Пока состояние запроса не прочитано (ошибка чтения), новый не предлагается.
  const canStart = canCorrect && !loading && !error && eligible && (!status || (unmarked(state) && terminal(status)));

  function closeForm() {
    if (busyRef.current) return;
    setOpen(false);
    setFormError(null);
    if (inline) window.setTimeout(() => startRef.current?.focus(), 0);
  }

  async function act(kind: 'create' | 'cancel' | 'retry') {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    setFormError(null);
    try {
      const next = kind === 'create'
        ? await api.requestFieldWork({ employee_id: employeeId, date: day, manager_confirmed: true,
          ...(location.trim() ? { work_location: location.trim() } : {}),
          ...(description.trim() ? { work_description: description.trim() } : {}) })
        : kind === 'cancel'
          ? await api.cancelFieldWork(request!.id)
          : await api.retryFieldWork(request!.id);
      setRequest(next);
      previousStatus.current = next.status;
      if (kind === 'create') {
        setOpen(false);
        setLocation('');
        setDescription('');
        setManagerConfirmed(false);
      }
      onChanged();
    } catch (failure) {
      // Ошибка создания — рядом с формой, введённое остаётся на месте.
      // Отказ — словами сервера: «в этот день уже есть отметка» нужнее
      // кадровику, чем общее «действие недоступно».
      if (kind === 'create' && open) setFormError(reasonFor(failure));
      else setError(reasonFor(failure));
      // On a timeout, creation may have succeeded. Fetch before allowing another send.
      void refresh();
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  const schedule = scheduledStart && scheduledEnd
    ? `${scheduledStart.slice(0, 5)}–${scheduledEnd.slice(0, 5)}` : 'по назначенному графику';
  const sent = delivery(request?.delivery_status);
  const hours = credit(request, status);

  const fields = (
    <>
      <label className="fw__field">
        <span className="fw__label">Объект или место <span className="fw__hint">необязательно</span></span>
        <input ref={firstFieldRef} maxLength={200} value={location} disabled={busy}
               placeholder="Например, офис партнёра"
               onChange={(e) => setLocation(e.target.value)} />
      </label>
      <label className="fw__field">
        <span className="fw__label">Рабочая задача <span className="fw__hint">необязательно</span></span>
        <textarea maxLength={1000} value={description} disabled={busy}
                  placeholder="Кратко опишите работу вне офиса"
                  onChange={(e) => setDescription(e.target.value)} />
      </label>
      <label className={`fw__check${managerConfirmed ? ' fw__check--on' : ''}`}>
        <input type="checkbox" checked={managerConfirmed} disabled={busy}
               aria-describedby={`${ids}-attest`}
               onChange={(e) => setManagerConfirmed(e.target.checked)} />
        <span>
          Руководитель подтвердил, что сотрудник работает вне офиса в этот день
          <small id={`${ids}-attest`} className="fw__hint fw__hint--block">
            Отметка HR о полученном подтверждении, а не подпись руководителя.
          </small>
        </span>
      </label>
    </>
  );

  if (inline) {
    return (
      <section className="fw fw--inline" aria-labelledby={`${ids}-title`}>
        <header className="fw__head">
          <h3 id={`${ids}-title`} className="fw__title">Выездная работа</h3>
          {scheduledStart && scheduledEnd && (
            <span className="fw__chip" title="График дня">{schedule}</span>
          )}
        </header>

        {loading && !status && <p role="status" className="fw__muted">Проверяем запрос…</p>}

        {status && !open && (
          <div className={`fw__state fw__state--${status.toLowerCase()}`}>
            <p className="fw__status" role="status"><i className="fw__dot" aria-hidden="true" />{LABEL[status] ?? status}</p>
            {hours && <p className="fw__line">{hours}</p>}
            {status === 'PENDING' && sent && (
              <p className={`fw__line fw__delivery${sent.bad ? ' fw__delivery--bad' : ''}`}>{sent.text}</p>
            )}
            {(request?.work_location || request?.work_description) && (
              <dl className="fw__facts">
                {request?.work_location && (<><dt>Объект</dt><dd>{request.work_location}</dd></>)}
                {request?.work_description && (<><dt>Задача</dt><dd className="fw__wrap">{request.work_description}</dd></>)}
              </dl>
            )}
          </div>
        )}
        {!status && state === 'FIELD_WORK' && (
          <p className="fw__line">Полный день засчитан по графику. Вход и выход по QR не создавались.</p>
        )}

        {error && (
          <p className="fw__error" role="alert">
            {error} <button type="button" className="btn" onClick={() => void refresh()}>Обновить</button>
          </p>
        )}

        {canCorrect && request?.status === 'PENDING' && !open && (
          <div className="fw__row fw__row--start">
            {request.delivery_status === 'FAILED' && (
              <button type="button" className="fw__primary" disabled={busy} onClick={() => void act('retry')}>Повторить отправку</button>
            )}
            <button type="button" className="fw__ghost" disabled={busy} onClick={() => void act('cancel')}>Отменить запрос</button>
          </div>
        )}

        {canStart && !open && (
          <button ref={startRef} type="button" className="fw__primary fw__start" onClick={() => setOpen(true)}>
            <svg className="fw__icon" viewBox="0 0 16 16" aria-hidden="true">
              <path d="M8 1.5a4.5 4.5 0 0 0-4.5 4.5c0 3.2 4.5 8.5 4.5 8.5s4.5-5.3 4.5-8.5A4.5 4.5 0 0 0 8 1.5Zm0 6.3A1.8 1.8 0 1 1 8 4.2a1.8 1.8 0 0 1 0 3.6Z" />
            </svg>
            {status ? 'Отправить новый запрос' : 'Запросить подтверждение'}
          </button>
        )}

        {open && (
          <div ref={formRef} className="fw__form" role="group" aria-labelledby={`${ids}-title`}
               onKeyDown={(event) => {
                 if (event.key !== 'Escape') return;
                 // Escape закрывает только форму: панель остаётся открытой.
                 event.stopPropagation();
                 closeForm();
               }}>
            <p className="fw__note">
              <svg className="fw__icon" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm.9 10.6H7.1V7h1.8v4.6ZM8 5.9a1 1 0 1 1 0-2 1 1 0 0 1 0 2Z" />
              </svg>
              <span>Сотрудник получит запрос в Telegram. До его ответа день не засчитывается.</span>
            </p>
            {fields}
            {formError && <p className="fw__error" role="alert">{formError}</p>}
            <div className="fw__footer">
              <div className="fw__row">
                <button type="button" className="fw__ghost" disabled={busy} onClick={closeForm}>Отмена</button>
                <button type="button" className="fw__primary" disabled={!managerConfirmed || busy}
                        aria-busy={busy} onClick={() => void act('create')}>
                  {busy ? 'Отправляем…' : 'Отправить сотруднику'}
                </button>
              </div>
            </div>
          </div>
        )}
      </section>
    );
  }

  return (
    <section className="fw" aria-label="Выездная работа">
      <h3 className="fw__title">Выездная работа</h3>
      {loading && !status && <p role="status">Проверяем запрос…</p>}
      {status && (
        <p className={`fw__status fw__status--${status.toLowerCase()}`} role="status">
          {LABEL[status] ?? status}
          {status === 'PENDING' && request?.delivery_status === 'FAILED' ? ' · Не удалось доставить в Telegram' : ''}
          {status === 'PENDING' && (request?.delivery_status === 'PENDING' || request?.delivery_status === 'RUNNING') ? ' · Отправляется в Telegram' : ''}
        </p>
      )}
      {request?.work_location && <p>Объект: {request.work_location}</p>}
      {request?.work_description && <p>Задача: {request.work_description}</p>}
      {state === 'FIELD_WORK' && <p>Полный день засчитан по графику. Вход и выход по QR не создавались.</p>}
      {error && <p className="fw__error" role="alert">{error} <button type="button" onClick={() => void refresh()}>Обновить</button></p>}
      {canStart && (
        <button type="button" className="fw__button" onClick={() => setOpen(true)}>
          {status ? 'Отправить новый запрос на выездную работу' : 'Запросить подтверждение выездной работы'}
        </button>
      )}
      {canCorrect && request?.status === 'PENDING' && (
        <div className="fw__actions">
          {request.delivery_status === 'FAILED' && (
            <button type="button" disabled={busy} onClick={() => void act('retry')}>Повторить отправку</button>
          )}
          <button type="button" disabled={busy} onClick={() => void act('cancel')}>Отменить запрос</button>
        </div>
      )}

      {open && (
        <div className="fw__overlay" role="presentation">
          <div className="fw__dialog" role="dialog" aria-modal="true" aria-labelledby="fw-dialog-title">
            <h3 id="fw-dialog-title">Отправить подтверждение выездной работы?</h3>
            <p>{employeeName} · {day}</p>
            <p>График: {schedule}.
              Сотрудник должен подтвердить работу в Telegram. До его ответа день не засчитывается.</p>
            {fields}
            {formError && <p className="fw__error" role="alert">{formError}</p>}
            <div className="fw__actions">
              <button ref={closeRef} type="button" disabled={busy} onClick={() => setOpen(false)}>Отмена</button>
              <button type="button" className="fw__button" disabled={!managerConfirmed || busy}
                      onClick={() => void act('create')}>
                {busy ? 'Отправляем…' : 'Отправить сотруднику'}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
