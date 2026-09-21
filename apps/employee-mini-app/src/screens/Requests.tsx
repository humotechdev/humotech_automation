/**
 * Заявки: больничные и отпуска.
 *
 * Форма одна на оба вида — механика у них общая, различаются только
 * правила, а правила приходят с сервера (`/me/absences/options`). Клиент
 * по ним показывает верные подсказки и не предлагает того, чего
 * организация не разрешает; решает всё равно сервер.
 *
 * Комментарий подписан прямо в поле: диагноз сюда писать не нужно.
 * Он попадёт в глаза кадровику, а в уведомление — намеренно нет.
 *
 * Повторная отправка закрыта на трёх уровнях: кнопка блокируется, форма
 * не принимает второй `submit`, и подтверждение закрывается только после
 * ответа сервера. Заявка на отпуск, поданная дважды, — это два
 * вычета из остатка.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  api,
  type AbsenceOptions,
  type AbsenceRequest,
} from '../api';
import { REQUEST_STATUS, isoToday, period } from '../format';
import { haptic } from '../telegram';
import { DateRangePicker, Field, FileUploadField } from '../ui/fields';
import {
  AfterSubmitNote,
  CertificateRow,
  OnBehalfRow,
  OptionalDateRange,
  OptionalLabel,
  TermsCheckbox,
} from '../ui/sick-fields';
import {
  CheckIcon,
  MedicalIcon,
  PlaneIcon,
  RequestsIcon,
  SendIcon,
} from '../ui/icons';
import { BottomSheet, ConfirmationDialog } from '../ui/overlays';
import {
  Card,
  PrimaryButton,
  SecondaryButton,
  SectionHeader,
  StatusBadge,
  type Tone,
} from '../ui/primitives';
import { EmptyState, ErrorState, LoadingScreen } from '../ui/states';

export type AbsenceKind = 'SICK_LEAVE' | 'ANNUAL_LEAVE';

/**
 * Условия оформления больничного.
 *
 * Здесь, а не на сервере: это не документ, с которым сверяют согласие,
 * а объяснение порядка — то же, что кадровик говорит вслух. Документ
 * версионируется и подтверждается отдельно, в разделе ознакомления.
 */
const TERMS_TEXT = [
  'Заявка создаётся сразу, без согласования.',
  'Заявление по шаблону придёт в Telegram: распечатайте, подпишите и отправьте на почту HR.',
  'Справку можно приложить сейчас или позже, в карточке заявки.',
  'Фактические даты больничного кадровик проставит по справке.',
].join(' ');

/** Заявки, по которым ещё возможны действия. Остальное — история. */
const ACTIVE_STATUSES = ['DRAFT', 'SUBMITTED', 'IN_REVIEW'];

const TONE: Record<string, Tone> = {
  SUBMITTED: 'warning',
  IN_REVIEW: 'warning',
  APPROVED: 'success',
  REJECTED: 'danger',
  CANCELLED: 'neutral',
  DRAFT: 'neutral',
};

