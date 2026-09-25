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
import { calendarDaysText, dayLabel, isoToday, period } from '../format';
import { haptic } from '../telegram';
import {
  DateRangePicker,
  Field,
  FileUploadField,
  checkCertificate,
} from '../ui/fields';
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
  ChevronRightIcon,
  ClipIcon,
  CloseIcon,
  DocumentIcon,
  HistoryIcon,
  MedicalIcon,
  PlaneIcon,
  RequestsIcon,
  SendIcon,
} from '../ui/icons';
import { BottomSheet, ConfirmationDialog } from '../ui/overlays';
import { PrimaryButton, type Tone } from '../ui/primitives';
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

/**
 * Заявки, по которым ещё возможны действия. Остальное — история.
 *
 * Делим по состоянию ЗАЯВКИ, а не отсутствия. Подтверждённый больничный
 * — решённое дело: приложить к нему нечего, отменить нельзя, и висеть
 * он должен там, где смотрят прошлое. Что он при этом ещё идёт, видно
 * на главной и в календаре, а не в списке заявок.
 */
const ACTIVE_STATUSES = ['DRAFT', 'SUBMITTED', 'IN_REVIEW'];

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
  /**
   * Какая заявка раскрыта целиком.
   *
   * Хранится номер, а не сама заявка: после того как человек приложит
   * справку, список перечитывается, и объект, снятый со старого списка,
   * показывал бы вчерашнее состояние.
   */
  const [opened, setOpened] = useState<string | null>(null);
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
  // Незакрытый больничный у человека может быть только один, и сервер
  // второго не создаст. Показывать при этом форму — обещать то, чего
  // не будет: вместо неё человека надо вернуть к его же заявке.
  const openSick = active.find(
    (row) => row.kind === 'CREATE' && row.absence_type.requires_document,
  );
  const past = (requests ?? []).filter(
    (row) => !ACTIVE_STATUSES.includes(row.status),
  );
  const shown = tab === 'active' ? active : past;
  const sheet = (requests ?? []).find((row) => row.id === opened) ?? null;

  return (
    <>
      <header className="rq-head">
        <div>
          <h1>Заявки</h1>
          <p>Больничные и отпуска</p>
        </div>
        {/* Кнопка ведёт на ту же вкладку «История», что и переключатель
            ниже: сверху её ищут те, кто пришёл именно за прошлым, и
            путь к нему не должен зависеть от того, куда человек
            посмотрел первым. */}
        <button
          type="button"
          className="rq-head-history"
          aria-label="История заявок"
          onClick={() => setTab('history')}
        >
          <HistoryIcon size={22} />
        </button>
      </header>

      {/* Два действия одной карточкой, строками: это не равноправные
          плитки, а список того, что здесь можно начать. Подпись под
          каждым объясняет, чем они отличаются, — без неё «больничный»
          и «отпуск» выглядят одинаково, пока не нажмёшь. */}
      <div className="rq-actions">
        <button
          type="button"
          onClick={() =>
            openSick ? setOpened(openSick.id) : setForm('SICK_LEAVE')
          }
        >
          <MedicalIcon size={26} />
          <span>
            <b>
              {openSick ? 'Открыть текущий больничный' : 'Оформить больничный'}
            </b>
            <small>
              {openSick
                ? `${STAGE[openSick.stage]?.title ?? 'В работе'} — приложите справку или отмените заявку`
                : 'Создать заявку и загрузить справку позже'}
            </small>
          </span>
          <ChevronRightIcon size={20} />
        </button>
        <button type="button" onClick={() => setForm('ANNUAL_LEAVE')}>
          <PlaneIcon size={26} />
          <span>
            <b>Запросить отпуск</b>
            <small>
              {balance !== null
                ? `Доступно ${balance} дн. — выбрать даты и отправить`
                : 'Выбрать даты и отправить на согласование'}
            </small>
          </span>
          <ChevronRightIcon size={20} />
        </button>
      </div>

      {/* Вкладки, а не сегментированный переключатель: разделов два и
          они неравноценны — активные смотрят каждый день, историю
          изредка. Число стоит только у активных: в истории оно растёт
          без конца и ни на что не влияет. */}
      <div className="rq-tabs" role="tablist" aria-label="Какие заявки показать">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'active'}
          onClick={() => setTab('active')}
        >
          Активные
          <i>{active.length}</i>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'history'}
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
          title={
            tab === 'active' ? 'Сейчас нет активных заявок' : 'История пуста'
          }
          description={
            tab === 'active'
              ? 'Создайте больничный или запрос на отпуск — здесь появится статус.'
              : 'Здесь появятся рассмотренные и отменённые заявки.'
          }
        />
      )}

      {shown.map((row) => (
        <RequestCard
          key={row.id}
          request={row}
          onCancel={() => setCancelling(row)}
          onChanged={() => void reload()}
          lateDocuments={options?.policy.document_can_be_added_later}
        />
      ))}

      {/* Заявка целиком — той же формой, что и её создание: те же
          поля в том же порядке, только заполненные и закрытые на
          правку. Менять поданную заявку нельзя — её уже читает
          кадровик; исправляют её справкой и отменой. */}
      <AbsenceForm
        kind={sheet ? 'SICK_LEAVE' : null}
        existing={sheet}
        options={options}
        balance={balance}
        fullName={fullName}
        onClose={() => setOpened(null)}
        onCreated={() => setOpened(null)}
        onChanged={() => void reload()}
        onCancelRequest={() => {
          setOpened(null);
          if (sheet) setCancelling(sheet);
        }}
      />

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

