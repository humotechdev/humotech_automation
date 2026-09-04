/**
 * Больничные и отпуска: подача заявки и список своих.
 *
 * Форма одна на оба вида — механика у них общая, различаются только
 * правила, и правила приходят с сервера (`/me/absences/options`).
 * Клиент по ним показывает верные подсказки и не предлагает того, чего
 * организация не разрешает; решает всё равно сервер.
 *
 * Комментарий подписан прямо в поле: диагноз сюда писать не нужно.
 * Он попадёт в глаза кадровику, а в уведомление — намеренно нет.
 */

import { useEffect, useState } from 'react';

import { api, type AbsenceOptions, type AbsenceRequest } from '../api';
import { REQUEST_STATUS, isoToday, period } from '../format';

export function Absences({
  kind,
  onBack,
}: {
  kind: 'SICK_LEAVE' | 'ANNUAL_LEAVE';
  onBack: () => void;
}) {
  const [options, setOptions] = useState<AbsenceOptions | null>(null);
  const [requests, setRequests] = useState<AbsenceRequest[]>([]);
  const [balance, setBalance] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [first, setFirst] = useState(isoToday());
  const [last, setLast] = useState(isoToday());
  const [comment, setComment] = useState('');
  const [file, setFile] = useState<File | null>(null);

  async function reload() {
    const [opts, list] = await Promise.all([
      api.absenceOptions(),
      api.absences(),
    ]);
    if (opts.ok) setOptions(opts.value);
    if (list.ok) setRequests(list.value.requests);

    if (kind === 'ANNUAL_LEAVE') {
      const rest = await api.leaveBalance();
      if (rest.ok && rest.value.balances.length) {
        const row = rest.value.balances.find(
          (item) => item.absence_type.code === 'ANNUAL_LEAVE',
        );
        if (row) setBalance(`${row.available_days} дн. доступно`);
      }
    }
  }

  useEffect(() => {
    void reload();
    // Перезагружается при смене вида: у больничного и отпуска разные
    // списки и разный смысл остатка.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind]);

  const type = options?.types.find((item) => item.code === kind);
  const mine = requests.filter((row) => row.absence_type.code === kind);
  const needsDocument = options?.policy.document_required ?? false;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;

    setBusy(true);
    setError(null);

    const form = new FormData();
    form.append('absence_type_code', kind);
    form.append('first_day', first);
    form.append('last_day', last);
    if (comment.trim()) form.append('comment', comment.trim());
    if (file) form.append('document', file);

    const result = await api.createAbsence(form);
    setBusy(false);

    if (!result.ok) {
      setError(result.message);
      return;
    }
    setComment('');
    setFile(null);
    await reload();
  }

  async function cancel(id: string) {
    setBusy(true);
    const result = await api.cancelAbsence(id);
    setBusy(false);
    if (!result.ok) {
      setError(result.message);
      return;
    }
    await reload();
  }

  return (
    <div className="stack">
      <h2>{type?.name ?? (kind === 'SICK_LEAVE' ? 'Больничный' : 'Отпуск')}</h2>
      {balance && <p className="muted">{balance}</p>}

      <form className="card" onSubmit={submit}>
        <label htmlFor="first">С какого дня</label>
        <input
          id="first"
          type="date"
          value={first}
          onChange={(event) => setFirst(event.target.value)}
          required
        />

        <label htmlFor="last">По какой день</label>
        <input
          id="last"
          type="date"
          value={last}
          min={first}
          onChange={(event) => setLast(event.target.value)}
          required
        />

        <label htmlFor="comment">
          Комментарий {kind === 'SICK_LEAVE' && '(диагноз указывать не нужно)'}
        </label>
        <textarea
          id="comment"
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          rows={2}
          maxLength={2000}
        />

        {(needsDocument || type?.requires_document) && (
          <>
            <label htmlFor="document">
              Справка {needsDocument ? '(обязательна)' : '(можно приложить позже)'}
            </label>
            <input
              id="document"
              type="file"
              accept={options?.policy.allowed_document_types.join(',')}
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </>
        )}

        {error && <p className="error">{error}</p>}

        <button type="submit" className="primary" disabled={busy}>
          {busy ? 'Отправляем…' : 'Отправить заявку'}
        </button>

        {options && !options.policy.require_hr_approval && (
          <p className="muted">
            Согласование не требуется — заявка вступит в силу сразу.
          </p>
        )}
      </form>

      {mine.length > 0 && (
        <section className="stack">
          <h3>Мои заявки</h3>
          {mine.map((row) => (
            <article key={row.id} className="card">
              <p>
                <strong>
                  {row.first_day && row.last_day
                    ? period(row.first_day, row.last_day)
                    : '—'}
                </strong>{' '}
                <span className="muted">
                  {REQUEST_STATUS[row.status] ?? row.status.toLowerCase()}
                </span>
              </p>
              <p className="muted">Рабочих дней: {row.working_days}</p>
              {row.extension_pending && (
                <p className="muted">Продление ждёт решения</p>
              )}
              {row.review_comment && (
                <p className="muted">Комментарий: {row.review_comment}</p>
              )}
              {row.can_cancel && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void cancel(row.id)}
                >
                  Отменить заявку
                </button>
              )}
            </article>
          ))}
        </section>
      )}

      <button type="button" onClick={onBack}>
        Назад
      </button>
    </div>
  );
}
