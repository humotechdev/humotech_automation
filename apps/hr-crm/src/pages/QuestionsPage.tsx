/**
 * «Обращения»: вопросы сотрудников из Telegram и ответы HR.
 *
 * Не «Заявки»: отпуск, больничный и исправление отметки здесь только
 * обсуждаются, оформляются они на своих страницах.
 *
 * Одна белая рабочая поверхность, а внутри — три зоны без собственных
 * карточек: очередь, переписка, контекст с управлением. Разделяют их
 * только тонкие линии: это одно рабочее место, а не три виджета.
 *
 * Выбор, вкладка и фильтры живут в адресе и переживают обновление и
 * ссылку коллеге. Данные живые: поток событий CRM сообщает, какое
 * обращение изменилось, и страница перечитывает очередь, счётчики и,
 * если нужно, открытую переписку — обычными запросами, с текущими
 * фильтрами. Сигнал сам ничего не вставляет в список: сообщение из
 * обращения вне выбранной вкладки меняет числа, но не саму вкладку.
 * Без потока (сеть, старый браузер) — редкий резервный опрос. Ничего из
 * показанного не выбрасывается: ни прокрутка, ни набранный ответ.
 *
 * Подсказка из базы знаний — подсказка, а не ответ: она никогда не уходит
 * сотруднику сама. Кадровик открывает её, читает и сам решает, вставить
 * ли текст в ответ.
 */

import {
  memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
  type KeyboardEvent, type ReactNode,
} from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { AppShell, initials } from '../components/AppShell';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import { AppPopover, AppSelectField } from '../components/AppSelect';
import { useSession } from '../features/auth/session';
import { useBlock, type Block } from '../features/dashboard/data';
import { liveState, liveSupported, onLive, onLiveState, type LiveState } from '../features/realtime/events';
import { useStickyState } from '../features/shell/sticky';
import '../styles/questions.css';

/** Резервный опрос, пока потока событий нет. */
const FALLBACK_MS = 30_000;
/**
 * Опрос и при живом потоке, но редкий: обращение становится срочным или
 * просроченным само, по времени, и никакого события об этом нет.
 */
const SLOW_MS = 3 * 60_000;
/**
 * Окно склейки сигналов. Первый сигнал перечитывает СРАЗУ; сигналы,
 * пришедшие в течение окна после него, дают одно перечитывание в конце
 * окна, а не по одному на каждый.
 */
const COALESCE_MS = 150;
const PAGE = 30;
const MAX_LIMIT = 200;
/** Столько же принимает сервер; проверка здесь — чтобы не ждать отказа. */
const FILE_MAX_BYTES = 10 * 1024 * 1024;
const FILE_TYPES = ['application/pdf', 'image/png', 'image/jpeg'];

const TABS: { key: api.QuestionStatus; title: string; counted: boolean }[] = [
  { key: 'NEW', title: 'Новые', counted: true },
  { key: 'IN_PROGRESS', title: 'В работе', counted: true },
  { key: 'WAITING_EMPLOYEE', title: 'Ждут сотрудника', counted: true },
  // Закрытых сотни, и число здесь ничего не требует от кадровика.
  { key: 'CLOSED', title: 'Закрытые', counted: false },
];

const QUICK = [
  { key: 'all', title: 'Все' },
  { key: 'unanswered', title: 'Без ответа' },
  { key: 'urgent', title: 'Срочные' },
] as const;
type QuickKey = (typeof QUICK)[number]['key'];

const STATUS_TITLE: Record<api.QuestionStatus, string> = {
  NEW: 'Новое',
  IN_PROGRESS: 'В работе',
  WAITING_EMPLOYEE: 'Ждёт сотрудника',
  CLOSED: 'Закрыто',
};

/** В строке очереди — коротко: длинная плашка съедала тему. */
const ROW_STATUS: Record<api.QuestionStatus, string> = {
  ...STATUS_TITLE,
  WAITING_EMPLOYEE: 'Ждёт ответа',
};

const CATEGORY_TITLE: Record<api.QuestionCategory, string> = {
  VACATION: 'Отпуск',
  SICK_LEAVE: 'Больничный',
  ATTENDANCE: 'Посещаемость',
  SCHEDULE: 'График',
  SALARY: 'Зарплата',
  DOCUMENTS: 'Документы',
  TELEGRAM: 'Telegram',
  OTHER: 'Другое',
};

const PRIORITY_TITLE: Record<api.QuestionPriority, string> = {
  LOW: 'Низкий',
  NORMAL: 'Обычный',
  HIGH: 'Высокий',
  URGENT: 'Срочный',
};

/**
 * Состояние доставки ответа — ровно то, что известно.
 *
 * «Отправлено» — Telegram принял сообщение от бота. Подтверждения
 * прочтения бот от Telegram не получает вовсе, поэтому «Прочитано» здесь
 * не пишется никогда: оно было бы догадкой.
 */
const DELIVERY_TITLE: Record<api.DeliveryStatus, string> = {
  QUEUED: 'Отправляется',
  DELIVERED: 'Отправлено',
  READ: 'Отправлено',
  FAILED: 'Не доставлено',
  UNKNOWN: 'Доставка неизвестна',
};

const TELEGRAM_REASON: Record<string, string> = {
  not_linked: 'Telegram не подключён',
  pending_confirmation: 'привязка Telegram ждёт подтверждения',
  link_revoked: 'привязка Telegram отозвана',
  link_blocked: 'Telegram заблокирован',
  blocked_by_user: 'сотрудник заблокировал бота',
  employee_inactive: 'сотрудник не работает',
  no_assignment: 'у сотрудника нет назначения',
  organization_inactive: 'организация неактивна',
  attachment_unavailable: 'файл не удалось получить',
  // Причины, которые пишет очередь отправки, — тоже словами: код на
  // экране HR ни о чём не говорит.
  attachment_rejected: 'файл не прошёл проверку',
  attachment_not_found: 'файл не найден',
  cancelled_by_operator: 'отправку отменили вручную',
  demo_account: 'это демонстрационный сотрудник',
  stale_lock: 'бот не подтвердил отправку',
  stub_forced_failure: 'проверочная ошибка стенда',
};

/**
 * Причина недоставки словами. Неизвестный код — общей фразой, а не как
 * есть: имя класса ошибки Telegram («TelegramBadRequest») HR не поможет.
 */
function deliveryReason(code: string): string {
  if (TELEGRAM_REASON[code]) return TELEGRAM_REASON[code]!;
  if (code.startsWith('Telegram')) return 'Telegram отклонил сообщение';
  return 'ошибка отправки';
}

/** Состояние дня словами посещаемости — теми же, что на её странице. */
const TODAY_TITLE: Record<string, [string, 'ok' | 'wait' | 'off' | 'bad']> = {
  IN_OFFICE: ['Сегодня на работе', 'ok'],
  LEFT: ['Был, уже ушёл', 'off'],
  LATE: ['Предупредил об опоздании', 'wait'],
  NOT_COME: ['Сегодня не отмечался', 'bad'],
  DAY_OFF: ['Выходной', 'off'],
  NO_SCHEDULE: ['Нет отметки', 'off'],
  SICK_LEAVE: ['На больничном', 'off'],
  VACATION: ['В отпуске', 'off'],
  OTHER_ABSENCE: ['Отсутствует', 'off'],
};

/** Короткий набор, уместный в переписке HR. */
const EMOJI = ['🙂', '👍', '🙏', '✅', '📄', '📌', '⏰', '❗'];

/** Вид материала базы знаний — подписью под названием статьи. */
const SOURCE_KIND: Record<string, string> = {
  POLICY: 'Положение',
  FAQ: 'Готовый ответ',
  DOCUMENT: 'Документ',
  INSTRUCTION: 'Инструкция',
  TEMPLATE: 'Шаблон',
};

const CLOSE_REASONS = ['Вопрос решён', 'Дубликат обращения', 'Не по адресу HR', 'Другое'];

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const MONTHS_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

type Act = (label: string, action: () => Promise<api.Question>, done?: string) => Promise<api.Question | null>;

/** Свой ответ, уже показанный «Отправляется», но ещё не подтверждённый сервером. */
type Pending = { key: string; questionId: string; text: string; file: string | null; at: string };

/** Последнее сообщение сотрудника в переписке — то, что HR увидел. */
function lastEmployeeMessage(question: api.Question): string | null {
  for (let index = question.messages.length - 1; index >= 0; index -= 1) {
    const one = question.messages[index];
    if (one?.kind === 'EMPLOYEE') return one.id;
  }
  return null;
}

/**
 * До нижнего края ближе этого — «читает последнее», и лента следует за
 * новыми сообщениями. Не строгое равенство: дробные пиксели масштаба и
 * выросшая строка статуса не должны считаться «ушёл читать историю».
 */
const BOTTOM_SLACK_PX = 80;
/** Сколько длится плавная прокрутка к новому сообщению, с запасом. */
const FOLLOW_MS = 700;

function reducedMotion(): boolean {
  return Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
}

// --- страница ----------------------------------------------------------------