/**
 * Состояние заявки: подпись и цвет.
 *
 * Стадию считает сервер и присылает готовой — клиент её только
 * называет. Собирать её здесь значило бы, что приложение и кадровая
 * система по-разному отвечают на вопрос «подтверждён ли больничный»,
 * а подтверждение — это строка в табеле и деньги.
 */
const STAGE: Record<string, { title: string; tone: Tone }> = {
  WAITING_DOCUMENTS: { title: 'Ожидаем документы', tone: 'warning' },
  HR_REVIEW: { title: 'На проверке HR', tone: 'navy' },
  NEEDS_FIX: { title: 'Нужны исправления', tone: 'danger' },
  PENDING: { title: 'На согласовании', tone: 'navy' },
  APPROVED: { title: 'Подтверждён', tone: 'success' },
  REJECTED: { title: 'Отклонён', tone: 'danger' },
  CANCELLED: { title: 'Отменён', tone: 'neutral' },
};

/**
 * Пояснение под заголовком: чего ждут и от кого.
 *
 * Без него «Ожидаем документы» и «На проверке HR» выглядят одинаково
 * — как «что-то происходит», — и человек не понимает, нужно ли ему
 * что-то делать.
 */
function noteOf(request: AbsenceRequest): string | null {
  if (request.stage === 'NEEDS_FIX') {
    // Причина — от кадровика и дословно: пересказ своими словами
    // однажды смягчит «нечитаемое фото» до «нужен другой документ»,
    // и придёт то же фото.
    return request.certificate_comment
      ? `Справку не приняли. Комментарий HR: ${request.certificate_comment}`
      : 'Справку не приняли — приложите другую.';
  }
  if (request.review_comment) return `Отдел кадров: ${request.review_comment}`;
  if (request.stage === 'CANCELLED' || request.stage === 'REJECTED') return null;
  if (request.stage === 'WAITING_DOCUMENTS') {
    return 'Приложите справку и отправьте подписанное заявление на почту HR.';
  }
  if (request.stage === 'HR_REVIEW') {
    return !request.first_day || !request.last_day
      ? 'Отдел кадров проверит документы и проставит период по справке.'
      : 'Отдел кадров проверяет документы.';
  }
  if (request.stage === 'APPROVED') return null;
  return 'Руководитель ещё не принял решение.';
}

