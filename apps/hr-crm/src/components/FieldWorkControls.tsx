/** HR creates a dated field-work request only after the manager confirmed it.
 * The server owns eligibility, scope, scheduled hours and final attendance. */
import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../api/crm';
import { messageFor } from '../api/errors';
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

export function FieldWorkControls({ employeeId, employeeName, day, scheduledStart, scheduledEnd, firstEntryAt, state, summary, canCorrect, autoOpen = false, onChanged }: {
  employeeId: string; employeeName: string; day: string;
  scheduledStart?: string | null | undefined; scheduledEnd?: string | null | undefined;
  firstEntryAt?: string | null | undefined;
  state: string; summary?: { id: string; status: api.FieldWorkStatus } | null | undefined;
  canCorrect: boolean; autoOpen?: boolean; onChanged: () => void;
}) {
  const [request, setRequest] = useState<api.FieldWorkRequest | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [managerConfirmed, setManagerConfirmed] = useState(false);
  const [location, setLocation] = useState('');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const previousStatus = useRef<string | null>(null);
  const didAutoOpen = useRef(false);
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;
  const busyRef = useRef(busy);
  busyRef.current = busy;

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

  useEffect(() => {
    if (!open) return;
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
  }, [open]);

  // Do not offer creation against a day with QR events, approved leave or no schedule.
  const eligible = (unmarked(state) && !firstEntryAt && Boolean(scheduledStart)) || state === 'FIELD_WORK_PENDING';
  const status = request?.status ?? summary?.status;
  const show = eligible || state === 'FIELD_WORK' || Boolean(status);
  if (!show) return null;

  async function act(kind: 'create' | 'cancel' | 'retry') {
    if (busy) return;
    setBusy(true);
    setError(null);
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
      setOpen(false);
      onChanged();
    } catch (failure) {
      setError(messageFor(failure));
      // On a timeout, creation may have succeeded. Fetch before allowing another send.
      void refresh();
    } finally {
      setBusy(false);
    }
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
      {canCorrect && !loading && eligible && (!status || (unmarked(state) && terminal(status))) && (
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
            <p>График: {scheduledStart && scheduledEnd ? `${scheduledStart.slice(0, 5)}–${scheduledEnd.slice(0, 5)}` : 'по назначенному графику'}.
              Сотрудник должен подтвердить работу в Telegram. До его ответа день не засчитывается.</p>
            <label className="fw__field">Объект или место (необязательно)
              <input maxLength={200} value={location} onChange={(e) => setLocation(e.target.value)} />
            </label>
            <label className="fw__field">Рабочая задача (необязательно)
              <textarea maxLength={1000} value={description} onChange={(e) => setDescription(e.target.value)} />
            </label>
            <label className="fw__check">
              <input type="checkbox" checked={managerConfirmed} onChange={(e) => setManagerConfirmed(e.target.checked)} />
              Я уточнил(а) у руководителя: сотрудник работает вне офиса в указанный день.
            </label>
            {error && <p className="fw__error" role="alert">{error}</p>}
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