export function QuestionsPage() {
  const session = useSession();
  const user = session.status === 'authenticated' ? session.user : null;
  const canRead = (user?.permissions ?? []).includes('questions.read');

  const [params, setParams] = useSearchParams();
  const status: api.QuestionStatus = TABS.find((tab) => tab.key === params.get('status'))?.key ?? 'NEW';
  const office = params.get('office_id') ?? '';
  const assignee = params.get('assignee') ?? '';
  const search = params.get('q') ?? '';
  const quick: QuickKey = QUICK.find((one) => one.key === params.get('quick'))?.key ?? 'all';
  const picked = params.get('id') ?? '';

  const patch = useCallback(
    (changes: Record<string, string | null>) => {
      setParams(
        (was) => {
          const next = new URLSearchParams(was);
          for (const [key, value] of Object.entries(changes)) {
            if (value) next.set(key, value);
            else next.delete(key);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  // --- поиск с задержкой: запрос на каждую букву перегружал бы сервер ---
  const [draftSearch, setDraftSearch] = useState(search);
  useEffect(() => setDraftSearch(search), [search]);
  useEffect(() => {
    if (draftSearch === search) return;
    const timer = window.setTimeout(() => patch({ q: draftSearch.trim() || null, id: null }), 350);
    return () => window.clearTimeout(timer);
  }, [draftSearch, search, patch]);

  // --- резервный опрос: часто без потока, редко — с ним ---
  const [tick, setTick] = useState(0);
  const [stream, setStream] = useState<LiveState>(liveState());
  useEffect(() => onLiveState(setStream), []);
  const live = stream === 'live';
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) setTick((n) => n + 1);
    }, live ? SLOW_MS : FALLBACK_MS);
    return () => window.clearInterval(timer);
  }, [live]);

  const filters = useMemo<api.QuestionQuery>(
    () => ({
      ...(office ? { office_id: office } : {}),
      ...(assignee ? { assignee } : {}),
      ...(search ? { search } : {}),
    }),
    [office, assignee, search],
  );
  const filterKey = JSON.stringify(filters);
  const listKey = `${filterKey}|${status}|${quick}`;

  const [pages, setPages] = useState(1);
  useEffect(() => setPages(1), [listKey]);
  const limit = String(Math.min(PAGE * pages, MAX_LIMIT));

  const [list, reloadList, listRefresh] = useBlock(
    (signal) => api.questionList(
      { ...filters, status, ...(quick !== 'all' ? { quick } : {}), limit },
      signal,
    ),
    `${listKey}|${limit}|${tick}`,
    canRead,
  );
  const [counts, reloadCounts] = useBlock(
    (signal) => api.questionCounts({ ...filters, status }, signal),
    `${filterKey}|${status}|${tick}`,
    canRead,
  );
  const [offices] = useBlock(
    (signal) => api.offices(signal).then((body) => body.items.filter((one) => one.status === 'ACTIVE')),
    'offices',
    canRead,
  );
  const [assignees] = useBlock((signal) => api.questionAssignees(signal), 'question-assignees', canRead);

  const items = list.state === 'ready' ? list.data.items : [];
  // Без выбора в адресе открыто первое обращение очереди: на широком
  // экране пустая середина при полной очереди — лишний клик. Выбранное так
  // обращение закрепляется: иначе сообщение в другом чате, поднявшее его
  // строку наверх, молча переключило бы HR в чужой разговор. Новая
  // вкладка или фильтр — выбор заново.
  const firstId = items[0]?.id ?? '';
  const [pinned, setPinned] = useState('');
  useEffect(() => setPinned(''), [listKey]);
  useEffect(() => {
    if (!picked && !pinned && firstId) setPinned(firstId);
  }, [picked, pinned, firstId]);
  const currentId = picked || pinned || firstId;

  const [detailBlock, reloadDetail] = useBlock(
    (signal) => api.question(currentId, signal),
    `${currentId}|${tick}`,
    canRead && Boolean(currentId),
  );
  const [contextBlock, reloadContext] = useBlock(
    (signal) => api.questionContext(currentId, signal),
    `${currentId}|${Math.floor(tick / 4)}`,
    canRead && Boolean(currentId),
  );

  // --- живые сигналы -------------------------------------------------------
  //
  // Сигнал говорит только «какое обращение изменилось». Очередь и счётчики
  // перечитываются всегда — с текущими фильтрами, поэтому обращение вне
  // вкладки меняет числа, но не список. Переписка — только если сигнал про
  // открытое обращение. Серия сигналов даёт одно перечитывание.
  const openRef = useRef(currentId);
  openRef.current = currentId;
  const reloadsRef = useRef({ reloadList, reloadCounts, reloadDetail, reloadContext });
  reloadsRef.current = { reloadList, reloadCounts, reloadDetail, reloadContext };
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    let queued = false;
    let queuedDetail = false;
    let queuedAll = false;
    const run = (detail: boolean, everything: boolean) => {
      const reload = reloadsRef.current;
      reload.reloadList();
      reload.reloadCounts();
      if (detail || everything) reload.reloadDetail();
      if (everything) reload.reloadContext();
    };
    const open = () => {
      timer = setTimeout(() => {
        timer = null;
        if (!queued) return;
        const detail = queuedDetail;
        const everything = queuedAll;
        queued = false;
        queuedDetail = false;
        queuedAll = false;
        run(detail, everything);
        open();
      }, COALESCE_MS);
    };
    const stop = onLive((signal) => {
      const everything = signal.type === 'resync';
      const detail = !everything && signal.id === openRef.current;
      if (timer === null) {
        // Первый сигнал — перечитать сразу, без ожидания.
        run(detail, everything);
        open();
        return;
      }
      queued = true;
      queuedDetail ||= detail;
      queuedAll ||= everything;
    });
    return () => {
      stop();
      if (timer !== null) clearTimeout(timer);
    };
  }, []);

  // Ответ действия показывается сразу, не дожидаясь следующего опроса.
  const [override, setOverride] = useState<api.Question | null>(null);
  useEffect(() => setOverride(null), [detailBlock]);
  const detail: api.Question | null = override?.id === currentId
    ? override
    : detailBlock.state === 'ready' && detailBlock.data.id === currentId
      ? detailBlock.data
      : null;
  const context = contextBlock.state === 'ready' && detail && contextBlock.data.employee.id === detail.employee.id
    ? contextBlock.data
    : null;

  // Выпадающие списки рисуются поверх страницы, вне листа: шрифт листа
  // до них не доходит. Класс на body — пока открыта эта страница.
  useEffect(() => {
    document.body.classList.add('tk-type');
    return () => document.body.classList.remove('tk-type');
  }, []);

  // Увидел — значит прочитал. Отметка — по последнему сообщению сотрудника
  // на экране, а не «по обращению раз и навсегда»: новое сообщение в том же
  // обращении снова требует отметки. Сервер отметку не снимет, если после
  // показанного сообщения пришло ещё одно, которого HR не видел.
  //
  // Не отмечается скрытое: вкладка в фоне или узкий экран, где первое
  // обращение выбрано лишь по умолчанию и переписка не показана.
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden');
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);
  const readDone = useRef(new Set<string>());
  useEffect(() => {
    if (!detail || !detail.unread || !visible) return;
    if (!picked && window.matchMedia?.('(max-width: 860px)').matches) return;
    const seen = lastEmployeeMessage(detail);
    const mark = `${detail.id}:${seen ?? ''}`;
    if (readDone.current.has(mark)) return;
    readDone.current.add(mark);
    api.readQuestion(detail.id, seen)
      .then(() => {
        reloadList();
        reloadCounts();
      })
      .catch(() => readDone.current.delete(mark));
  }, [detail, picked, visible, reloadList, reloadCounts]);

  const [busy, setBusy] = useState<string | null>(null);
  /**
   * Чьё действие идёт и чьё сообщение показано.
   *
   * Ответ уходит на сервер секунду-другую, и за это время кадровик
   * может открыть другое обращение. Итог (ошибка, «Повторить») и
   * надпись «Отправляем…» относятся к тому обращению, где нажали кнопку,
   * а не к тому, что открыто, когда пришёл ответ сервера.
   */
  const [busyFor, setBusyFor] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: 'error' | 'ok'; text: string; id: string } | null>(null);
  const [closing, setClosing] = useState(false);
  const [sideOpen, setSideOpen] = useState(false);
  useEffect(() => {
    setNotice(null);
    setClosing(false);
    setSideOpen(false);
  }, [currentId]);

  const act: Act = useCallback(
    async (label, action, done) => {
      const owner = openRef.current;
      setBusy(label);
      setBusyFor(owner);
      setNotice(null);
      try {
        const fresh = await action();
        setOverride(fresh);
        reloadDetail();
        reloadList();
        reloadCounts();
        reloadContext();
        if (done) setNotice({ tone: 'ok', text: done, id: owner });
        return fresh;
      } catch (error) {
        setNotice({ tone: 'error', text: failureText(error, label), id: owner });
        // Конфликт, обрыв и сбой сервера — исход мог быть не тем, что
        // кажется: перечитываем обращение. Ответ, который всё-таки
        // сохранился, придёт в ленте; повтор с тем же ключом второго
        // сообщения не создаст.
        if (error instanceof ApiFailure && ['conflict', 'offline', 'server'].includes(error.kind)) {
          reloadDetail();
        }
        return null;
      } finally {
        setBusy(null);
        setBusyFor(null);
      }
    },
    [reloadDetail, reloadList, reloadCounts, reloadContext],
  );

  // --- свежие строки очереди: короткая подсветка, без мигания всей страницы ---
  const seenRows = useRef<Map<string, string> | null>(null);
  const [fresh, setFresh] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    if (list.state !== 'ready') return;
    const before = seenRows.current;
    const now = new Map(list.data.items.map((row) => [row.id, row.last_message_at]));
    seenRows.current = now;
    // Первый показ — не «новое»: подсвечивается только изменившееся.
    if (before === null) return;
    const changed = [...now].filter(([id, at]) => before.get(id) !== at).map(([id]) => id);
    if (changed.length === 0) return;
    setFresh(new Set(changed));
    const timer = window.setTimeout(() => setFresh(new Set()), 1600);
    return () => window.clearTimeout(timer);
  }, [list]);

  // --- ответ «Отправляется» до ответа сервера ---
  const [pending, setPending] = useState<Pending[]>([]);
  const canResend = (user?.permissions ?? []).includes('notifications.manage');
  const resend = useCallback(async (notificationId: string) => {
    try {
      await api.retryNotification(notificationId);
      setNotice(null);
    } catch (error) {
      setNotice({ tone: 'error', text: failureText(error), id: openRef.current });
    }
    reloadDetail();
  }, [reloadDetail]);

  // Набранный ответ у каждого обращения свой и не теряется при переходе —
  // ни к другому обращению, ни в другой раздел CRM.
  const [replies, setReplies] = useStickyState<Record<string, string>>('questions.replies', {});
  const reply = replies[currentId] ?? '';
  const setReply = useCallback(
    (value: string) => setReplies((was) => ({ ...was, [currentId]: value })),
    [currentId, setReplies],
  );
  // Свои неподтверждённые реплики открытого обращения. Тот же массив, пока
  // они не изменились: лента (`Thread`, memo) не перерисовывается на
  // каждую букву в поле ответа.
  const pendingHere = useMemo(
    () => pending.filter((one) => one.questionId === currentId),
    [pending, currentId],
  );
  const onSending = useCallback(
    (one: Pending) => setPending((was) => [...was.filter((x) => x.key !== one.key), one]),
    [],
  );
  const onSent = useCallback((key: string) => setPending((was) => was.filter((x) => x.key !== key)), []);


  if (!canRead) {
    return (
      <AppShell breadcrumb="Обращения" section="questions">
        <div className="tk">
          <section className="tk-sheet">
            <p className="tk-empty">Нет права просматривать обращения.</p>
          </section>
        </div>
      </AppShell>
    );
  }

  const tabCount = (key: api.QuestionStatus) =>
    counts.state === 'ready' ? counts.data.statuses[key] : null;
  const quickCount = (key: QuickKey) =>
    counts.state === 'ready' && key !== 'all' ? counts.data.quick[key] : null;
  const total = counts.state === 'ready' ? counts.data.quick.all : null;
  const filtered = Boolean(search || office || assignee || quick !== 'all');

  const closeWith = async (reason: string) => {
    if (!detail) return;
    const done = await act('close', () => api.closeQuestion(detail.id, reason), 'Обращение закрыто');
    if (done) setClosing(false);
  };

  return (
    <AppShell breadcrumb="Обращения" section="questions">
      <div className="tk">
        <section className="tk-sheet">
          <header className="tk-head">
            <div>
              <h1 className="tk-head__title">Обращения</h1>
              <p className="tk-head__sub">Вопросы сотрудников и ответы HR</p>
              {/* Поток событий упал: показанное — последнее полученное, а
                  не «обращений нет». Переподключение идёт само. */}
              {stream === 'down' && liveSupported() && (
                <p className="tk-offline" role="status">
                  Нет связи для живых обновлений — переподключаемся. Показаны последние полученные данные.
                </p>
              )}
            </div>
            <div className="tk-head__tools">
              <span className="tk-filter">
                <AppIcon name="building" size={16} />
                <AppSelectField className="tk-select" label="Офис" value={office}
                                onChange={(value) => patch({ office_id: value || null, id: null })}>
                  <option value="">Все офисы</option>
                  {offices.state === 'ready' && offices.data.map((one) => (
                    <option key={one.id} value={one.id}>{one.name}</option>
                  ))}
                </AppSelectField>
              </span>
              <span className="tk-filter">
                <AppIcon name="user" size={16} />
                <AppSelectField className="tk-select tk-select--wide" label="Ответственный" value={assignee}
                                onChange={(value) => patch({ assignee: value || null, id: null })}>
                  <option value="">Ответственный: Все</option>
                  <option value="none">Не назначен</option>
                  {assignees.state === 'ready' && assignees.data.items.map((one) => (
                    <option key={one.id} value={one.id}>{one.name}</option>
                  ))}
                </AppSelectField>
              </span>
              <button type="button"
                      className={listRefresh.busy ? 'tk-icon tk-icon--spin' : 'tk-icon'}
                      aria-label="Обновить" onClick={() => setTick((n) => n + 1)}>
                <AppIcon name="refresh" size={20} />
              </button>
            </div>
          </header>

          <div className="tk-tabs" role="tablist" aria-label="Состояние обращений">
            {TABS.map((tab) => {
              const count = tab.counted ? tabCount(tab.key) : null;
              return (
                <button key={tab.key} type="button" role="tab" aria-selected={status === tab.key}
                        className={status === tab.key ? 'tk-tab tk-tab--on' : 'tk-tab'}
                        onClick={() => patch({ status: tab.key === 'NEW' ? null : tab.key, quick: null, id: null })}>
                  {tab.title}
                  {count !== null && <span className="tk-tab__n">{count}</span>}
                </button>
              );
            })}
          </div>

          <div className={picked ? 'tk-work tk-work--picked' : 'tk-work'}>
            {/* --- очередь --- */}
            <section className="tk-queue" aria-label="Очередь обращений">
              <div className="tk-queue__tools">
                <label className="tk-find">
                  <AppIcon name="search" size={18} />
                  <input type="search" aria-label="Поиск обращений"
                         placeholder="Поиск по обращениям…"
                         value={draftSearch}
                         onChange={(event) => setDraftSearch(event.target.value)} />
                </label>
                <div className="tk-chips">
                  {QUICK.map((one) => {
                    const count = quickCount(one.key);
                    return (
                      <button key={one.key} type="button" aria-pressed={quick === one.key}
                              className={`tk-chip tk-chip--${one.key}${quick === one.key ? ' tk-chip--on' : ''}`}
                              onClick={() => patch({ quick: one.key === 'all' ? null : one.key, id: null })}>
                        {one.title}
                        {count !== null && <span className="tk-chip__n">{count}</span>}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className={listRefresh.busy && list.state === 'ready' ? 'tk-list tk-list--busy' : 'tk-list'}>
                {list.state === 'loading' && <QueueSkeleton />}
                {list.state === 'denied' && <p className="tk-empty">Нет доступа к обращениям.</p>}
                {list.state === 'error' && (
                  <div className="tk-empty">
                    <p>Не удалось загрузить очередь.</p>
                    <button type="button" className="tk-link" onClick={reloadList}>Повторить</button>
                  </div>
                )}
                {list.state === 'ready' && items.length === 0 && (
                  <div className="tk-empty tk-empty--queue">
                    <AppIcon name={filtered ? 'blank-search' : 'chat'} size={20} />
                    <p>{filtered ? 'По этим условиям обращений нет' : 'В этой очереди обращений нет'}</p>
                  </div>
                )}
                {items.length > 0 && (
                  <ul className="tk-rows">
                    {items.map((item) => (
                      <QueueRow key={item.id} row={item} on={item.id === currentId}
                                fresh={fresh.has(item.id)}
                                onPick={(id) => patch({ id })} />
                    ))}
                  </ul>
                )}
              </div>

              <footer className="tk-queue__foot">
                <span>
                  {list.state === 'ready' && (total !== null ? `${items.length} из ${total}` : `${items.length}`)}
                  {listRefresh.failed && list.state === 'ready' && <em> · не обновилось</em>}
                </span>
                {list.state === 'ready' && list.data.has_more && Number(limit) < MAX_LIMIT && (
                  <button type="button" className="tk-link" onClick={() => setPages((n) => n + 1)}>
                    Показать ещё
                  </button>
                )}
              </footer>
            </section>

            {/* --- переписка --- */}
            <section className="tk-talk" aria-label="Переписка">
              {!currentId && (
                <div className="tk-empty tk-empty--center">
                  <AppIcon name="chat" size={20} />
                  <p>Выберите обращение в очереди</p>
                </div>
              )}
              {currentId && !detail && (
                detailBlock.state === 'error'
                  ? (
                    <div className="tk-empty tk-empty--center">
                      <p>Не удалось открыть обращение.</p>
                      <button type="button" className="tk-link" onClick={reloadDetail}>Повторить</button>
                    </div>
                  )
                  : detailBlock.state === 'denied'
                    ? <p className="tk-empty tk-empty--center">Нет доступа к этому обращению.</p>
                    : <ThreadSkeleton />
              )}
              {detail && (
                <Conversation
                  key={detail.id}
                  question={detail}
                  context={context}
                  busy={busy}
                  sending={busy === 'reply' && busyFor === detail.id}
                  notice={notice && notice.id === detail.id ? notice : null}
                  reply={reply}
                  setReply={setReply}
                  act={act}
                  pending={pendingHere}
                  onSending={onSending}
                  onSent={onSent}
                  canResend={canResend}
                  onResend={resend}
                  onBack={() => patch({ id: null })}
                  onContext={() => setSideOpen(true)}
                />
              )}
            </section>

            {/* --- контекст и управление --- */}
            <aside className={sideOpen ? 'tk-side tk-side--open' : 'tk-side'} aria-label="Контекст сотрудника">
              <button type="button" className="tk-icon tk-side__close" aria-label="Скрыть контекст"
                      onClick={() => setSideOpen(false)}>
                <AppIcon name="close" size={18} />
              </button>
              {currentId && detail ? (
                <Side
                  key={detail.id}
                  question={detail}
                  block={contextBlock}
                  context={context}
                  assignees={assignees.state === 'ready' ? assignees.data.items : []}
                  busy={busy}
                  act={act}
                  onClose={() => setClosing(true)}
                  onInsert={(text) => setReply(reply ? `${reply}\n${text}` : text)}
                  onRetry={reloadContext}
                />
              ) : currentId ? (
                detailBlock.state === 'error'
                  ? <p className="tk-empty tk-empty--center">Не удалось загрузить обращение.</p>
                  : <SideSkeleton />
              ) : (
                <div className="tk-empty tk-empty--center">
                  <AppIcon name="user" size={20} />
                  <p>Контекст появится после выбора обращения</p>
                </div>
              )}
            </aside>
            {sideOpen && (
              <button type="button" className="tk-scrim" aria-label="Скрыть контекст"
                      onClick={() => setSideOpen(false)} />
            )}
          </div>
        </section>
      </div>

      {closing && detail && (
        <CloseDialog busy={busy === 'close'} onCancel={() => setClosing(false)} onClose={closeWith} />
      )}
    </AppShell>
  );
}

// --- очередь -------------------------------------------------------------------

function QueueRow({ row, on, fresh = false, onPick }: {
  row: api.QuestionRow; on: boolean; fresh?: boolean; onPick: (id: string) => void;
}) {
  const classes = ['tk-row'];
  if (on) classes.push('tk-row--on');
  // Строка, которую только что изменило новое сообщение: короткая мягкая
  // подсветка. Мигать всей очередью незачем — изменилась одна строка.
  if (fresh) classes.push('tk-row--fresh');
  if (row.unread) classes.push('tk-row--unread');
  return (
    <li>
      <button type="button" className={classes.join(' ')} aria-current={on ? 'true' : undefined}
              onClick={() => onPick(row.id)}>
        <Photo id={row.employee.id} name={row.employee.full_name} has={row.employee.has_photo} className="tk-face" />
        <span className="tk-row__main">
          <span className="tk-row__top">
            <span className="tk-row__name">{row.employee.full_name}</span>
            {row.unread && <span className="tk-dot" role="img" aria-label="Не прочитано" />}
            {(row.priority === 'URGENT' || row.overdue) && (
              <span className="tk-flag tk-flag--urgent">{row.priority === 'URGENT' ? 'Срочно' : 'Просрочено'}</span>
            )}
            <time className="tk-row__time" dateTime={row.last_message_at}>{listMoment(row.last_message_at)}</time>
          </span>
          <span className="tk-row__mid">
            <span className="tk-row__topic">{row.topic}</span>
            <span className={`tk-status tk-status--${row.status}`}>{ROW_STATUS[row.status]}</span>
          </span>
          <span className="tk-row__snip">
            {row.last_message_kind === 'HR' && <b>HR: </b>}
            {row.snippet}
          </span>
        </span>
      </button>
    </li>
  );
}

function QueueSkeleton() {
  return (
    <ul className="tk-rows" aria-label="Загрузка очереди">
      {[0, 1, 2, 3].map((one) => (
        <li key={one} className="tk-row tk-row--ghost">
          <span className="tk-ghost tk-ghost--round" />
          <span className="tk-row__main">
            <span className="tk-ghost" />
            <span className="tk-ghost tk-ghost--short" />
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Заглушка переписки: шапка и несколько реплик по сторонам, без текста. */
function ThreadSkeleton() {
  return (
    <div className="tk-talk__body tk-sk-talk">
      <span className="visually-hidden" role="status">Загружаем переписку…</span>
      <div aria-hidden="true">
        <div className="tk-sk-head">
          <span className="tk-ghost tk-ghost--round" />
          <span className="tk-sk-lines"><span className="tk-ghost" /><span className="tk-ghost tk-ghost--short" /></span>
        </div>
        <div className="tk-sk-thread">
          <span className="tk-sk-bubble" />
          <span className="tk-sk-bubble tk-sk-bubble--mine" />
          <span className="tk-sk-bubble tk-sk-bubble--wide" />
        </div>
      </div>
    </div>
  );
}

/** Заглушка контекста: сотрудник и два поля управления. */
function SideSkeleton() {
  return (
    <div className="tk-side__body">
      <span className="visually-hidden" role="status">Загружаем данные обращения…</span>
      <div aria-hidden="true" className="tk-sk-side">
        <div className="tk-ghost-block"><span className="tk-ghost tk-ghost--round" /><span className="tk-ghost" /></div>
        <span className="tk-ghost" />
        <span className="tk-ghost tk-ghost--short" />
        <span className="tk-ghost tk-sk-field" />
        <span className="tk-ghost tk-sk-field" />
      </div>
    </div>
  );
}

// --- переписка ----------------------------------------------------------------

function Conversation({
  question, context, busy, sending, notice, reply, setReply, act, onBack, onContext,
  pending, onSending, onSent, canResend, onResend,
}: {
  question: api.Question;
  context: api.QuestionContext | null;
  busy: string | null;
  /** Ответ из ЭТОГО обращения сейчас уходит на сервер. */
  sending: boolean;
  notice: { tone: 'error' | 'ok'; text: string } | null;
  reply: string;
  setReply: (value: string) => void;
  act: Act;
  onBack: () => void;
  onContext: () => void;
  pending: Pending[];
  onSending: (one: Pending) => void;
  onSent: (key: string) => void;
  canResend: boolean;
  onResend: (notificationId: string) => Promise<void>;
}) {
  const [menu, setMenu] = useState(false);
  const thread = useRef<HTMLDivElement | null>(null);
  const actions = question.actions;
  const id = question.id;

  // Реплики, которые сервер уже вернул в ленте, из «отправляется» уходят:
  // одна и та же реплика не рисуется дважды. Тот же массив, пока не
  // изменились ни свои реплики, ни лента: `Thread` не перерисовывается.
  const waiting = useMemo(() => {
    const known = new Set(question.messages.map((one) => one.client_request_id).filter(Boolean));
    return pending.filter((one) => !known.has(one.key));
  }, [pending, question.messages]);

  // --- прокрутка ---
  //
  // Где был HR ДО нового сообщения, известно по последней прокрутке:
  // `atBottom` обновляется на каждом событии scroll, то есть до того, как
  // React вставит новую реплику и вырастет высота ленты. Решение:
  //
  //   * первое открытие — сразу вниз, без анимации;
  //   * новая реплика в конце ленты, а HR был у нижнего края — лента
  //     плавно доезжает до неё (при уменьшенном движении — сразу);
  //   * новая реплика, а HR читает историю — scrollTop не трогается
  //     вовсе, внизу появляется «К новым сообщениям»;
  //   * свой только что отправленный ответ — вниз всегда. Ответ коллеги
  //     из другого окна — как любая чужая реплика;
  //   * перечитывание без новых реплик, смена статуса доставки, время —
  //     не прокручивают ничего.
  const atBottom = useRef(true);
  /** Идёт плавная прокрутка вниз: её промежуточные положения — не «ушёл читать». */
  const following = useRef(0);
  const [unseen, setUnseen] = useState(0);
  const sentKeys = useRef(new Set<string>());
  for (const one of pending) sentKeys.current.add(one.key);
  // Реплики первого показа — история: её не проявляем. Новыми считаются
  // пришедшие потом (по `id`), кроме своей реплики, сменившей
  // «Отправляется», — она встаёт на то же место и проявляться второй раз
  // не должна. Строки привязаны к `id` и не пересоздаются, поэтому
  // перечитывание с теми же сообщениями ничего не анимирует заново.
  const history = useRef<Set<string> | null>(null);
  if (history.current === null) history.current = new Set(question.messages.map((one) => one.id));
  const isNew = useCallback(
    (message: api.QuestionMessage) =>
      !history.current!.has(message.id)
      && !(message.client_request_id != null && sentKeys.current.has(message.client_request_id)),
    [],
  );

  const toBottom = useCallback((smooth: boolean) => {
    const box = thread.current;
    if (!box) return;
    atBottom.current = true;
    setUnseen(0);
    const glide = smooth && !reducedMotion();
    window.clearTimeout(following.current);
    following.current = glide ? window.setTimeout(() => { following.current = 0; }, FOLLOW_MS) : 0;
    if (typeof box.scrollTo === 'function') {
      box.scrollTo({ top: box.scrollHeight, behavior: glide ? 'smooth' : 'auto' });
    } else {
      box.scrollTop = box.scrollHeight;
    }
  }, []);
  useEffect(() => () => window.clearTimeout(following.current), []);

  const lastMessage = question.messages[question.messages.length - 1];
  const tail = waiting.length > 0
    ? `pending:${waiting[waiting.length - 1]!.key}`
    : lastMessage?.id ?? '';
  const count = question.messages.length + waiting.length;
  const previous = useRef<{ tail: string; count: number } | null>(null);
  useLayoutEffect(() => {
    const box = thread.current;
    const before = previous.current;
    previous.current = { tail, count };
    if (!box) return;
    if (before === null) {
      box.scrollTop = box.scrollHeight;
      atBottom.current = true;
      return;
    }
    // Новое — только то, что встало в конец ленты. Замена «отправляется»
    // на сохранённую реплику и любые правки внутри ленты новым не считаются.
    if (tail === before.tail || count <= before.count) return;
    const own = waiting.length > 0
      || (lastMessage?.client_request_id != null
        && sentKeys.current.has(lastMessage.client_request_id));
    if (own || atBottom.current) {
      toBottom(true);
      return;
    }
    setUnseen((n) => n + (count - before.count));
  }, [tail, count, waiting.length, lastMessage, toBottom]);
  const onScroll = () => {
    const box = thread.current;
    if (!box) return;
    const near = box.scrollHeight - box.scrollTop - box.clientHeight <= BOTTOM_SLACK_PX;
    atBottom.current = near || following.current !== 0;
    if (near) setUnseen(0);
  };

  // Поле ответа выросло, появилось уведомление — лента стала ниже. Если
  // человек был внизу, последнее сообщение остаётся на виду, а не
  // уезжает под поле ввода.
  useEffect(() => {
    const box = thread.current;
    if (!box || typeof ResizeObserver === 'undefined') return;
    const watcher = new ResizeObserver(() => {
      if (atBottom.current && following.current === 0) box.scrollTop = box.scrollHeight;
    });
    watcher.observe(box);
    return () => watcher.disconnect();
  }, []);

  const where = [question.office?.name ?? context?.employee.office?.name, context?.employee.department]
    .filter(Boolean).join(' · ');

  return (
    <div className="tk-talk__body">
      <header className="tk-talk__head">
        <button type="button" className="tk-icon tk-only-small" aria-label="К очереди" onClick={onBack}>
          <AppIcon name="back" size={18} />
        </button>
        <Photo id={question.employee.id} name={question.employee.full_name} has={question.employee.has_photo} className="tk-face tk-face--md" />
        <div className="tk-talk__who">
          <p className="tk-talk__name">{question.employee.full_name}</p>
          <p className="tk-talk__where">{where || 'Офис не указан'}</p>
        </div>
        <span className={`tk-status tk-status--${question.status} tk-status--lg`}>{STATUS_TITLE[question.status]}</span>
        <button type="button" className="tk-icon tk-only-narrow" aria-label="Контекст сотрудника" onClick={onContext}>
          <AppIcon name="user" size={18} />
        </button>
        {(actions.priority || actions.category) && (
          <span className="tk-pop">
            <button type="button" className="tk-icon" aria-label="Другие действия"
                    aria-expanded={menu} onClick={() => setMenu((was) => !was)}>
              <AppIcon name="dots" size={18} />
            </button>
            <AppPopover open={menu} onClose={() => setMenu(false)} className="tk-menu">
              {actions.priority && (
                <div className="tk-menu__group" role="group" aria-label="Приоритет">
                  <span className="tk-menu__label">Приоритет</span>
                  {(Object.keys(PRIORITY_TITLE) as api.QuestionPriority[]).map((key) => (
                    <button key={key} type="button" role="menuitemradio" aria-checked={question.priority === key}
                            disabled={busy !== null || question.priority === key}
                            onClick={() => { setMenu(false); void act('priority', () => api.setQuestionPriority(id, key)); }}>
                      {PRIORITY_TITLE[key]}
                      {question.priority === key && <AppIcon name="tick" size={16} />}
                    </button>
                  ))}
                </div>
              )}
              {actions.category && (
                <div className="tk-menu__group" role="group" aria-label="Категория">
                  <span className="tk-menu__label">Категория</span>
                  {(Object.keys(CATEGORY_TITLE) as api.QuestionCategory[]).map((key) => (
                    <button key={key} type="button" role="menuitemradio" aria-checked={question.category === key}
                            disabled={busy !== null || question.category === key}
                            onClick={() => { setMenu(false); void act('category', () => api.setQuestionCategory(id, key)); }}>
                      {CATEGORY_TITLE[key]}
                      {question.category === key && <AppIcon name="tick" size={16} />}
                    </button>
                  ))}
                </div>
              )}
            </AppPopover>
          </span>
        )}
      </header>

      {notice && (
        <p className={notice.tone === 'error' ? 'tk-notice tk-notice--bad' : 'tk-notice'}
           role={notice.tone === 'error' ? 'alert' : 'status'}>
          {notice.text}
        </p>
      )}

      <div className="tk-thread-wrap">
        <div className="tk-thread" ref={thread} onScroll={onScroll}>
          <Thread question={question} pending={waiting} canResend={canResend} onResend={onResend} isNew={isNew} />
          {question.status === 'CLOSED' && (
            <p className="tk-event tk-event--closed">
              Обращение закрыто{question.closed_at ? ` ${fullDay(question.closed_at)} в ${clock(question.closed_at)}` : ''}
              {question.closed_by ? ` · ${question.closed_by.name}` : ''}
              {question.close_reason ? ` · ${question.close_reason}` : ''}
            </p>
          )}
        </div>
        {unseen > 0 && (
          <button type="button" className="tk-jump" onClick={() => toBottom(true)}>
            <AppIcon name="chevron" size={16} />
            К новым сообщениям
          </button>
        )}
      </div>

      {actions.reply ? (
        <Composer
          question={question}
          value={reply}
          onChange={setReply}
          sending={sending}
          // Ответ из другого обращения ещё в пути: второй параллельно не
          // отправляем, но и «Отправляем…» здесь не пишем — не отсюда.
          blocked={busy === 'reply' && !sending}
          onSend={async (body) => {
            // Реплика видна сразу, «Отправляется». Не «отправлено»: успешный
            // ответ сервера значит лишь, что она сохранена и стоит в очереди
            // Telegram. «Отправлено» появится, когда бот её действительно
            // отправит, — сигналом потока событий.
            onSending({
              key: body.client_request_id, questionId: id, text: body.text,
              file: body.file ? body.file.name : null, at: new Date().toISOString(),
            });
            const fresh = await act(
              'reply',
              () => (body.file
                ? api.replyQuestionWithFile(id, { ...body, file: body.file })
                : api.replyQuestion(id, { text: body.text, after: body.after, client_request_id: body.client_request_id })),
            );
            onSent(body.client_request_id);
            return fresh;
          }}
        />
      ) : (
        <div className="tk-compose tk-compose--off">
          <p>
            {question.status === 'CLOSED'
              ? 'Обращение закрыто. Чтобы ответить, откройте его снова.'
              : 'Отвечать на обращения может только сотрудник с правом «Ответы на вопросы».'}
          </p>
        </div>
      )}
    </div>
  );
}

/**
 * Лента сообщений.
 *
 * Перерисовывается только когда пришли другие данные обращения или свои
 * неподтверждённые реплики: набор текста в поле ответа ленту не трогает —
 * на длинной переписке это сотни строк на каждую букву.
 *
 * `isNew` — пришло после открытия (по `id`). Такие строки получают
 * модификатор `--new` и один раз мягко появляются (CSS-анимация при
 * монтировании строки). Строка с тем же `id` не пересоздаётся, и
 * анимация второй раз не запускается — ни при перечитывании по сигналу
 * потока событий, ни при резервном опросе.
 */
const Thread = memo(function Thread({ question, pending = [], canResend = false, onResend, isNew }: {
  question: api.Question;
  pending?: Pending[];
  canResend?: boolean;
  onResend?: ((notificationId: string) => Promise<void>) | undefined;
  /** Пришла после открытия — коротко проявляется. История — нет. */
  isNew?: (message: api.QuestionMessage) => boolean;
}) {
  const fresh = (message: api.QuestionMessage) => (isNew?.(message) ? ' tk-msg--new' : '');
  const out: ReactNode[] = [];
  let day = '';
  for (const message of question.messages) {
    const label = fullDay(message.created_at);
    if (label !== day) {
      day = label;
      out.push(
        <div key={`day-${message.id}`} className={isNew?.(message) ? 'tk-day tk-day--new' : 'tk-day'}>
          <span>{label}</span>
        </div>,
      );
    }
    if (message.kind === 'SYSTEM') {
      // История обращения: кто взял, кому передали, когда закрыли. Мелко
      // и по центру — это работа с обращением, а не реплика в разговоре.
      out.push(
        <p key={message.id} className={`tk-event${isNew?.(message) ? ' tk-event--new' : ''}`}>
          {eventText(message)}
          {message.author.type === 'user' && <> · {message.author.name}</>}
          <time dateTime={message.created_at}> · {clock(message.created_at)}</time>
        </p>,
      );
      continue;
    }
    const file = message.attachment && (
      <a className="tk-file" href={api.questionFileUrl(question.id, message.id)} target="_blank" rel="noreferrer">
        <AppIcon name="attach" size={16} />
        <span className="tk-file__name">{message.attachment.name}</span>
        <span className="tk-file__size">{sizeText(message.attachment.size_bytes)}</span>
      </a>
    );
    const body = message.body && (
      <p className="tk-msg__text">
        {message.body}
        {/* Распорка в конце текста — место под время в последней строке. */}
        <span className="tk-msg__pad" aria-hidden="true" />
      </p>
    );
    if (message.kind === 'HR') {
      // Ответ HR — это «свои» сообщения того, кто сидит в CRM: справа,
      // синим, со статусом доставки. Подпись с именем остаётся: HR в
      // переписке бывает несколько, и видно, кто именно ответил.
      out.push(
        <article key={message.id} className={`tk-msg tk-msg--mine${fresh(message)}`}
                 data-message-id={message.id}>
          <p className="tk-msg__who">
            <b>{message.author.name}</b>
            <span aria-hidden="true">·</span>
            <span>HR-специалист</span>
          </p>
          <div className="tk-msg__bubble">
            {body}
            {file}
            <span className="tk-msg__time">
              <time dateTime={message.created_at}>{clock(message.created_at)}</time>
              {message.delivery && (
                <Delivery delivery={message.delivery} canResend={canResend} onResend={onResend} />
              )}
            </span>
          </div>
        </article>,
      );
      continue;
    }
    // Сообщение сотрудника — собеседник: слева, белое, с его лицом.
    out.push(
      <article key={message.id} className={`tk-msg tk-msg--theirs${fresh(message)}`}
               data-message-id={message.id}>
        <Photo id={question.employee.id} name={message.author.name} has={question.employee.has_photo} className="tk-face tk-face--sm" />
        <div className="tk-msg__bubble">
          {body}
          {file}
          <span className="tk-msg__time">
            <time dateTime={message.created_at}>{clock(message.created_at)}</time>
          </span>
        </div>
      </article>,
    );
  }
  for (const one of pending) {
    // Свой ответ до подтверждения сервером: тот же вид, что у ответа HR,
    // и честное «Отправляется».
    out.push(
      <article key={`pending-${one.key}`} className="tk-msg tk-msg--mine tk-msg--pending tk-msg--new">
        <div className="tk-msg__bubble">
          {one.text && (
            <p className="tk-msg__text">
              {one.text}
              <span className="tk-msg__pad" aria-hidden="true" />
            </p>
          )}
          {one.file && (
            <span className="tk-file">
              <AppIcon name="attach" size={16} />
              <span className="tk-file__name">{one.file}</span>
            </span>
          )}
          <span className="tk-msg__time">
            <time dateTime={one.at}>{clock(one.at)}</time>
            <Delivery delivery={{ status: 'QUEUED', sent_at: null, read_at: null, error: null }} />
          </span>
        </div>
      </article>,
    );
  }
  return <>{out}</>;
});

/**
 * Статус доставки ответа — ровно тот, что прислал сервер.
 *
 * Значок не меняется сам по таймеру или по окончании анимации:
 * «Отправлено» появляется только когда его сообщил сервер (сигнал потока
 * событий или опрос). Смена статуса при открытой переписке показывается
 * мягко (`--changed`); при открытии переписки значки стоят сразу.
 */
function Delivery({ delivery, canResend = false, onResend }: {
  delivery: NonNullable<api.QuestionMessage['delivery']>;
  canResend?: boolean;
  onResend?: ((notificationId: string) => Promise<void>) | undefined;
}) {
  const title = DELIVERY_TITLE[delivery.status];
  const [retrying, setRetrying] = useState(false);
  const [first] = useState(delivery.status);
  const changed = delivery.status !== first ? ' tk-delivery--changed' : '';
  if (delivery.status === 'FAILED') {
    const id = delivery.notification_id;
    const can = Boolean(id && onResend && canResend && delivery.can_retry);
    return (
      <span key={delivery.status} className={`tk-delivery tk-delivery--bad${changed}`} role="status">
        {title}{delivery.error ? `: ${deliveryReason(delivery.error)}` : ''}
        {can && (
          <button type="button" className="tk-delivery__retry" disabled={retrying}
                  onClick={() => {
                    setRetrying(true);
                    void onResend!(id!).finally(() => setRetrying(false));
                  }}>
            {retrying ? 'Отправляем…' : 'Отправить снова'}
          </button>
        )}
      </span>
    );
  }
  if (delivery.status === 'READ' || delivery.status === 'DELIVERED') {
    // Одна галочка и «Отправлено»: Telegram принял сообщение. Двух галочек
    // нет — подтверждения прочтения у бота не бывает.
    return (
      <span key={delivery.status} className={`tk-delivery tk-delivery--sent${changed}`} role="status" title={title}>
        <AppIcon name="tick" size={16} />
        <span className="tk-delivery__text">{title}</span>
      </span>
    );
  }
  return (
    <span key={delivery.status} className={`tk-delivery${changed}`} role="status" title={title}>
      <AppIcon name="clock" size={16} />
      <span className="tk-delivery__text">{title}</span>
    </span>
  );
}

// --- ответ --------------------------------------------------------------------

/** Пять строк поля ответа; выше — прокрутка внутри поля. */
const FIELD_MAX = 132;
const SEND_LABELS = ['Отправить', 'Повторить', 'Отправляем…'] as const;

function Composer({ question, value, onChange, sending, blocked, onSend }: {
  question: api.Question;
  value: string;
  onChange: (value: string) => void;
  sending: boolean;
  /** Идёт отправка из другого обращения: эта кнопка ждёт, но не «отправляет». */
  blocked: boolean;
  onSend: (body: { text: string; after: api.ReplyAfter; client_request_id: string; file: File | null }) => Promise<api.Question | null>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [emoji, setEmoji] = useState(false);
  const field = useRef<HTMLTextAreaElement | null>(null);
  const picker = useRef<HTMLInputElement | null>(null);
  // Ключ повтора живёт, пока не изменилось содержимое: повторное нажатие
  // после сбоя сети с тем же текстом не даёт человеку второго сообщения.
  // Защёлка — от двойного клика раньше, чем React успел отключить кнопку.
  const pending = useRef<{ sign: string; key: string } | null>(null);
  const inFlight = useRef(false);
  /** Последняя отправка не подтверждена: текст и файл остаются, кнопка — «Повторить». */
  const [failed, setFailed] = useState(false);

  // Отправка оборвалась, но ответ всё-таки сохранился — он пришёл в
  // ленте с нашим ключом. Тогда поле очищается, как после успеха, и
  // повторять нечего.
  useEffect(() => {
    const key = pending.current?.key;
    if (!key || inFlight.current) return;
    if (question.messages.some((one) => one.client_request_id === key)) {
      pending.current = null;
      setFailed(false);
      onChange('');
      setFile(null);
    }
  }, [question.messages, onChange]);
  const connected = question.telegram.connected;
  const text = value.trim();
  const ready = (text.length > 0 || file !== null) && connected && !sending && !blocked;

  /*
   * Поле растёт вместе с текстом, но не выше пяти строк.
   *
   * Нужную высоту можно узнать, только сбросив её в `auto`, — и тогда
   * переход по `height` не срабатывает: поле прыгает. Поэтому после
   * замера возвращается прежняя высота, браузер её фиксирует, и только
   * потом ставится новая — её поле и проходит плавно (см. CSS). Всё это
   * до отрисовки кадра, мигания нет.
   */
  useLayoutEffect(() => {
    const box = field.current;
    if (!box) return;
    const was = box.style.height;
    box.style.height = 'auto';
    const full = box.scrollHeight;
    const next = `${Math.min(full, FIELD_MAX)}px`;
    box.style.overflowY = full > FIELD_MAX ? 'auto' : 'hidden';
    if (was && was !== next) {
      box.style.height = was;
      void box.offsetHeight;
    }
    box.style.height = next;
  }, [value]);

  function insert(piece: string) {
    const box = field.current;
    const start = box?.selectionStart ?? value.length;
    const end = box?.selectionEnd ?? value.length;
    onChange(value.slice(0, start) + piece + value.slice(end));
    window.setTimeout(() => {
      if (!box) return;
      box.focus({ preventScroll: true });
      box.selectionStart = box.selectionEnd = start + piece.length;
    }, 0);
  }

  function choose(chosen: File | undefined) {
    setFileError(null);
    if (!chosen) return;
    if (!FILE_TYPES.includes(chosen.type)) {
      setFileError('Можно приложить PDF, PNG или JPEG');
      return;
    }
    if (chosen.size > FILE_MAX_BYTES) {
      setFileError('Файл больше 10 МБ');
      return;
    }
    setFile(chosen);
  }

  async function send() {
    if (!ready || inFlight.current) return;
    inFlight.current = true;
    const sign = `${text}|${file ? `${file.name}:${file.size}` : ''}`;
    const key = pending.current?.sign === sign ? pending.current.key : requestKey();
    pending.current = { sign, key };
    try {
      setFailed(false);
      const fresh = await onSend({ text, after: 'KEEP', client_request_id: key, file });
      if (fresh) {
        pending.current = null;
        onChange('');
        setFile(null);
      } else {
        setFailed(true);
      }
    } finally {
      inFlight.current = false;
    }
  }

  const sendLabel: (typeof SEND_LABELS)[number] = sending ? 'Отправляем…' : failed ? 'Повторить' : 'Отправить';

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter — отправить, Shift+Enter — новая строка. Во время набора через
    // IME Enter подтверждает слово, а не сообщение.
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send();
    }
  }

  return (
    <form className={['tk-compose', sending ? 'tk-compose--sending' : '', failed ? 'tk-compose--failed' : '']
            .filter(Boolean).join(' ')}
          aria-label="Отправка ответа"
          aria-busy={sending} onSubmit={(event) => { event.preventDefault(); void send(); }}>
      {(file || fileError) && (
        <div className="tk-compose__file">
          {file && (
            <span className="tk-attached">
              <AppIcon name="attach" size={16} />
              <span>{file.name}</span>
              <small>{sizeText(file.size)}</small>
              <button type="button" aria-label="Убрать файл" onClick={() => setFile(null)}>
                <AppIcon name="close" size={16} />
              </button>
            </span>
          )}
          {fileError && <span className="tk-compose__error" role="alert">{fileError}</span>}
        </div>
      )}
      <div className="tk-compose__row">
        <button type="button" className="tk-attach" aria-label="Прикрепить файл"
                disabled={!connected || sending} onClick={() => picker.current?.click()}>
          <AppIcon name="attach" size={20} />
        </button>
        <input ref={picker} type="file" hidden accept="application/pdf,image/png,image/jpeg,.pdf,.png,.jpg,.jpeg"
               data-testid="reply-file"
               onChange={(event) => { choose(event.target.files?.[0]); event.target.value = ''; }} />
        <span className="tk-compose__box">
          <textarea
            ref={field}
            aria-label="Ответ сотруднику"
            className="tk-compose__field"
            rows={1}
            maxLength={4000}
            value={value}
            placeholder={connected ? 'Введите сообщение…' : `${telegramReason(question.telegram.reason)} — отправка недоступна`}
            readOnly={sending}
            onChange={(event) => { setFailed(false); onChange(event.target.value); }}
            onKeyDown={onKey}
          />
          <button type="button" className="tk-emoji" aria-label="Эмодзи" aria-expanded={emoji}
                  disabled={!connected || sending} onClick={() => setEmoji((was) => !was)}>
            <AppIcon name="smile" size={20} />
          </button>
          <AppPopover open={emoji} onClose={() => setEmoji(false)} className="tk-menu tk-emoji-menu">
            {EMOJI.map((one) => (
              <button key={one} type="button" aria-label={`Вставить ${one}`} onClick={() => { insert(one); setEmoji(false); }}>
                {one}
              </button>
            ))}
          </AppPopover>
        </span>
        {/* Все надписи лежат в одной клетке, видна одна: кнопка не меняет
            ширину, и поле ввода рядом не дёргается. */}
        <button type="submit" className={sending ? 'tk-send tk-send--busy' : 'tk-send'}
                disabled={!ready} aria-busy={sending}>
          {SEND_LABELS.map((one) => (
            <span key={one} className="tk-send__label" aria-hidden={one !== sendLabel}>{one}</span>
          ))}
        </button>
      </div>
      {failed && !sending && (
        <p className="tk-compose__failed" role="status">
          Ответ не подтверждён сервером — {file ? 'текст и файл сохранены' : 'текст сохранён'}. Повтор не создаст второго сообщения.
        </p>
      )}
    </form>
  );
}

// --- контекст и управление ------------------------------------------------------

function Side({ question, block, context, assignees, busy, act, onClose, onInsert, onRetry }: {
  question: api.Question;
  block: Block<api.QuestionContext>;
  context: api.QuestionContext | null;
  assignees: api.Person[];
  busy: string | null;
  act: Act;
  onClose: () => void;
  onInsert: (text: string) => void;
  onRetry: () => void;
}) {
  const actions = question.actions;
  const closed = question.status === 'CLOSED';
  const id = question.id;

  const people = question.assignee && !assignees.some((one) => one.id === question.assignee?.id)
    ? [question.assignee, ...assignees]
    : assignees;

  const statusOptions: ChoiceOption[] = [
    // «Новое» — только текущее значение: вернуть обращение в новые значит
    // сделать вид, что его никто не видел.
    { value: 'NEW', label: STATUS_TITLE.NEW, disabled: true },
    { value: 'IN_PROGRESS', label: STATUS_TITLE.IN_PROGRESS, disabled: !actions.start && question.status !== 'IN_PROGRESS' },
    { value: 'WAITING_EMPLOYEE', label: STATUS_TITLE.WAITING_EMPLOYEE, disabled: !actions.wait && question.status !== 'WAITING_EMPLOYEE' },
    { value: 'CLOSED', label: STATUS_TITLE.CLOSED, disabled: !actions.close && !closed },
  ];

  function setStatus(next: string) {
    if (next === question.status) return;
    if (next === 'IN_PROGRESS') void act('start', () => api.startQuestion(id), 'Обращение в работе');
    else if (next === 'WAITING_EMPLOYEE') void act('wait', () => api.waitForEmployee(id), 'Ждём ответа сотрудника');
    else if (next === 'CLOSED') onClose();
  }

  // Статьи — те, на которые опирается ответ по базе знаний: сперва
  // источники готового черновика, потом материалы обращения. Снятые с
  // публикации не показываются — по ним уже не отвечают.
  const draft = question.draft;
  const ready = draft && draft.status === 'READY' && draft.text && !draft.outdated ? draft.text : null;
  const articles: api.KnowledgeRef[] = [];
  for (const one of [...(draft?.sources ?? []), ...(context?.materials ?? [])]) {
    if (one.status === 'ACTIVE' && !articles.some((seen) => seen.id === one.id)) articles.push(one);
  }

  return (
    <div className="tk-side__body">
      <section className="tk-part" aria-label="Данные сотрудника">
        <h2 className="tk-part__title">О сотруднике</h2>
        {block.state === 'error' && (
          <div className="tk-empty">
            <p>Не удалось загрузить данные сотрудника.</p>
            <button type="button" className="tk-link" onClick={onRetry}>Повторить</button>
          </div>
        )}
        {block.state === 'denied' && <p className="tk-muted">Нет доступа к данным сотрудника.</p>}
        {!context && block.state !== 'error' && block.state !== 'denied' && (
          <div className="tk-ghost-block" aria-label="Загрузка контекста">
            <span className="tk-ghost tk-ghost--round" />
            <span className="tk-ghost" />
          </div>
        )}
        {context && (
          <>
            <div className="tk-person">
              <Photo id={context.employee.id} name={context.employee.full_name} has={context.employee.has_photo} className="tk-face tk-face--md" />
              <div>
                {context.links.employee_card
                  ? <Link className="tk-person__name" to={`/employees/${context.employee.id}`}>{context.employee.full_name}</Link>
                  : <p className="tk-person__name">{context.employee.full_name}</p>}
                <p className="tk-person__role">{context.employee.position ?? 'Должность не указана'}</p>
              </div>
            </div>
            <ul className="tk-facts">
              <Fact icon="building" label="Офис">{context.employee.office?.name ?? 'Офис не указан'}</Fact>
              <Fact icon="users" label="Отдел">{context.employee.department ?? 'Отдел не указан'}</Fact>
              {context.today !== undefined && context.today !== null && (
                <Fact icon="bag" label="Сегодня на работе"><TodayState today={context.today} /></Fact>
              )}
              <Fact icon="doc" label="Открытых обращений">
                Открытых обращений <span className="tk-count">{context.history.open}</span>
              </Fact>
            </ul>
          </>
        )}
      </section>

      <section className="tk-part" aria-label="Управление обращением">
        <h2 className="tk-part__title">Управление обращением</h2>
        <div className="tk-control">
          <span className="tk-control__label">Ответственный</span>
          <Choice
            label="Ответственный"
            shown={question.assignee?.name ?? 'Не назначен'}
            lead={question.assignee ? <span className="tk-face tk-face--xs tk-face--none" aria-hidden="true">{initials(question.assignee.name)}</span> : null}
            value={question.assignee?.id ?? ''}
            disabled={!actions.assign || busy !== null}
            options={people.map((one) => ({ value: one.id, label: one.name }))}
            onPick={(value) => void act('assign', () => api.assignQuestion(id, value), 'Ответственный назначен')}
          />
        </div>
        <div className="tk-control">
          <span className="tk-control__label">Статус</span>
          <Choice
            label="Статус"
            shown={STATUS_TITLE[question.status]}
            lead={<span className={`tk-state-dot tk-state-dot--${question.status}`} aria-hidden="true" />}
            value={question.status}
            disabled={closed || busy !== null}
            options={statusOptions}
            onPick={setStatus}
          />
        </div>
        {closed ? (
          <div className="tk-closed">
            <span className="tk-closed__state"><AppIcon name="check" size={18} />Закрыто</span>
            {actions.reopen && (
              <button type="button" className="tk-action" disabled={busy !== null}
                      onClick={() => void act('reopen', () => api.reopenQuestion(id), 'Обращение открыто снова')}>
                <AppIcon name="refresh" size={18} />Открыть снова
              </button>
            )}
          </div>
        ) : actions.close && (
          <button type="button" className="tk-action" disabled={busy !== null} onClick={onClose}>
            <AppIcon name="lock" size={18} />Закрыть обращение
          </button>
        )}
      </section>

      {articles.length > 0 && (
        <section className="tk-part tk-part--last" aria-label="Похожие статьи из базы знаний">
          <h2 className="tk-part__title">Похожие статьи из базы знаний</h2>
          <ul className="tk-articles">
            {articles.slice(0, 3).map((one) => (
              <li key={one.id}>
                <AppIcon name="doc" size={18} />
                <span>
                  <b>{one.title}</b>
                  <small>
                    {SOURCE_KIND[one.source_type] ?? 'Материал'}
                    {one.published_at ? ` · от ${dayMonth(one.published_at)}` : ''}
                  </small>
                </span>
              </li>
            ))}
          </ul>
          {/* Готовый ответ по этим статьям — только по нажатию: сам он
              сотруднику не уходит, кадровик читает его в поле и правит. */}
          {ready && actions.reply && (
            <button type="button" className="tk-link tk-articles__use" onClick={() => onInsert(ready)}>
              Вставить готовый ответ
            </button>
          )}
        </section>
      )}
    </div>
  );
}

function Fact({ icon, label, children }: { icon: AppIconName; label: string; children: ReactNode }) {
  return (
    <li className="tk-fact" aria-label={label}>
      <AppIcon name={icon} size={18} />
      <span>{children}</span>
    </li>
  );
}

function TodayState({ today }: { today: NonNullable<api.QuestionContext['today']> }) {
  if (!today.state) return <span className="tk-today tk-today--off">Нет отметки</span>;
  const [title, tone] = TODAY_TITLE[today.state] ?? ['Нет отметки', 'off'];
  // Время прихода — подсказкой: строка остаётся короткой, как в макете.
  const since = today.state === 'IN_OFFICE' && today.first_entry_at ? `Пришёл в ${clock(today.first_entry_at)}` : undefined;
  return <span className={`tk-today tk-today--${tone}`} {...(since ? { title: since } : {})}>{title}</span>;
}

type ChoiceOption = { value: string; label: string; disabled?: boolean };

/**
 * Выбор с настоящими вариантами. Пустого «не выбрано» среди них нет:
 * снять ответственного сервер не умеет, и пункт, который ничего не
 * делает, был бы обманом.
 */
function Choice({ label, shown, lead, value, options, disabled, onPick }: {
  label: string;
  shown: string;
  lead?: ReactNode;
  value: string;
  options: ChoiceOption[];
  disabled?: boolean;
  onPick: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <span className="tk-choice">
      <button type="button" className="tk-choice__button" aria-label={`${label}: ${shown}`}
              aria-haspopup="listbox" aria-expanded={open} disabled={disabled}
              onClick={() => setOpen((was) => !was)}>
        {lead}
        <span className="tk-choice__text">{shown}</span>
        <AppIcon name="chevron" size={16} />
      </button>
      <AppPopover open={open} onClose={() => setOpen(false)} className="tk-menu">
        <div role="listbox" aria-label={label}>
          {options.map((one) => (
            <button key={one.value} type="button" role="option" aria-selected={one.value === value}
                    disabled={one.disabled}
                    onClick={() => { setOpen(false); if (one.value !== value) onPick(one.value); }}>
              {one.label}
              {one.value === value && <AppIcon name="tick" size={16} />}
            </button>
          ))}
        </div>
      </AppPopover>
    </span>
  );
}

function CloseDialog({ busy, onCancel, onClose }: {
  busy: boolean;
  onCancel: () => void;
  onClose: (reason: string) => Promise<void>;
}) {
  const [choice, setChoice] = useState(CLOSE_REASONS[0] ?? '');
  const [note, setNote] = useState('');
  const reason = choice === 'Другое' ? note.trim() : [choice, note.trim()].filter(Boolean).join(': ');
  const card = useRef<HTMLFormElement | null>(null);
  const [leaving, setLeaving] = useState(false);
  const left = useRef(false);
  // Кнопка, с которой открыли окно: фокус вернётся к ней. Запоминается
  // при отрисовке — до того, как фокус уйдёт в окно.
  const [opener] = useState(() => document.activeElement as HTMLElement | null);

  /**
   * Отмена с короткой обратной анимацией. Повторная отмена ничего не
   * делает; снятие окна подстраховано таймером — затемнение не
   * останется. Пока сервер закрывает обращение, отменить нельзя.
   */
  const cancel = useCallback(() => {
    if (left.current || busy) return;
    left.current = true;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      onCancel();
      return;
    }
    setLeaving(true);
    window.setTimeout(onCancel, 160);
  }, [busy, onCancel]);

  useEffect(() => {
    card.current?.focus();
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') cancel();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [cancel]);
  useEffect(() => () => {
    if (opener && opener.isConnected) opener.focus();
  }, [opener]);

  return (
    <div className={leaving ? 'tk-modal tk-modal--leaving' : 'tk-modal'} role="presentation"
         onMouseDown={(event) => { if (event.target === event.currentTarget) cancel(); }}>
      <form ref={card} tabIndex={-1} className="tk-modal__card" role="dialog" aria-modal="true" aria-label="Закрытие обращения"
            onSubmit={(event) => {
              event.preventDefault();
              if (reason && !busy) void onClose(reason);
            }}>
        <h2>Закрыть обращение</h2>
        <p className="tk-muted">Переписка останется в истории. Сотрудник сможет написать снова.</p>
        <fieldset>
          <legend>Причина</legend>
          {CLOSE_REASONS.map((one) => (
            <label key={one} className="tk-radio">
              <input type="radio" name="close-reason" checked={choice === one} onChange={() => setChoice(one)} />
              {one}
            </label>
          ))}
        </fieldset>
        <input className="tk-input" aria-label="Комментарий к закрытию"
               placeholder={choice === 'Другое' ? 'Опишите причину' : 'Комментарий (необязательно)'}
               value={note} maxLength={900} onChange={(event) => setNote(event.target.value)} />
        <div className="tk-modal__buttons">
          <button type="button" className="tk-btn" disabled={busy} onClick={cancel}>Отмена</button>
          <button type="submit" className="tk-btn tk-btn--main" disabled={!reason || busy} aria-busy={busy}>
            {busy ? 'Закрываем…' : 'Закрыть обращение'}
          </button>
        </div>
      </form>
    </div>
  );
}

// --- общее ---------------------------------------------------------------------

function Photo({ id, name, has, className }: { id: string; name: string; has: boolean; className: string }) {
  const [broken, setBroken] = useState(false);
  if (!has || broken) {
    return <span className={`${className} tk-face--none`} aria-hidden="true">{initials(name)}</span>;
  }
  return (
    <img className={className} src={api.employeePhotoUrl(id)} alt=""
         onError={() => setBroken(true)}
         onLoad={(event) => { if (event.currentTarget.naturalWidth < 32) setBroken(true); }} />
  );
}

function failureText(error: unknown, label?: string): string {
  if (error instanceof ApiFailure) {
    if (label === 'reply' && (error.kind === 'offline' || error.kind === 'server')) {
      return 'Связь прервалась — неизвестно, дошёл ли ответ. Переписка перечитывается: если ответа в ней нет, отправьте снова — второго сообщения не будет.';
    }
    if (error.code === 'telegram_not_connected') {
      return 'Telegram сотрудника не подключён — сообщение не отправлено и в ленту не добавлено.';
    }
    if (error.kind === 'conflict') return 'Действие недоступно: обращение уже изменилось. Показано актуальное состояние.';
    if (error.kind === 'validation') return 'Сервер не принял данные. Проверьте текст и файл.';
  }
  return messageFor(error);
}

function requestKey(): string {
  const random = globalThis.crypto?.randomUUID?.();
  return random ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function telegramReason(reason: string | null): string {
  const text = (reason && TELEGRAM_REASON[reason]) || 'Telegram не подключён';
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function eventText(message: api.QuestionMessage): string {
  const details = message.details ?? {};
  const name = (key: string) => {
    const value = details[key];
    return value && typeof value === 'object' && 'name' in value && typeof value.name === 'string' ? value.name : null;
  };
  const priority = (key: string) => {
    const value = details[key];
    return typeof value === 'string' && value in PRIORITY_TITLE ? PRIORITY_TITLE[value as api.QuestionPriority] : '—';
  };
  const category = (key: string) => {
    const value = details[key];
    return typeof value === 'string' && value in CATEGORY_TITLE ? CATEGORY_TITLE[value as api.QuestionCategory] : '—';
  };
  switch (message.event) {
    case 'CREATED': return 'Обращение создано';
    case 'TAKEN': return `Взято в работу: ${name('to') ?? message.author.name}`;
    case 'ASSIGNED': return `Назначен ответственный: ${name('to') ?? '—'}`;
    case 'TRANSFERRED': return `Передано: ${name('from') ?? '—'} → ${name('to') ?? '—'}`;
    case 'PRIORITY': return `Приоритет: ${priority('from')} → ${priority('to')}`;
    case 'CATEGORY': return `Категория: ${category('from')} → ${category('to')}`;
    case 'STARTED': return 'Статус: в работе';
    case 'WAITING_EMPLOYEE': return 'Статус: ждём ответа сотрудника';
    case 'RESUMED': return 'Сотрудник ответил — обращение снова в работе';
    case 'CLOSED': return typeof details['reason'] === 'string' ? `Закрыто: ${details['reason']}` : 'Закрыто';
    case 'REOPENED':
      return details['by'] === 'employee'
        ? 'Сотрудник написал в закрытое обращение — оно открыто снова'
        : 'Обращение открыто снова';
    default: return 'Событие обращения';
  }
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

/** «21 сентября 2026» — разделитель дня в переписке. */
function fullDay(iso: string): string {
  const date = new Date(iso);
  return `${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()} г.`;
}

/** Время в строке очереди: «10:24», «Вчера», «12 апр.». */
function listMoment(iso: string): string {
  const date = new Date(iso);
  const now = new Date();
  const day = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  if (day(date) === day(now)) return clock(iso);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (day(date) === day(yesterday)) return 'Вчера';
  return `${date.getDate()} ${MONTHS_SHORT[date.getMonth()]}.`;
}

function dayMonth(iso: string): string {
  const date = new Date(iso);
  return `${date.getDate()} ${MONTHS[date.getMonth()]}`;
}

function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1).replace('.', ',')} МБ`;
}