export function RequestCard({
  request,
  onCancel,
  onChanged,
  lateDocuments = true,
}: {
  request: AbsenceRequest;
  onCancel: () => void;
  /** Справку приложили — список надо перечитать. */
  onChanged: () => void;
  /**
   * Разрешает ли организация донести справку после одобрения.
   *
   * По умолчанию да — столько же, сколько на сервере. Правила приходят
   * отдельным запросом, и пока он не вернулся, карточка не должна
   * прятать действие, которое на самом деле работает.
   */
  lateDocuments?: boolean;
}) {
  const state = STAGE[request.stage] ?? STAGE.PENDING;
  const note = noteOf(request);

  // В шапке — период заявки, а если его ещё нет, день подачи: строка
  // «·» без даты слева выглядит как потерянные данные.
  const when =
    request.first_day && request.last_day
      ? request.first_day === request.last_day
        ? dayLabel(request.first_day)
        : `${dayLabel(request.first_day)} — ${dayLabel(request.last_day)}`
      : request.submitted_at
        ? dayLabel(request.submitted_at.slice(0, 10))
        : null;

  return (
    <article className={`rq-card rq-card--${state.tone}`}>
      <p className="rq-card__top">
        <span className="rq-card__when">
          {when && `${when} · `}
          {request.absence_type.name.toUpperCase()}
        </span>
        <span className="rq-card__state">
          <i aria-hidden="true" />
          {state.title}
        </span>
      </p>

      <h3 className="rq-card__title">{request.absence_type.name}</h3>

      {request.first_day && request.last_day && (
        <p className="rq-card__days">
          {calendarDaysText(request.first_day, request.last_day)}
        </p>
      )}
      {note && <p className="rq-card__note">{note}</p>}
      {request.extension_pending && (
        <p className="rq-card__note">Продление ждёт решения.</p>
      )}
      {request.certificate_status === 'VERIFIED' && (
        <p className="rq-card__done">
          <CheckIcon size={16} />
          Справка принята
        </p>
      )}
      {request.certificate_status === 'PENDING' && (
        <p className="rq-card__done rq-card__done--wait">
          <CheckIcon size={16} />
          Справка приложена, ждёт проверки
        </p>
      )}

      <RequestActions
        request={request}
        onCancel={onCancel}
        onChanged={onChanged}
        lateDocuments={lateDocuments}
      />
    </article>
  );
}

/**
 * Что человек может сделать с заявкой.
 *
 * Отдельно от карточки: те же действия показывают и в раскрытой
 * заявке, а написанные дважды они однажды разойдутся — в одном месте
 * появится кнопка, которой в другом не будет.
 */