export function Requests({
  openForm,
  fullName,
}: {
  openForm?: AbsenceKind | null;
  /** На кого оформляется заявка. Человек должен это видеть. */
  fullName: string;
}) {
  const [options, setOptions] = useState<AbsenceOptions | null>(null);
  const [requests, setRequests] = useState<AbsenceRequest[] | null>(null);
  const [balance, setBalance] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<'active' | 'history'>('active');
  const [form, setForm] = useState<AbsenceKind | null>(openForm ?? null);
  const [cancelling, setCancelling] = useState<AbsenceRequest | null>(null);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const reload = useCallback(async () => {
    const [opts, list, rest] = await Promise.all([
      api.absenceOptions(),
      api.absences(),
      api.leaveBalance(),
    ]);
    if (!list.ok) {
      setError(list.message);
      return;
    }
    setError(null);
    setRequests(list.value.requests);
    if (opts.ok) setOptions(opts.value);
    if (rest.ok) {
      const row = rest.value.balances.find(
        (item) => item.absence_type.code === 'ANNUAL_LEAVE',
      );
      setBalance(row ? row.available_days : null);
    }
  }, []);

  useEffect(() => {
    setRequests(null);
    void reload();
  }, [reload, attempt]);

  useEffect(() => {
    if (openForm) setForm(openForm);
  }, [openForm]);

  async function confirmCancel() {
    if (!cancelling) return;
    setBusy(true);
    const result = await api.cancelAbsence(cancelling.id);
    setBusy(false);
    if (!result.ok) {
      setError(result.message);
      setCancelling(null);
      return;
    }
    haptic('success');
    setCancelling(null);
    await reload();
  }

  const active = (requests ?? []).filter((row) =>
    ACTIVE_STATUSES.includes(row.status),
  );
  const past = (requests ?? []).filter(
    (row) => !ACTIVE_STATUSES.includes(row.status),
  );
  const shown = tab === 'active' ? active : past;

  return (
    <>
      <SectionHeader title="Заявки" />

      <div className="quick-actions">
        <button
          type="button"
          className="quick-action"
          onClick={() => setForm('SICK_LEAVE')}
        >
          <MedicalIcon size={22} />
          <span>Оформить больничный</span>
        </button>
        <button
          type="button"
          className="quick-action"
          onClick={() => setForm('ANNUAL_LEAVE')}
        >
          <PlaneIcon size={22} />
          <span>Запросить отпуск</span>
        </button>
      </div>

      {balance !== null && (
        <Card>
          <div className="section-header">
            <h3>Остаток отпуска</h3>
            <span className="metric-value">{balance} дн.</span>
          </div>
          <p className="muted">
            Доступно к запросу. Подтверждённые дни уже вычтены.
          </p>
        </Card>
      )}

      <div className="segmented" role="group" aria-label="Какие заявки показать">
        <button
          type="button"
          aria-pressed={tab === 'active'}
          onClick={() => setTab('active')}
        >
          Активные{active.length ? ` (${active.length})` : ''}
        </button>
        <button
          type="button"
          aria-pressed={tab === 'history'}
          onClick={() => setTab('history')}
        >
          История
        </button>
      </div>

      {error && (
        <ErrorState message={error} onRetry={() => setAttempt((n) => n + 1)} />
      )}
      {!requests && !error && <LoadingScreen label="Загружаем заявки" cards={2} />}

      {requests && shown.length === 0 && (
        <EmptyState
          icon={<RequestsIcon size={28} />}
          title={tab === 'active' ? 'Активных заявок нет' : 'История пуста'}
          description={
            tab === 'active'
              ? 'Оформите больничный или запросите отпуск кнопками выше.'
              : 'Здесь появятся рассмотренные и отменённые заявки.'
          }
        />
      )}

      {shown.map((row) => (
        <RequestCard
          key={row.id}
          request={row}
          onCancel={() => setCancelling(row)}
        />
      ))}

      <AbsenceForm
        kind={form}
        options={options}
        balance={balance}
        fullName={fullName}
        onClose={() => setForm(null)}
        onCreated={() => {
          setForm(null);
          setTab('active');
          void reload();
        }}
      />

      <ConfirmationDialog
        open={cancelling !== null}
        title="Отменить заявку?"
        description="Отменённую заявку нельзя вернуть — придётся подать новую."
        confirmLabel="Отменить заявку"
        cancelLabel="Оставить"
        busy={busy}
        onConfirm={() => void confirmCancel()}
        onCancel={() => setCancelling(null)}
      />
    </>
  );
}

export function RequestCard({
  request,
  onCancel,
}: {
  request: AbsenceRequest;
  onCancel: () => void;
}) {
  const [getting, setGetting] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  /**
   * Показать заявление.
   *
   * Файл приходит с токеном в заголовке, а не по ссылке с токеном в
   * адресе: такую ссылку человек перешлёт в чат, и она будет работать
   * у всех, кто её открыл.
   *
   * Дальше его отдаёт браузер — `<a download>` с программным нажатием.
   * Другого способа в вебвью Telegram нет: панели вложений у мини-
   * приложения не существует, а `openLink` умеет только обычные адреса.
   */
  async function openApplication() {
    if (getting) return;
    setGetting(true);
    setFailed(null);
    const answer = await api.absenceApplication(request.id);
    setGetting(false);

    if (!answer.ok) {
      setFailed(answer.message);
      return;
    }
    if (typeof URL?.createObjectURL !== 'function') {
      setFailed('Это приложение не умеет открывать файлы. Попросите бланк в отделе кадров.');
      return;
    }

    const url = URL.createObjectURL(answer.value);
    const link = document.createElement('a');
    link.href = url;
    link.download = `zayavlenie-${request.id.slice(0, 8)}.pdf`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Адрес держит файл в памяти вкладки, пока его не отозвать. Секунда
    // — с запасом на то, чтобы браузер успел начать сохранение.
    setTimeout(() => URL.revokeObjectURL?.(url), 1000);
  }

  return (
    <Card>
      <div className="section-header">
        <h3>{request.absence_type.name}</h3>
        <StatusBadge tone={TONE[request.status] ?? 'neutral'} dot>
          {REQUEST_STATUS[request.status] ?? request.status.toLowerCase()}
        </StatusBadge>
      </div>

      <p className="metric-value">
        {request.first_day && request.last_day
          ? period(request.first_day, request.last_day)
          : '—'}
      </p>

      <div className="stack-tight">
        <p className="muted">Рабочих дней: {request.working_days}</p>
        {request.submitted_at && (
          <p className="muted">
            Подана {new Date(request.submitted_at).toLocaleDateString('ru-RU')}
          </p>
        )}
        {request.documents > 0 && (
          <p className="muted">
            Справка приложена ({request.documents})
          </p>
        )}
        {request.absence_type.requires_document && request.documents === 0 && (
          <StatusBadge tone="warning">Нужна справка</StatusBadge>
        )}
        {request.extension_pending && (
          <StatusBadge tone="navy">Продление ждёт решения</StatusBadge>
        )}
        {request.review_comment && (
          <p className="muted">Отдел кадров: {request.review_comment}</p>
        )}
      </div>

      {/* Бланк для печати. Оформляют заявку в телефоне, а подписывают
          бумагу: набирать её заново в Word после того, как всё уже
          введено, — лишняя работа, в которой ещё и ошибаются. */}
      <SecondaryButton onClick={() => void openApplication()} wide>
        {getting ? 'Готовим заявление…' : 'Заявление для печати'}
      </SecondaryButton>
      {failed && <p className="field-error" role="alert">{failed}</p>}

      {request.can_cancel && (
        <SecondaryButton onClick={onCancel} wide>
          Отменить заявку
        </SecondaryButton>
      )}
    </Card>
  );
}

/**
 * Форма заявки в нижней шторке.
 *
 * После отправки — свой экран успеха, а не системное окно: `alert`
 * в вебвью Telegram выглядит чужим, не переводится и на части клиентов
 * блокирует приложение до ответа.
 */
/** Экспортируется ради теста на повторную отправку. */
export function AbsenceForm({
  kind,
  options,
  balance,
  fullName,
  onClose,
  onCreated,
}: {
  kind: AbsenceKind | null;
  options: AbsenceOptions | null;
  balance: number | null;
  fullName: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const sick = kind === 'SICK_LEAVE';
  // У больничного даты пустые: человек заболел и не знает, когда
  // выйдет. У отпуска — сегодняшние: его планируют, и пустое поле там
  // означало бы недозаполненную форму, а не «неизвестно».
  const [first, setFirst] = useState(isoToday());
  const [last, setLast] = useState(isoToday());
  const [agreed, setAgreed] = useState(false);
  const [terms, setTerms] = useState(false);
  const [comment, setComment] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<AbsenceRequest | null>(null);
  const [confirming, setConfirming] = useState(false);

  // Замок на отправке — в ссылке, а не в состоянии. `busy` гасит кнопку,
  // но гасит её только после следующей отрисовки: два быстрых касания
  // успевают попасть в один и тот же кадр, где `busy` ещё `false`, и
  // тогда уходят две заявки — а с ними два резерва отпускных дней.
  const sending = useRef(false);

  useEffect(() => {
    if (!kind) return;
    const start = kind === 'SICK_LEAVE' ? '' : isoToday();
    setFirst(start);
    setLast(start);
    setComment('');
    setFile(null);
    setAgreed(false);
    setError(null);
    setCreated(null);
  }, [kind]);

  if (!kind) return null;

  const type = options?.types.find((item) => item.code === kind);
  const title = type?.name ?? (kind === 'SICK_LEAVE' ? 'Больничный' : 'Отпуск');
  const documentRequired =
    (options?.policy.document_required ?? false) ||
    (type?.requires_document ?? false);

  async function send() {
    if (sending.current) return; // второй submit не проходит
    sending.current = true;
    setBusy(true);
    setError(null);

    const form = new FormData();
    form.append('absence_type_code', kind as string);
    // Пустые даты не отправляются вовсе. Пустая строка в
    // `multipart/form-data` — это значение, и сервер разбирал бы её
    // как неверную дату вместо «не указано».
    if (first && last) {
      form.append('first_day', first);
      form.append('last_day', last);
    }
    if (comment.trim()) form.append('comment', comment.trim());
    if (file) form.append('document', file);

    const result = await api.createAbsence(form);
    sending.current = false;
    setBusy(false);
    setConfirming(false);

    if (!result.ok) {
      haptic('error');
      setError(result.message);
      return;
    }
    haptic('success');
    setCreated(result.value);
  }

  if (created) {
    return (
      <BottomSheet open title={title} onClose={onCreated}>
        <div className="scan-result">
          <span className="scan-mark">
            <CheckIcon size={34} />
          </span>
          <p className="scan-title">
            {sick ? 'Заявка создана' : 'Заявка отправлена'}
          </p>
          <p className="state-text">
            {created.first_day && created.last_day
              ? `${period(created.first_day, created.last_day)} · ${
                  created.working_days
                } рабочих дней`
              : ''}
          </p>
          <p className="state-text">
            {sick
              ? 'В Telegram придёт заявление по шаблону. Распечатайте его, подпишите и отправьте на почту HR.'
              : options?.policy.require_hr_approval === false
                ? 'Согласование не требуется — заявка уже в силе.'
                : 'Отдел кадров рассмотрит её и пришлёт решение в этот чат.'}
          </p>
          <PrimaryButton onClick={onCreated} wide>
            Готово
          </PrimaryButton>
        </div>
      </BottomSheet>
    );
  }

  if (sick) {
    /*
     * Больничный. Отдельная ветка, а не флаги в общей форме: правила
     * различаются в каждом поле — даты, справка, согласие, — и общая
     * форма превратилась бы в пять ветвлений подряд, где каждое надо
     * читать, чтобы понять любое.
     */
    return (
      <>
        <BottomSheet open title={title} onClose={onClose}>
          <div className="sick-form">
            <OnBehalfRow fullName={fullName} />

            <OptionalDateRange
              first={first}
              last={last}
              onFirst={setFirst}
              onLast={setLast}
              disabled={busy}
              {...(first && last && last < first
                ? { error: 'Конец периода раньше начала' }
                : {})}
            />

            <div className="sick-block">
              <OptionalLabel label="Комментарий" htmlFor="sick-comment" />
              <textarea
                id="sick-comment"
                className="sick-area"
                value={comment}
                placeholder="Напишите, если есть важная информация"
                onChange={(event) => setComment(event.target.value)}
                maxLength={2000}
                disabled={busy}
              />
              <p className="sick-note">Диагноз указывать не нужно.</p>
            </div>

            <CertificateRow
              file={file}
              onFile={setFile}
              {...(options?.policy.allowed_document_types
                ? { allowedTypes: options.policy.allowed_document_types }
                : {})}
              {...(options?.policy.max_document_bytes
                ? { maxBytes: options.policy.max_document_bytes }
                : {})}
              disabled={busy}
            />

            <AfterSubmitNote icon={<SendIcon size={22} />} title="После отправки">
              В Telegram придёт готовый шаблон заявления. Распечатайте его,
              подпишите и отправьте на электронную почту HR.
            </AfterSubmitNote>

            {error && (
              <p className="field-error" role="alert">
                {error}
              </p>
            )}
          </div>

          {/* Согласие и кнопка прижаты к низу: до них дотягиваются
              большим пальцем, а список полей над ними прокручивается. */}
          <div className="sick-footer">
            <TermsCheckbox
              checked={agreed}
              onChange={setAgreed}
              onTerms={() => setTerms(true)}
              disabled={busy}
            />
            <PrimaryButton
              onClick={() => void send()}
              disabled={busy || !agreed || (Boolean(first && last) && last < first)}
              wide
            >
              {busy ? 'Создаём…' : 'Создать заявку'}
            </PrimaryButton>
          </div>
        </BottomSheet>

        <ConfirmationDialog
          open={terms}
          title="Условия оформления больничного"
          description={TERMS_TEXT}
          confirmLabel="Понятно"
          onConfirm={() => setTerms(false)}
          onCancel={() => setTerms(false)}
        />
      </>
    );
  }

  return (
    <>
      <BottomSheet open title={title} onClose={onClose}>
        <DateRangePicker
          first={first}
          last={last}
          onFirst={setFirst}
          onLast={setLast}
          disabled={busy}
          error={last < first ? 'Конец периода раньше начала' : undefined}
          footnote={
            kind === 'ANNUAL_LEAVE' && balance !== null
              ? `Доступно ${balance} дн. Точное число рабочих дней посчитает сервер.`
              : 'Точное число рабочих дней посчитает сервер по вашему графику.'
          }
        />

        <Field label="Комментарий" htmlFor="absence-comment" hint="Необязательно.">
          <textarea
            id="absence-comment"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            maxLength={2000}
            disabled={busy}
          />
        </Field>

        {documentRequired && (
          <FileUploadField
            file={file}
            onFile={setFile}
            allowedTypes={options?.policy.allowed_document_types}
            maxBytes={options?.policy.max_document_bytes}
            required={documentRequired}
            hint={
              documentRequired
                ? 'Без справки заявку не примут.'
                : 'Справку можно приложить позже.'
            }
            disabled={busy}
          />
        )}

        {error && (
          <p className="field-error" role="alert">
            {error}
          </p>
        )}

        <PrimaryButton
          onClick={() => setConfirming(true)}
          disabled={busy || last < first}
          wide
        >
          {busy ? 'Отправляем…' : 'Отправить заявку'}
        </PrimaryButton>
      </BottomSheet>

      <ConfirmationDialog
        open={confirming}
        title="Отправить заявку?"
        description={`${title}, ${period(first, last)}. После отправки её увидит отдел кадров.`}
        confirmLabel="Отправить"
        busy={busy}
        onConfirm={() => void send()}
        onCancel={() => setConfirming(false)}
      />
    </>
  );
}