export function RequestActions({
  request,
  onCancel,
  onChanged,
  lateDocuments = true,
}: {
  request: AbsenceRequest;
  onCancel: () => void;
  /** Справку приложили — список надо перечитать. */
  onChanged: () => void;
  /** Разрешает ли организация донести справку после одобрения. */
  lateDocuments?: boolean;
}) {
  const [getting, setGetting] = useState(false);
  const [showing, setShowing] = useState(false);
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  /** Какую бумагу только что отправили: строка под кнопкой. */
  const [sent, setSent] = useState<'application' | 'certificate' | null>(null);

  // Пока справку не приняли, её ждут — даже если одна бумага уже
  // лежит. Считать по количеству документов значило бы закрыть
  // человеку дорогу ровно там, где кадровик попросил принести другую.
  //
  // Справку спрашивают только у исходной заявки: продление и отмена
  // подтверждаются по ней же, и своих бумаг у них нет.
  const needsPaper =
    request.kind === 'CREATE' &&
    request.absence_type.requires_document &&
    request.certificate_status !== 'VERIFIED';
  const open = ACTIVE_STATUSES.includes(request.status);
  // Отменённая и отклонённая заявка ничего больше не требует. Бланк для
  // неё не печатают: подписанное заявление по отказанной заявке — это
  // бумага, которая потом всплывёт в переписке как действующая.
  const closed = request.stage === 'CANCELLED' || request.stage === 'REJECTED';

  // Справку доносят и после решения: больничный подтверждают по трём
  // пунктам сразу, а бумага приходит своим ходом. Запрет организации
  // касается только уже подтверждённой заявки.
  const canAttach = needsPaper && !closed && (open || lateDocuments);

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
  /**
   * Попросить бота прислать бумагу в чат.
   *
   * Не скачивание: вебвью Telegram не даёт сохранить файл, и нажатие
   * «скачать» заканчивалось ничем. Бот присылает сообщением — оттуда
   * бумагу и пересылают, и печатают, и она остаётся в переписке.
   */
  async function sendPaper(paper: 'application' | 'certificate') {
    if (getting || showing) return;
    const mark = paper === 'application' ? setGetting : setShowing;
    mark(true);
    setFailed(null);
    setSent(null);
    const answer = await api.sendAbsencePaper(request.id, paper);
    mark(false);
    if (!answer.ok) {
      setFailed(answer.message);
      return;
    }
    haptic('success');
    setSent(paper);
  }

  /**
   * Приложить справку прямо из карточки.
   *
   * Заявку подают в первый день болезни, а справку выдают при выписке —
   * между ними неделя. Возвращать человека в форму создания незачем:
   * заявка уже есть, не хватает только файла.
   */
  async function attach(file: File) {
    if (sending) return;
    const complaint = checkCertificate(file);
    if (complaint) {
      setFailed(complaint);
      return;
    }
    setSending(true);
    setFailed(null);
    const answer = await api.attachAbsenceDocument(request.id, file);
    setSending(false);
    if (!answer.ok) {
      setFailed(answer.message);
      return;
    }
    haptic('success');
    onChanged();
  }

  return (
    <>
    {failed && (
      <p className="field-error" role="alert">
        {failed}
      </p>
    )}

    <div className="rq-card__acts">
      {/* Справка первой строкой, пока её ждут: это единственное, что
          сейчас нужно от человека. */}
      {canAttach && (
        <label className="rq-act">
          <ClipIcon size={20} />
          <span>
            <b>
              {sending
                ? 'Прикрепляем…'
                : request.stage === 'NEEDS_FIX'
                  ? 'Приложить другую справку'
                  : 'Прикрепить справку'}
            </b>
            <small>
              {request.stage === 'NEEDS_FIX'
                ? 'Прежнюю не приняли'
                : 'Можно сделать позже'}
            </small>
          </span>
          {/* Системное окно открывает сам `label`, без единой строки
              JavaScript между касанием и вызовом: WebView считает
              пользовательским действием только само нажатие. */}
          <input
            type="file"
            accept="image/*,application/pdf"
            disabled={sending}
            onChange={(event) => {
              const picked = event.target.files?.[0] ?? null;
              // Сбрасываем до обработки: иначе второй выбор того же
              // файла не даст события и человек решит, что сломалось.
              event.target.value = '';
              if (picked) void attach(picked);
            }}
          />
        </label>
      )}

      {/* Приложенная справка. Она остаётся нужной и после решения:
          по ней сверяют период, её же просят прислать повторно. */}
      {request.certificate_status !== null && (
        <button
          type="button"
          className="rq-act"
          onClick={() => void sendPaper('certificate')}
        >
          <ClipIcon size={20} />
          <span>
            <b>{showing ? 'Отправляем…' : 'Прислать справку в чат'}</b>
            <small>
              {sent === 'certificate'
                ? 'Отправили — посмотрите чат с ботом'
                : request.certificate_status === 'VERIFIED'
                  ? 'Принята кадровиком'
                  : request.certificate_status === 'REJECTED'
                    ? 'Не принята — нужна другая'
                    : 'Ждёт проверки'}
            </small>
          </span>
        </button>
      )}

      {/* Бланк для печати. Оформляют заявку в телефоне, а подписывают
          бумагу: набирать её заново в Word после того, как всё уже
          введено, — лишняя работа, в которой ещё и ошибаются. */}
      {!closed && (
        <button
          type="button"
          className="rq-act"
          onClick={() => void sendPaper('application')}
        >
          <DocumentIcon size={20} />
          <span>
            <b>{getting ? 'Отправляем…' : 'Прислать заявление в чат'}</b>
            <small>
              {sent === 'application'
                ? 'Отправили — посмотрите чат с ботом'
                : 'Распечатать и подписать'}
            </small>
          </span>
        </button>
      )}

      {request.can_cancel && (
        <button type="button" className="rq-act rq-act--danger" onClick={onCancel}>
          <CloseIcon size={20} />
          <span>
            <b>Отменить заявку</b>
          </span>
        </button>
      )}
    </div>
    </>
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
  existing = null,
  onChanged,
  onCancelRequest,
}: {
  kind: AbsenceKind | null;
  options: AbsenceOptions | null;
  balance: number | null;
  fullName: string;
  onClose: () => void;
  onCreated: () => void;
  /**
   * Уже поданная заявка.
   *
   * Та же форма показывает и её — с теми же полями в том же порядке,
   * только заполненными и закрытыми на правку. Отдельный экран для
   * просмотра означал бы две разметки об одном и том же, которые
   * однажды разойдутся.
   */
  existing?: AbsenceRequest | null;
  /** Справку приложили — список надо перечитать. */
  onChanged?: () => void;
  onCancelRequest?: () => void;
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
    if (existing) {
      // Поля берутся из самой заявки: человек смотрит на то, что
      // отправил, а не на пустой бланк с её названием.
      setFirst(existing.first_day ?? '');
      setLast(existing.last_day ?? '');
      setComment(existing.comment ?? '');
      setFile(null);
      setAgreed(false);
      setError(null);
      setCreated(null);
      return;
    }
    const start = kind === 'SICK_LEAVE' ? '' : isoToday();
    setFirst(start);
    setLast(start);
    setComment('');
    setFile(null);
    setAgreed(false);
    setError(null);
    setCreated(null);
  }, [kind, existing]);

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

            {existing && (
              <p className={`rq-card__state rq-card__state--${
                (STAGE[existing.stage] ?? STAGE.PENDING).tone
              }`}>
                <i aria-hidden="true" />
                {(STAGE[existing.stage] ?? STAGE.PENDING).title}
              </p>
            )}

            <OptionalDateRange
              first={first}
              last={last}
              onFirst={setFirst}
              onLast={setLast}
              disabled={busy || Boolean(existing)}
              {...(!existing && first && last && last < first
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
                disabled={busy || Boolean(existing)}
              />
              {!existing && (
                <p className="sick-note">Диагноз указывать не нужно.</p>
              )}
            </div>

            {/* Справку у поданной заявки не выбирают заново: её
                прикладывают и открывают действиями ниже, и второй
                выбор файла здесь означал бы, что он куда-то денется. */}
            {!existing && (
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
            )}

            {!existing && (
              <AfterSubmitNote icon={<SendIcon size={22} />} title="После отправки">
                В Telegram придёт готовый шаблон заявления. Распечатайте его,
                подпишите и отправьте на электронную почту HR.
              </AfterSubmitNote>
            )}

            {existing && (
              <RequestActions
                request={existing}
                onCancel={() => onCancelRequest?.()}
                onChanged={() => onChanged?.()}
                {...(options?.policy.document_can_be_added_later !== undefined
                  ? { lateDocuments: options.policy.document_can_be_added_later }
                  : {})}
              />
            )}

            {error && (
              <p className="field-error" role="alert">
                {error}
              </p>
            )}
          </div>

          {/* Согласие и кнопка прижаты к низу: до них дотягиваются
              большим пальцем, а список полей над ними прокручивается.
              У поданной заявки подвала нет вовсе: соглашаться не с чем,
              и «создать» второй раз — это вторая заявка. */}
          {!existing && (
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
          )}
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
