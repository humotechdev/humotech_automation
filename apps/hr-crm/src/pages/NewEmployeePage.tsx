/**
 * Приём и правка сотрудника: одна форма на оба случая.
 *
 * `/employees/new` — приём, `/employees/:id/edit` — правка. Форма одна:
 * поля те же, порядок тот же, и кадровик не переучивается ради смены
 * одной фамилии. Отличий три: в правке данные подставлены, внизу
 * «Сохранить изменения», а в окне подтверждения видно, что на что
 * меняется.
 *
 * ПРАВКА РАСПАДАЕТСЯ НА ТРИ ОПЕРАЦИИ, и это не прихоть интерфейса.
 * Личные данные — это правка карточки. Офис, отдел, должность и
 * руководитель — перевод: он создаёт НОВЫЙ период назначения с даты,
 * а прежний закрывает, иначе отметки за прошлый месяц оказались бы
 * отнесены к офису, в котором человек тогда не работал. График —
 * третья, у него свой период. Отсюда «Дата перевода» на втором шаге:
 * она появляется только тогда, когда есть что переводить.
 *
 * Общей транзакции у этих трёх нет. Поэтому они идут по очереди, а при
 * отказе видно, что уже записалось: «личные данные сохранены, перевод
 * не прошёл» полезнее, чем «не удалось сохранить».
 *
 * Приём сотрудника: три шага — личные данные, работа и график, документы.
 *
 * Шаги, а не одна простыня. Полей четырнадцать плюс десяток бумаг, и на
 * одном экране кадровик терял, что уже заполнил, а что нет. Разделены
 * они по зависимостям, а не по красоте: на первом шаге ничего не зависит
 * от справочников, на втором должность зависит от офиса, а график — от
 * даты выхода, на третьем остаются файлы, которые ничего не меняют.
 *
 * Назад можно на любой шаг: введённое не пропадает, форма живёт целиком
 * и отправляется одним запросом в конце.
 *
 * Проверка — по шагам. Незаполненное поле первого шага не даёт уйти на
 * второй: узнавать про пустую фамилию на третьем шаге поздно.
 *
 * Ни один документ не обязателен. Часть бумаг приносят на бумаге, часть
 * человек донесёт позже, и приём не должен вставать из-за отсутствия
 * скана: сотрудника заводят в день выхода.
 *
 * Отправляется форма только после подтверждения. Причина не в
 * осторожности ради осторожности: приём создаёт человека, назначение,
 * график и доступ к боту, и отменить это одной кнопкой нельзя — карточку
 * потом не удаляют, её увольняют.
 *
 * Повторное нажатие безопасно: ключ идемпотентности придумывается один
 * раз на открытие формы, и второй такой же запрос вернёт того же
 * сотрудника, а не заведёт второго.
 */

import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { AppIcon } from '../components/AppIcon';
import { AppShell } from '../components/AppShell';
import { DatePicker } from '../components/DatePicker';
import { Dropdown } from '../components/AppSelect';
import { longDate, today, useBlock } from '../features/dashboard/data';
import '../styles/new-employee.css';

/** Поля формы. Всё строками: ровно то, что человек видит в полях. */
type Draft = {
  last_name: string;
  first_name: string;
  middle_name: string;
  birth_date: string;
  pinfl: string;
  phone: string;
  corporate_email: string;
  telegram_username: string;
  hire_date: string;
  region_id: string;
  office_id: string;
  department_id: string;
  position_id: string;
  manager_employee_id: string;
  employment_type: string;
  probation: string;
  probation_from: string;
  probation_to: string;
  schedule_id: string;
  gender: string;
  marital_status: string;
};

const EMPTY: Draft = {
  last_name: '', first_name: '', middle_name: '', birth_date: '',
  pinfl: '', phone: '', corporate_email: '', telegram_username: '',
  hire_date: '', region_id: '', office_id: '', department_id: '',
  position_id: '', manager_employee_id: '', employment_type: 'FULL_TIME',
  probation: '', probation_from: '', probation_to: '',
  schedule_id: '', gender: '', marital_status: '',
};

const STEPS = [
  { key: 'person', title: 'Личные данные' },
  { key: 'work', title: 'Работа и график' },
  { key: 'papers', title: 'Документы' },
] as const;

/** Подписи полей для окна «что на что меняется». */
const FIELD_TITLE: Partial<Record<keyof Draft, string>> = {
  last_name: 'Фамилия',
  first_name: 'Имя',
  middle_name: 'Отчество',
  birth_date: 'Дата рождения',
  phone: 'Телефон',
  corporate_email: 'Email',
  gender: 'Пол',
  marital_status: 'Семейное положение',
  hire_date: 'Дата начала работы',
  region_id: 'Регион',
  office_id: 'Офис',
  department_id: 'Отдел',
  position_id: 'Должность',
  manager_employee_id: 'Руководитель',
  employment_type: 'Тип занятости',
  probation: 'Выходит на',
  probation_from: 'Стажировка с',
  probation_to: 'Стажировка по',
  schedule_id: 'График работы',
};

/** Срок стажировки: правится карточкой, а не переводом. */
const PROBATION_DATES: (keyof Draft)[] = ['probation_from', 'probation_to'];

/** Что меняется переводом: новый период назначения с даты. */
const ASSIGNMENT: (keyof Draft)[] = [
  'office_id', 'department_id', 'position_id', 'manager_employee_id',
  'employment_type',
];

/** Поля, без которых шаг не закрыть. Те же, что помечены звёздочкой. */
const NEEDED: Record<number, (keyof Draft)[]> = {
  0: ['last_name', 'first_name', 'birth_date', 'pinfl', 'phone'],
  1: ['hire_date', 'region_id', 'office_id', 'department_id', 'position_id',
      'employment_type', 'schedule_id'],
  2: [],
};

/*
 * Пол и семейное положение — анкетные поля, и оба необязательны. У
 * сотрудников, заведённых до их появления, значения нет, и требовать
 * его задним числом значило бы не дать открыть их карточку. Здесь та
 * же логика: приём состоится и без них.
 */
const GENDERS = [
  { id: 'MALE', name: 'Мужской' },
  { id: 'FEMALE', name: 'Женский' },
];

const MARITAL_STATUSES = [
  { id: 'SINGLE', name: 'Не женат / не замужем' },
  { id: 'MARRIED', name: 'Женат / замужем' },
  { id: 'DIVORCED', name: 'Разведён(а)' },
  { id: 'WIDOWED', name: 'Вдовец / вдова' },
];

/*
 * Занятость — про то, сколько человек работает, а не кем он числится.
 *
 * «Стажировки» здесь нет намеренно: она отвечает на другой вопрос — в
 * штат человека берут или на испытательный срок, — и для него есть
 * поле «Выходит на». Два места для одной мысли однажды разойдутся:
 * стажёр на полный день оказался бы либо «полным днём», либо
 * «стажировкой», и оба ответа были бы половинчатыми.
 */
const EMPLOYMENT_TYPES = [
  { id: 'FULL_TIME', name: 'Полный рабочий день' },
  { id: 'PART_TIME', name: 'Неполный день' },
  { id: 'CONTRACT', name: 'Договор подряда' },
];

/**
 * Кем человек выходит: на стажировку или сразу в штат.
 *
 * Названия те же, что и в карточке: увидеть «Испытательный срок» при
 * приёме и «Стажировка» на следующем экране — значит решить, что это
 * два разных состояния.
 */
const PROBATION = [
  { id: 'PROBATION', name: 'Стажировка' },
];

/*
 * Пределы те же, что у сервера (`EmployeeAttachmentService`). Здесь они
 * повторены не ради второй проверки, а ради быстрого ответа: про файл
 * на двадцать мегабайт незачем узнавать после его отправки.
 */
const PHOTO_TYPES = ['image/jpeg', 'image/png'];
const PHOTO_MAX = 5 * 1024 * 1024;
const DOCUMENT_TYPES = ['application/pdf', 'image/jpeg', 'image/png'];
const DOCUMENT_MAX = 10 * 1024 * 1024;

/**
 * Бумага из списка на приём.
 *
 * `kind` — то, чем документ является для сервера. Видов там четыре:
 * паспорт, договор, приказ и «другой». Остальное уходит как «другой» с
 * названием — заводить в базе десяток новых видов ради подписи в
 * списке значило бы менять схему из-за слова.
 */
type Need = {
  key: string;
  kind: api.EmployeeDocumentKind;
  title: string;
  note: string;
};

/**
 * Что просят принести при приёме — и что из этого можно приложить файлом.
 *
 * Список кадровый, не выдуманный. Здесь только то, что существует в
 * электронном виде или сканируется: печатные фото 3×4 и оригинал
 * медсправки живут в личном деле на бумаге и в список загрузки не
 * входят — им место ниже, отдельной памяткой.
 */
const PAPERS: Need[] = [
  {
    key: 'identity', kind: 'IDENTITY',
    title: 'Паспорт или ID-карта',
    note: 'Разворот с фотографией и страница с пропиской',
  },
  {
    key: 'labour', kind: 'OTHER',
    title: 'Трудовая книжка или выписка из электронной',
    note: 'Выписку заверяют по последнему месту работы. Тем, кто устраивается впервые, не нужна',
  },
  {
    key: 'mainjob', kind: 'OTHER',
    title: 'Справка с основного места работы',
    note: 'Только для совместителей — вместо трудовой книжки',
  },
  {
    key: 'military', kind: 'OTHER',
    title: 'Военный билет или приписное удостоверение',
    note: 'Для военнообязанных и призывников',
  },
  {
    key: 'diploma', kind: 'OTHER',
    title: 'Диплом об образовании',
    note: 'Высшее, среднее специальное или профессиональное',
  },
  {
    key: 'inn', kind: 'OTHER',
    title: 'ИНН',
    note: 'Свидетельство о присвоении идентификационного номера',
  },
  {
    key: 'pension', kind: 'OTHER',
    title: 'Накопительная пенсионная книжка',
    note: 'Кроме тех, кто устраивается на работу впервые',
  },
  {
    key: 'marriage', kind: 'OTHER',
    title: 'Свидетельство о заключении брака',
    note: 'Если есть',
  },
  {
    key: 'child', kind: 'OTHER',
    title: 'Свидетельство о рождении ребёнка',
    note: 'По одному на каждого ребёнка — остальные добавьте строкой ниже',
  },
  {
    key: 'medical', kind: 'OTHER',
    title: 'Медицинская справка Ф-86',
    note: 'Скан принимаем, оригинал приносят на бумаге',
  },
];

/** Что файлом не приложишь. Показано, чтобы кадровик не искал строку. */
const ON_PAPER = [
  {
    title: 'Фотографии 3×4 — 4 штуки',
    note: 'Печатные, для личного дела. Фото в карточку загружается на первом шаге',
  },
  {
    title: 'ПИНФЛ',
    note: 'Введён числом на шаге «Личные данные» — отдельный файл не нужен',
  },
];

/** Кто собирает бумаги. Телефон здесь же: искать его негде. */
const HR_CONTACT = {
  name: 'Абдуваситова Феруза',
  phone: '+998 (50) 017-66-18',
  tel: '+998500176618',
};

/** Один приложенный файл в форме: до приёма он ещё ни к кому не привязан. */
type Paper = {
  /** Ключ строки: вид может повторяться у «другого документа». */
  key: string;
  kind: api.EmployeeDocumentKind;
  /** Название документа: уходит на сервер вместе с файлом. */
  title: string;
  /** Что показано человеку. `null` — файл ещё не выбран. */
  name: string | null;
  size: number;
  /** Знак файла на сервере. Пока `null`, приём его не возьмёт. */
  id: string | null;
  /** Строка документа в карточке — только в правке. */
  docId?: string | null;
  busy: boolean;
  error: string | null;
};

export function NewEmployeePage() {
  const navigate = useNavigate();
  const now = today();
  /** Есть идентификатор — правим, нет — принимаем. */
  const { id = '' } = useParams();
  const editing = Boolean(id);
  const steps = STEPS;

  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  /** Каким человек был до правки: из этого считается «что на что». */
  const [initial, setInitial] = useState<Draft>(EMPTY);
  /** Дата перевода: с неё действует новое назначение и новый график. */
  const [since, setSince] = useState(now);
  const [errors, setErrors] = useState<Partial<Record<keyof Draft, string>>>({});
  const [common, setCommon] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [sending, setSending] = useState(false);
  // Ссылка одноразовая, поэтому показываем именно ту, которую вернул
  // сервер при создании сотрудника. HR передаёт её сотруднику любым
  // удобным каналом; дальше бот и система обходятся без HR.
  const [telegramInvite, setTelegramInvite] = useState<{
    employeeId: string;
    link: string;
  } | null>(null);

  const [photo, setPhoto] = useState<Paper | null>(null);
  const [papers, setPapers] = useState<Record<string, Paper>>({});
  /** Строки сверх списка: второй ребёнок, справка из ГАИ, что угодно. */
  const [extra, setExtra] = useState<Paper[]>([]);
  // Предпросмотр живёт отдельно от `photo`: адрес выдаёт браузер, и его
  // надо освободить, иначе каждая замена оставляет картинку в памяти.
  const [preview, setPreview] = useState<string | null>(null);
  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  /*
   * Ключ одного нажатия. Живёт от открытия формы до успешного приёма:
   * повтор из-за двойного нажатия или потерянного ответа приходит с тем
   * же ключом и возвращает того же человека.
   */
  const key = useRef<string>(makeKey());

  const [card] = useBlock(
    (signal) => api.employee(id, signal),
    `edit-employee|${id}`,
    editing,
  );

  /*
   * Данные человека в поля — один раз, при загрузке карточки.
   *
   * `initial` хранит тот же снимок: без него «что изменилось» пришлось
   * бы спрашивать у сервера повторно, а показать «было → стало» в
   * подтверждении было бы не из чего.
   */
  useEffect(() => {
    if (card.state !== 'ready') return;
    const seeded = draftOf(card.data);
    setDraft(seeded);
    setInitial(seeded);
    /*
     * Дата перевода — не раньше дня после начала действующего
     * назначения: сервер иначе откажет, и человек получил бы отказ на
     * ровном месте. У назначения, начатого сегодня, перевод возможен
     * только с завтрашнего дня — таково правило периодов.
     */
    const at = (card.data['current_assignment'] ?? {}) as Record<string, unknown>;
    const from = typeof at['valid_from'] === 'string' ? at['valid_from'] : '';
    setSince(from && from >= now ? nextDay(from) : now);

    // Уже приложенные бумаги — в те же строки списка. Строка чек-листа
    // без файла остаётся пустой: «паспорта нет» — это факт, а не ошибка.
    const rows = Array.isArray(card.data['documents'])
      ? (card.data['documents'] as Record<string, unknown>[]) : [];
    const known: Record<string, Paper> = {};
    const rest: Paper[] = [];
    for (const row of rows) {
      const file = row['file_id'];
      if (!file) continue;
      const title = String(row['title'] ?? '');
      const kind = String(row['kind'] ?? 'OTHER') as api.EmployeeDocumentKind;
      const need = PAPERS.find((one) =>
        one.kind === 'OTHER' ? one.title === title : one.kind === kind);
      const paper: Paper = {
        key: need?.key ?? String(row['id'] ?? makeKey()),
        kind, title: need?.title ?? title, name: title, size: 0,
        id: String(file), busy: false, error: null,
        docId: String(row['id'] ?? ''),
      };
      if (need) known[need.key] = paper;
      else rest.push(paper);
    }
    setPapers(known);
    setExtra(rest);
  }, [card, now]);

  const [regions] = useBlock((signal) => api.regions(signal), 'regions');
  const [offices] = useBlock((signal) => api.offices(signal), 'offices');
  const [positions] = useBlock((signal) => api.positions(signal), 'positions');
  const [schedules] = useBlock((signal) => api.workSchedules(signal), 'schedules');

  // Отделы зависят от офиса: состав отделов у офисов разный, и общий
  // список предложил бы отдел, которого в этом офисе нет.
  const [departments] = useBlock(
    (signal) => api.officeDepartments(draft.office_id, signal),
    `departments:${draft.office_id}`,
    Boolean(draft.office_id),
  );
  // Руководитель выбирается из тех, кто работает в этом же офисе.
  const [managers] = useBlock(
    (signal) => api.employees({ office_id: draft.office_id, status: 'ACTIVE', limit: '200' }, signal),
    `managers:${draft.office_id}`,
    Boolean(draft.office_id),
  );

  const officeOptions = useMemo(() => {
    if (offices.state !== 'ready') return [];
    const all = offices.data.items.filter((item) => item.status !== 'INACTIVE');
    return draft.region_id
      ? all.filter((item) => item.region_id === draft.region_id)
      : all;
  }, [offices, draft.region_id]);

  // Смена региона снимает выбранный офис, если он из другого региона:
  // иначе в форме осталась бы пара «регион A, офис из B», которую сервер
  // отвергнет только в момент отправки.
  useEffect(() => {
    if (!draft.office_id) return;
    if (officeOptions.some((item) => item.id === draft.office_id)) return;
    setDraft((was) => ({ ...was, office_id: '', department_id: '', manager_employee_id: '' }));
  }, [officeOptions, draft.office_id]);

  /** Изменённые поля: подпись, было, стало. Пусто — менять нечего. */
  const changes = useMemo(() => {
    if (!editing) return [];
    const rows: { field: keyof Draft; title: string; was: string; now: string }[] = [];
    for (const field of Object.keys(FIELD_TITLE) as (keyof Draft)[]) {
      if (draft[field] === initial[field]) continue;
      rows.push({
        field,
        title: FIELD_TITLE[field] ?? field,
        was: initial[field],
        now: draft[field],
      });
    }
    return rows;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing, draft, initial]);

  /** Меняется ли назначение: от этого зависит «Дата перевода». */
  const moved = editing && ASSIGNMENT.some((field) => draft[field] !== initial[field]);

  const set = (field: keyof Draft, value: string) => {
    setDraft((was) => ({ ...was, [field]: value }));
    setErrors((was) => {
      if (!(field in was)) return was;
      const next = { ...was };
      delete next[field];
      return next;
    });
    setCommon(null);
  };

  /**
   * Отдать файл серверу и вернуть строку формы.
   *
   * Загрузка начинается сразу после выбора: про слишком большой файл
   * незачем узнавать в момент приёма, вместе с отказом.
   */
  async function accept(
    file: File,
    purpose: 'photo' | 'document',
    show: (next: (was: Paper) => Paper) => void,
  ): Promise<string | null> {
    const types = purpose === 'photo' ? PHOTO_TYPES : DOCUMENT_TYPES;
    const limit = purpose === 'photo' ? PHOTO_MAX : DOCUMENT_MAX;
    if (!types.includes(file.type)) {
      show((was) => ({ ...was, busy: false, error: wrongType(purpose) }));
      return null;
    }
    if (file.size > limit) {
      show((was) => ({
        ...was,
        busy: false,
        error: `Файл больше ${weigh(limit)} — уменьшите его`,
      }));
      return null;
    }
    show((was) => ({
      ...was, name: file.name, size: file.size, id: null, busy: true, error: null,
    }));
    try {
      const saved = await api.uploadEmployeeFile(file, purpose);
      show((was) => ({ ...was, id: saved.id, busy: false, error: null }));
      return saved.id;
    } catch (error) {
      // Отказ сервера показывается как есть: он знает про содержимое
      // файла то, чего не знает браузер, — например, что расширение
      // и настоящий тип не совпали.
      show((was) => ({ ...was, id: null, busy: false, error: messageFor(error) }));
      return null;
    }
  }

  function choosePhoto(file: File) {
    if (preview) URL.revokeObjectURL(preview);
    setPreview(PHOTO_TYPES.includes(file.type) ? URL.createObjectURL(file) : null);
    setPhoto({ key: 'photo', kind: 'OTHER', title: 'Фотография', name: file.name,
               size: file.size, id: null, busy: true, error: null });
    setCommon(null);
    void (async () => {
      const saved = await accept(file, 'photo',
        (next) => setPhoto((was) => (was ? next(was) : was)));
      // В правке фотография встаёт в карточку сразу: ждать «Сохранить»
      // ей незачем — она ничего не переводит и ни с чем не спорит.
      if (!saved || !editing) return;
      try {
        await api.setEmployeePhoto(id, saved);
      } catch (error) {
        setPhoto((was) => (was ? { ...was, error: messageFor(error) } : was));
      }
    })();
  }

  function dropPhoto() {
    if (preview) URL.revokeObjectURL(preview);
    setPreview(null);
    setPhoto(null);
  }

  /** Файл к строке из списка. Строка заводится в момент выбора файла. */
  function chooseNeeded(need: Need, file: File) {
    setCommon(null);
    const prior = papers[need.key]?.docId ?? null;
    setPapers((was) => ({
      ...was,
      [need.key]: was[need.key] ?? {
        key: need.key, kind: need.kind, title: need.title,
        name: null, size: 0, id: null, busy: true, error: null,
      },
    }));
    void (async () => {
      const saved = await accept(file, 'document', (next) =>
        setPapers((was) => {
          const one = was[need.key]
            ?? { key: need.key, kind: need.kind, title: need.title,
                 name: null, size: 0, id: null, busy: false, error: null };
          return { ...was, [need.key]: next(one) };
        }));
      if (!saved || !editing) return;
      try {
        // Прежняя бумага снимается ДО новой: «прочее» на сервере не
        // объединяется по виду, и без этого замена завела бы вторую
        // строку с тем же названием.
        if (prior) await api.removeEmployeeDocument(id, prior);
        const made = await api.attachEmployeeDocument(id, {
          kind: need.kind, file_id: saved, title: need.title,
        });
        const docId = String(made['id'] ?? '');
        setPapers((was) => {
          const one = was[need.key];
          return one ? { ...was, [need.key]: { ...one, docId } } : was;
        });
      } catch (error) {
        setPapers((was) => {
          const one = was[need.key];
          return one ? { ...was, [need.key]: { ...one, error: messageFor(error) } } : was;
        });
      }
    })();
  }

  function dropNeeded(key: string) {
    const gone = papers[key]?.docId ?? null;
    setPapers((was) => {
      const next = { ...was };
      delete next[key];
      return next;
    });
    if (editing && gone) {
      void api.removeEmployeeDocument(id, gone).catch((error) => {
        setCommon(messageFor(error));
      });
    }
  }

  function addExtra() {
    setExtra((was) => [
      ...was,
      { key: makeKey(), kind: 'OTHER', title: '', name: null, size: 0,
        id: null, busy: false, error: null },
    ]);
  }

  function chooseExtra(key: string, file: File) {
    setCommon(null);
    const row = extra.find((one) => one.key === key);
    const prior = row?.docId ?? null;
    void (async () => {
      const saved = await accept(file, 'document', (next) =>
        setExtra((was) => was.map((one) => (one.key === key ? next(one) : one))));
      if (!saved || !editing) return;
      try {
        if (prior) await api.removeEmployeeDocument(id, prior);
        const made = await api.attachEmployeeDocument(id, {
          kind: 'OTHER', file_id: saved,
          ...(row?.title.trim() ? { title: row.title.trim() } : {}),
        });
        const docId = String(made['id'] ?? '');
        setExtra((was) => was.map((one) => (one.key === key ? { ...one, docId } : one)));
      } catch (error) {
        setExtra((was) => was.map((one) =>
          (one.key === key ? { ...one, error: messageFor(error) } : one)));
      }
    })();
  }

  /** Убрать свободную строку: в правке она исчезает и на сервере. */
  function dropExtra(key: string) {
    const gone = extra.find((one) => one.key === key)?.docId ?? null;
    setExtra((was) => was.filter((one) => one.key !== key));
    if (editing && gone) {
      void api.removeEmployeeDocument(id, gone).catch((error) => {
        setCommon(messageFor(error));
      });
    }
  }

  const rows = [...Object.values(papers), ...extra];
  const filesBusy = photo?.busy === true || rows.some((one) => one.busy);
  // Выбран, но не записан. Пропустить такую строку значило бы создать
  // сотрудника без документа, который человек только что приложил.
  const stuck = (one: Paper) => one.name !== null && one.id === null && !one.busy;
  const filesStuck = (photo !== null && stuck(photo)) || rows.some(stuck);
  // Строка без выбранного файла — не повод отменять приём: её просто
  // ещё не заполнили, и отправлять нечего.
  const filesReady = rows.filter((one) => one.id !== null);

  const named = (block: typeof offices, id: string): string => {
    if (!id || block.state !== 'ready') return '';
    const list = block.data.items as { id: string; name: string }[];
    return list.find((item) => item.id === id)?.name ?? '';
  };

  const officeName = named(offices, draft.office_id);
  const departmentName =
    departments.state === 'ready'
      ? departments.data.items.find((item) => item.id === draft.department_id)?.name ?? ''
      : '';
  const positionName =
    positions.state === 'ready'
      ? positions.data.items.find((item) => item.id === draft.position_id)?.name ?? ''
      : '';
  const scheduleName =
    schedules.state === 'ready'
      ? schedules.data.items.find((item) => item.id === draft.schedule_id)?.name ?? ''
      : '';
  const regionName =
    regions.state === 'ready'
      ? regions.data.items.find((item) => item.id === draft.region_id)?.name ?? ''
      : '';
  const fullName = [draft.last_name, draft.first_name, draft.middle_name]
    .filter(Boolean)
    .join(' ');

  /**
   * Значение поля словами: «Бухарская область», а не идентификатор.
   *
   * Окно сверки читает человек, и «было 27500be4… стало 55958a2e…»
   * ему ничего не говорит.
   */
  function shown(field: keyof Draft, value: string): string {
    if (!value) return 'не указано';
    const named = (rows: { id: string; name: string }[]) =>
      rows.find((one) => one.id === value)?.name ?? value;
    switch (field) {
      case 'gender': return named(GENDERS);
      case 'marital_status': return named(MARITAL_STATUSES);
      case 'employment_type': return named(EMPLOYMENT_TYPES);
      case 'probation': return named(PROBATION);
      case 'birth_date':
      case 'hire_date':
      case 'probation_from':
      case 'probation_to': return longDate(value);
      case 'region_id':
        return named(regions.state === 'ready' ? regions.data.items : []);
      case 'office_id':
        return named(offices.state === 'ready' ? offices.data.items : []);
      case 'department_id':
        return named(departments.state === 'ready' ? departments.data.items : []);
      case 'position_id':
        return named(positions.state === 'ready' ? positions.data.items : []);
      case 'schedule_id':
        return named(schedules.state === 'ready' ? schedules.data.items : []);
      case 'manager_employee_id':
        return managers.state === 'ready'
          ? managers.data.items.find((one) => one.id === value)?.full_name ?? value
          : value;
      default: return value;
    }
  }

  /** Проверка одного шага. Возвращает `false` и подсвечивает поля. */
  function check(which: number): boolean {
    const found: Partial<Record<keyof Draft, string>> = {};
    for (const field of NEEDED[which] ?? []) {
      // В правке ПИНФЛ не спрашиваем: карточка его не отдаёт, и пустое
      // поле означало бы «не заполнен», а не «не меняется».
      if (editing && field === 'pinfl') continue;
      if (draft[field].trim() === '') found[field] = 'Заполните поле';
    }
    if (which === 1
        && draft.probation_from && draft.probation_to
        && draft.probation_to < draft.probation_from) {
      found.probation_to = 'Конец стажировки раньше её начала';
    }
    if (which === 0) {
      // ПИНФЛ проверяется здесь же, а не только на сервере: четырнадцать
      // цифр видно сразу, и гонять запрос ради этого незачем.
      const digits = draft.pinfl.replace(/\D/g, '');
      if (draft.pinfl.trim() !== '' && digits.length !== 14) {
        found.pinfl = 'ПИНФЛ состоит из 14 цифр';
      }
      if (draft.corporate_email.trim() !== '' && !draft.corporate_email.includes('@')) {
        found.corporate_email = 'Похоже на неполный адрес';
      }
    }
    setErrors(found);
    return Object.keys(found).length === 0;
  }

  /** Вперёд — только через проверку текущего шага. Назад — свободно. */
  function go(next: number) {
    if (next <= step) {
      setStep(next);
      return;
    }
    for (let at = step; at < next; at += 1) {
      if (!check(at)) {
        setStep(at);
        return;
      }
    }
    setErrors({});
    setStep(next);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  /**
   * Сохранить правку: карточка, перевод, график — по очереди.
   *
   * Общей транзакции у них нет, поэтому порядок важен и отказ должен
   * назвать, что уже записалось. Не изменённое не отправляется вовсе:
   * перевод «на тот же офис» создал бы лишний период в истории.
   */
  async function save() {
    if (sending) return;
    setSending(true);
    setCommon(null);
    const done: string[] = [];
    try {
      // Собираем только изменённое: сервер принимает эти поля и
      // отвергает весь запрос, если среди них окажется чужое.
      const personal: api.EmployeeEdit = {};
      const changed = (field: keyof api.EmployeeEdit & keyof Draft) =>
        draft[field] !== initial[field];
      if (changed('last_name')) personal.last_name = draft.last_name.trim();
      if (changed('first_name')) personal.first_name = draft.first_name.trim();
      if (changed('middle_name')) personal.middle_name = draft.middle_name.trim() || null;
      if (changed('birth_date')) personal.birth_date = draft.birth_date || null;
      if (changed('phone')) personal.phone = draft.phone.trim() || null;
      if (changed('corporate_email')) {
        personal.corporate_email = draft.corporate_email.trim() || null;
      }
      if (changed('gender')) personal.gender = draft.gender || null;
      if (changed('marital_status')) personal.marital_status = draft.marital_status || null;
      for (const field of PROBATION_DATES) {
        if (draft[field] === initial[field]) continue;
        // Снятая стажировка уносит и свои даты.
        personal[field as 'probation_from' | 'probation_to'] =
          draft.probation ? draft[field] || null : null;
      }
      if (Object.keys(personal).length > 0) {
        await api.updateEmployee(id, personal);
        done.push('личные данные');
      }

      if (ASSIGNMENT.some((field) => draft[field] !== initial[field])) {
        await api.changeEmployeeAssignment(id, {
          effective_from: since,
          ...(draft.office_id ? { office_id: draft.office_id } : {}),
          department_id: draft.department_id || null,
          position_id: draft.position_id || null,
          manager_employee_id: draft.manager_employee_id || null,
          ...(draft.employment_type ? { employment_type: draft.employment_type } : {}),
        });
        done.push('перевод');
      }

      if (draft.schedule_id && draft.schedule_id !== initial.schedule_id) {
        await api.assignScheduleToEmployee(draft.schedule_id, {
          employee_id: id,
          valid_from: since,
        });
        done.push('график');
      }

      setAsking(false);
      navigate(`/employees/${id}`, { replace: true });
    } catch (error) {
      setAsking(false);
      const field = error instanceof ApiFailure ? error.field : null;
      if (field && field in EMPTY) {
        setErrors((was) => ({ ...was, [field as keyof Draft]: reasonFor(field) }));
        setStep(stepOf(field as keyof Draft));
      }
      // Что успело записаться — говорим прямо: иначе кадровик повторит
      // правку целиком и заведёт второй период назначения.
      setCommon(
        done.length === 0
          ? messageFor(error)
          : `${done.join(' и ')} сохранены, дальше не прошло: ${messageFor(error)}`,
      );
    } finally {
      setSending(false);
    }
  }

  async function send() {
    if (sending) return;
    setSending(true);
    setCommon(null);
    try {
      const body: api.OnboardBody = {
        idempotency_key: key.current,
        last_name: draft.last_name.trim(),
        first_name: draft.first_name.trim(),
        middle_name: draft.middle_name.trim() || null,
        birth_date: draft.birth_date || null,
        pinfl: draft.pinfl.replace(/\D/g, ''),
        phone: draft.phone.trim(),
        corporate_email: draft.corporate_email.trim() || null,
        telegram_username: draft.telegram_username.trim() || null,
        hire_date: draft.hire_date,
        region_id: draft.region_id || null,
        office_id: draft.office_id,
        department_id: draft.department_id,
        position_id: draft.position_id,
        manager_employee_id: draft.manager_employee_id || null,
        employment_type: draft.employment_type,
        employment_status: draft.probation ? 'PROBATION' : 'ACTIVE',
        // Срок уходит только вместе со стажировкой: у принятого в штат
        // этим датам не к чему относиться.
        probation_from: draft.probation ? draft.probation_from || null : null,
        probation_to: draft.probation ? draft.probation_to || null : null,
        schedule_id: draft.schedule_id,
        gender: draft.gender || null,
        marital_status: draft.marital_status || null,
        // Файлы уже на сервере: приём только ссылается на них, и
        // сотрудник появляется вместе с фотографией и бумагами, а не
        // до них.
        photo_file_id: photo?.id ?? null,
        documents: filesReady.map((one) => ({
          kind: one.kind,
          file_id: one.id as string,
          ...(one.title.trim() || one.name
            ? { title: one.title.trim() || (one.name as string) }
            : {}),
        })),
      };
      const result = await api.onboardEmployee(body);
      setAsking(false);
      if (result.telegram.link) {
        setTelegramInvite({ employeeId: result.employee.id, link: result.telegram.link });
      } else {
        // Telegram необязателен: если его не подготовили, не держим HR
        // в пустом окне, а сразу открываем карточку.
        navigate(`/employees/${result.employee.id}`, { replace: true });
      }
    } catch (error) {
      setAsking(false);
      const field = error instanceof ApiFailure ? error.field : null;
      if (field && field in EMPTY) {
        setErrors((was) => ({ ...was, [field as keyof Draft]: reasonFor(field) }));
        // Ошибка поля видна только на своём шаге — туда и возвращаем.
        setStep(stepOf(field as keyof Draft));
      } else {
        setCommon(messageFor(error));
      }
    } finally {
      setSending(false);
    }
  }

  return (
    <AppShell breadcrumb={editing ? 'Правка сотрудника' : 'Новый сотрудник'}
              section="employees">
      <div className="nh">
        <Link className="nh-back" to={editing ? `/employees/${id}` : '/employees'}>
          <AppIcon name="back" size={16} />
          {editing ? 'К карточке' : 'Сотрудники'}
        </Link>

        <header className="nh-head">
          <h1 className="nh-head__title">
            {editing ? (fullName || 'Правка сотрудника') : 'Новый сотрудник'}
          </h1>
          <span className="nh-head__step">Шаг {step + 1} из {steps.length}</span>
        </header>

        {editing && card.state === 'loading' && (
          <p className="nh-lead">Загружаем данные сотрудника…</p>
        )}
        {editing && card.state === 'error' && (
          <p className="nh-fail" role="alert">Не удалось открыть карточку сотрудника.</p>
        )}

        <nav className="nh-steps" aria-label="Шаги формы">
          {steps.map((one, at) => (
            <button key={one.key} type="button"
                    className={at === step ? 'nh-step nh-step--on'
                      : at < step ? 'nh-step nh-step--done' : 'nh-step'}
                    aria-current={at === step ? 'step' : undefined}
                    onClick={() => go(at)}>
              <b>{at + 1}</b>
              {one.title}
            </button>
          ))}
        </nav>

        <section className="nh-card" aria-label={steps[step]?.title}>
          <div className="nh-card__head">
            <h2 className="nh-card__title">{steps[step]?.title}</h2>
            <span className="nh-card__note">
              <i aria-hidden="true">*</i> — обязательные поля
            </span>
          </div>

          {step === 0 && (
            <>
              <PhotoRow photo={photo} preview={preview}
                        onPick={choosePhoto} onDrop={dropPhoto} />
              <div className="nh-grid">
                <Text label="Фамилия" required name="last_name"
                      draft={draft} errors={errors} set={set} />
                <Text label="Имя" required name="first_name"
                      draft={draft} errors={errors} set={set} />
                <Text label="Отчество" name="middle_name"
                      draft={draft} errors={errors} set={set} />
                <Field label="Дата рождения" required name="birth_date" errors={errors}>
                  <DatePicker label="Дата рождения" value={draft.birth_date}
                              max={now} now={now}
                              onChange={(value) => set('birth_date', value)} />
                </Field>
                {!editing && (
                  <Text label="ПИНФЛ" required name="pinfl" inputMode="numeric"
                        placeholder="14 цифр"
                        draft={draft} errors={errors} set={set} />
                )}
                <Text label="Телефон" required name="phone" inputMode="tel"
                      placeholder="+998 __ ___ __ __"
                      draft={draft} errors={errors} set={set} />
                <Text label="Email" name="corporate_email" inputMode="email"
                      placeholder="name@example.com"
                      draft={draft} errors={errors} set={set} />
                {!editing && (
                  <Text label="Telegram" name="telegram_username" placeholder="@username"
                        hint="Доступ к боту будет подготовлен автоматически"
                        draft={draft} errors={errors} set={set} />
                )}
                <Field label="Пол" name="gender" errors={errors}>
                  <Dropdown label="Пол" value={draft.gender} empty="Не указан"
                            options={GENDERS} onChange={(value) => set('gender', value)} />
                </Field>
                <Field label="Семейное положение" name="marital_status" errors={errors}>
                  <Dropdown label="Семейное положение" value={draft.marital_status}
                            empty="Не указано" options={MARITAL_STATUSES}
                            onChange={(value) => set('marital_status', value)} />
                </Field>
              </div>
              {editing && (
                <p className="nh-hint">
                  ПИНФЛ и Telegram здесь не меняются: ПИНФЛ — государственный
                  номер человека, а Telegram привязывается приглашением из
                  карточки. Фотография встаёт в карточку сразу после выбора,
                  ждать «Сохранить» ей незачем.
                </p>
              )}
            </>
          )}

          {step === 1 && (
            <div className="nh-grid">
              <Field label="Дата начала работы" required name="hire_date" errors={errors}>
                <DatePicker label="Дата начала работы" value={draft.hire_date} now={now}
                            onChange={(value) => set('hire_date', value)} />
              </Field>
              <Field label="Регион" required name="region_id" errors={errors}>
                <Dropdown label="Регион" value={draft.region_id} empty="Выберите регион"
                          options={regions.state === 'ready' ? regions.data.items : []}
                          onChange={(value) => set('region_id', value)} />
              </Field>
              <Field label="Офис" required name="office_id" errors={errors}>
                <Dropdown label="Офис" value={draft.office_id} empty="Выберите офис"
                          options={officeOptions}
                          onChange={(value) => {
                            set('office_id', value);
                            set('department_id', '');
                            set('manager_employee_id', '');
                          }} />
              </Field>
              <Field label="Отдел" required name="department_id" errors={errors}>
                <Dropdown label="Отдел" value={draft.department_id}
                          empty={draft.office_id ? 'Выберите отдел' : 'Сначала выберите офис'}
                          options={departments.state === 'ready' ? departments.data.items : []}
                          onChange={(value) => set('department_id', value)} />
              </Field>
              <Field label="Должность" required name="position_id" errors={errors}>
                <Dropdown label="Должность" value={draft.position_id} empty="Выберите должность"
                          options={positions.state === 'ready' ? positions.data.items : []}
                          onChange={(value) => set('position_id', value)} />
              </Field>
              <Field label="Руководитель" name="manager_employee_id" errors={errors}>
                <Dropdown label="Руководитель" value={draft.manager_employee_id}
                          empty={draft.office_id ? 'Без руководителя' : 'Сначала выберите офис'}
                          options={managers.state === 'ready'
                            ? managers.data.items.map((row) => ({ id: row.id, name: row.full_name }))
                            : []}
                          onChange={(value) => set('manager_employee_id', value)} />
              </Field>
              <Field label="Тип занятости" required name="employment_type" errors={errors}>
                <Dropdown label="Тип занятости" value={draft.employment_type} empty="Выберите тип"
                          options={EMPLOYMENT_TYPES}
                          onChange={(value) => set('employment_type', value)} />
              </Field>
              <Field label="Выходит на" name="probation" errors={errors}>
                <Dropdown label="Выходит на" value={draft.probation} empty="Сразу в штат"
                          options={PROBATION}
                          onChange={(value) => {
                            set('probation', value);
                            // Сняли стажировку — снимается и её срок:
                            // иначе у принятого в штат осталась бы пара
                            // дат, которым не к чему относиться.
                            if (!value) {
                              set('probation_from', '');
                              set('probation_to', '');
                            }
                          }} />
              </Field>
              {/* Срок стажировки появляется только вместе с ней. Обе
                  даты необязательны: стажёра берут и тогда, когда конец
                  ещё не назван. */}
              {draft.probation === 'PROBATION' && (
                <>
                  <Field label="Стажировка с" name="probation_from" errors={errors}>
                    <DatePicker label="Начало стажировки" value={draft.probation_from}
                                now={now}
                                onChange={(value) => set('probation_from', value)} />
                  </Field>
                  <Field label="Стажировка по" name="probation_to" errors={errors}>
                    <DatePicker label="Конец стажировки" value={draft.probation_to}
                                now={now}
                                {...(draft.probation_from ? { min: draft.probation_from } : {})}
                                onChange={(value) => set('probation_to', value)} />
                  </Field>
                  <p className="nh-say nh-say--wide">
                    <AppIcon name="clock" size={16} />
                    Даты необязательны — приём состоится и без них.
                    {draft.probation_to && ` Решение по стажёру принимают к ${longDate(draft.probation_to)}.`}
                  </p>
                </>
              )}
              <Field label="График работы" required name="schedule_id" errors={errors}>
                <Dropdown label="График работы" value={draft.schedule_id} empty="Выберите график"
                          options={schedules.state === 'ready' ? schedules.data.items : []}
                          onChange={(value) => set('schedule_id', value)} />
              </Field>
              {editing && moved && (
                <Field label="Дата перевода" required name="hire_date" errors={{}}>
                  <DatePicker label="Дата перевода" value={since} now={now}
                              onChange={(value) => setSince(value || now)} />
                </Field>
              )}
              {!editing && draft.hire_date && (
                <p className="nh-say">
                  <AppIcon name="clock" size={16} />
                  График начнёт действовать {longDate(draft.hire_date)}
                </p>
              )}
              {editing && moved && (
                <p className="nh-say">
                  <AppIcon name="clock" size={16} />
                  Прежнее назначение закроется накануне, новое начнётся
                  {' '}{longDate(since)}. История переводов сохранится.
                </p>
              )}
            </div>
          )}

          {step === 2 && (
            <>
              <p className="nh-lead">
                Ни один файл не обязателен: часть бумаг приносят на бумаге, часть
                сотрудник донесёт позже. {editing
                  ? 'Приложенное сохраняется сразу, отдельно от остальных правок: файл ничего не переводит и ни с чем не спорит.'
                  : 'Приём состоится и без них — приложите то, что уже есть.'}
              </p>

              <ul className="nh-docs">
                {PAPERS.map((need) => (
                  <DocRow key={need.key} need={need} paper={papers[need.key]}
                          onPick={(file) => chooseNeeded(need, file)}
                          onDrop={() => dropNeeded(need.key)} />
                ))}
              </ul>

              {extra.length > 0 && (
                <ul className="nh-docs nh-docs--extra">
                  {extra.map((one) => (
                    <ExtraRow key={one.key} paper={one}
                              onTitle={(title) => setExtra((was) =>
                                was.map((row) => (row.key === one.key ? { ...row, title } : row)))}
                              onPick={(file) => chooseExtra(one.key, file)}
                              onDrop={() => dropExtra(one.key)} />
                  ))}
                </ul>
              )}

              <button type="button" className="nh-add" onClick={addExtra}>
                <AppIcon name="plus" size={16} />
                Добавить другой документ
              </button>

              <h3 className="nh-sub">Приносят на бумаге</h3>
              <ul className="nh-paper">
                {ON_PAPER.map((one) => (
                  <li key={one.title}>
                    <b>{one.title}</b>
                    <span>{one.note}</span>
                  </li>
                ))}
              </ul>

              <p className="nh-contact">
                <AppIcon name="user" size={18} />
                <span>
                  Когда бумаги собраны, свяжитесь с HR-специалистом:
                  {' '}<b>{HR_CONTACT.name}</b>,{' '}
                  <a href={`tel:${HR_CONTACT.tel}`}>{HR_CONTACT.phone}</a>
                </span>
              </p>

              <p className="nh-hint">
                PDF, JPG или PNG до {weigh(DOCUMENT_MAX)}. Файлы сохранятся вместе
                с сотрудником — одним нажатием.
              </p>
            </>
          )}

          {common && <p className="nh-fail" role="alert">{common}</p>}

          <footer className="nh-foot">
            {step === 0 ? (
              <button type="button" className="nh-btn" onClick={() => navigate('/employees')}>
                Отмена
              </button>
            ) : (
              <button type="button" className="nh-btn" onClick={() => go(step - 1)}>
                <AppIcon name="back" size={16} />
                Назад
              </button>
            )}

            {step < steps.length - 1 ? (
              <button type="button" className="nh-btn nh-btn--blue" onClick={() => go(step + 1)}>
                {step === 0 ? 'Далее: работа' : 'Далее: документы'}
                <AppIcon name="arrow" size={16} />
              </button>
            ) : (
              <div className="nh-foot__end">
                <p className={filesStuck ? 'nh-hint nh-hint--bad' : 'nh-hint'}>
                  {editing
                    ? (changes.length === 0
                        ? 'Поля не изменены — документы сохраняются сразу'
                        : `Изменено полей: ${changes.length}`)
                    : filesBusy
                      ? 'Дождитесь загрузки файлов'
                      : filesStuck
                        ? 'Файл не загрузился: повторите выбор или уберите строку'
                        : 'Откроется окно подтверждения — страница не сменится'}
                </p>
                <button type="button" className="nh-btn nh-btn--blue"
                        disabled={sending || filesBusy || filesStuck
                                  || (editing && changes.length === 0)}
                        onClick={() => { if (check(0) && check(1)) setAsking(true); }}>
                  {editing ? 'Сохранить изменения' : 'Добавить сотрудника'}
                </button>
              </div>
            )}
          </footer>
        </section>
      </div>

      {asking && editing && (
        <ConfirmEdit
          name={fullName}
          rows={changes.map((one) => ({
            title: one.title,
            was: shown(one.field, one.was),
            now: shown(one.field, one.now),
          }))}
          since={ASSIGNMENT.some((field) => draft[field] !== initial[field])
            ? longDate(since) : ''}
          sending={sending}
          onBack={() => setAsking(false)}
          onConfirm={() => void save()}
        />
      )}
      {asking && !editing && (
        <Confirm
          name={fullName}
          office={officeName}
          region={regionName}
          department={departmentName}
          position={positionName}
          date={draft.hire_date ? longDate(draft.hire_date) : ''}
          schedule={scheduleName}
          telegram={draft.telegram_username.trim()}
          papers={filesReady.length}
          sending={sending}
          onBack={() => setAsking(false)}
          onConfirm={() => void send()}
        />
      )}
      {telegramInvite && (
        <TelegramInvite
          link={telegramInvite.link}
          onOpenEmployee={() => navigate(`/employees/${telegramInvite.employeeId}`, { replace: true })}
        />
      )}
    </AppShell>
  );
}

// --- поля --------------------------------------------------------------------

function Text({ label, name, draft, errors, set, required, hint, inputMode, placeholder }: {
  label: string;
  name: keyof Draft;
  draft: Draft;
  errors: Partial<Record<keyof Draft, string>>;
  set: (field: keyof Draft, value: string) => void;
  required?: boolean;
  hint?: string;
  inputMode?: 'numeric' | 'tel' | 'email';
  placeholder?: string;
}) {
  const id = useId();
  const bad = errors[name];
  return (
    <p className="nh-field">
      <label className="nh-label" htmlFor={id}>
        {label}
        {required && <i className="nh-star" aria-hidden="true">*</i>}
      </label>
      <input
        id={id}
        className={bad ? 'nh-input nh-input--bad' : 'nh-input'}
        value={draft[name]}
        required={required ?? false}
        aria-invalid={bad ? true : undefined}
        {...(placeholder ? { placeholder } : {})}
        {...(inputMode ? { inputMode } : {})}
        onChange={(event) => set(name, event.target.value)}
      />
      {/* Ошибка стоит под своим полем. Общее окно поверх формы не
          говорит, какое из четырнадцати полей править. */}
      {bad ? <span className="nh-bad" role="alert">{bad}</span>
           : hint ? <span className="nh-hint-line">{hint}</span> : null}
    </p>
  );
}

/** Поле, внутри которого живёт готовый выбор: дата или список. */
function Field({ label, name, required, errors, children }: {
  label: string;
  name: keyof Draft;
  required?: boolean;
  errors: Partial<Record<keyof Draft, string>>;
  children: React.ReactNode;
}) {
  const bad = errors[name];
  return (
    <p className={bad ? 'nh-field nh-field--bad' : 'nh-field'}>
      <span className="nh-label">
        {label}
        {required && <i className="nh-star" aria-hidden="true">*</i>}
      </span>
      {children}
      {bad && <span className="nh-bad" role="alert">{bad}</span>}
    </p>
  );
}

/**
 * Место под фотографию.
 *
 * Файл виден до сохранения: предпросмотр строится браузером из того же
 * файла, который ушёл на сервер. Пока идёт загрузка, кнопки замены и
 * удаления недоступны — иначе можно было бы удалить то, что ещё
 * записывается.
 */
function PhotoRow({ photo, preview, onPick, onDrop }: {
  photo: Paper | null;
  preview: string | null;
  onPick: (file: File) => void;
  onDrop: () => void;
}) {
  const field = useRef<HTMLInputElement>(null);
  const open = () => field.current?.click();

  return (
    <div className="nh-photo">
      <input
        ref={field}
        type="file"
        className="visually-hidden"
        accept={PHOTO_TYPES.join(',')}
        aria-label="Фотография сотрудника"
        onChange={(event) => {
          const file = event.target.files?.[0];
          // Поле очищается: иначе повторный выбор ТОГО ЖЕ файла не
          // вызовет события, и замена после ошибки не сработает.
          event.target.value = '';
          if (file) onPick(file);
        }}
      />

      <span className="nh-photo__face" aria-hidden="true">
        {preview ? <img src={preview} alt="" /> : <AppIcon name="user" size={20} />}
      </span>

      <button type="button" className="nh-photo__pick" onClick={open}
              disabled={photo?.busy === true}>
        {photo?.busy ? 'Загружаем…' : photo ? 'Заменить фото' : 'Загрузить фото'}
      </button>

      {photo && !photo.busy && (
        <button type="button" className="nh-photo__drop" onClick={onDrop}>Убрать</button>
      )}

      <span className="nh-photo__hint">JPG или PNG, до {weigh(PHOTO_MAX)}</span>

      {photo?.error && <p className="nh-bad nh-bad--wide" role="alert">{photo.error}</p>}
    </div>
  );
}

// --- документы ---------------------------------------------------------------

/** Строка списка документов: что это, зачем и приложен ли файл. */
function DocRow({ need, paper, onPick, onDrop }: {
  need: Need;
  paper: Paper | undefined;
  onPick: (file: File) => void;
  onDrop: () => void;
}) {
  const field = useRef<HTMLInputElement>(null);
  const done = paper?.id != null;

  return (
    <li className={done ? 'nh-doc nh-doc--done' : 'nh-doc'}>
      <input
        ref={field}
        type="file"
        className="visually-hidden"
        accept={DOCUMENT_TYPES.join(',')}
        aria-label={`Файл документа: ${need.title}`}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) onPick(file);
        }}
      />

      <span className="nh-doc__mark" aria-hidden="true">
        <AppIcon name={done ? 'check' : 'doc'} size={18} />
      </span>

      <span className="nh-doc__text">
        <b>{need.title}</b>
        <small>
          {paper?.name
            ? `${paper.name} · ${paper.busy ? 'загружаем…' : weigh(paper.size)}`
            : need.note}
        </small>
        {paper?.error && <span className="nh-bad" role="alert">{paper.error}</span>}
      </span>

      <span className="nh-doc__tools">
        <button type="button" className="nh-btn nh-btn--small"
                disabled={paper?.busy === true} onClick={() => field.current?.click()}>
          {paper?.name ? 'Заменить' : 'Загрузить'}
        </button>
        {paper && !paper.busy && (
          <button type="button" className="nh-doc__off" aria-label="Убрать файл" onClick={onDrop}>
            <AppIcon name="close" size={16} />
          </button>
        )}
      </span>
    </li>
  );
}

/** Своя строка: название пишут руками, вид всегда «другой документ». */
function ExtraRow({ paper, onTitle, onPick, onDrop }: {
  paper: Paper;
  onTitle: (title: string) => void;
  onPick: (file: File) => void;
  onDrop: () => void;
}) {
  const field = useRef<HTMLInputElement>(null);

  return (
    <li className={paper.id ? 'nh-doc nh-doc--done' : 'nh-doc'}>
      <input
        ref={field}
        type="file"
        className="visually-hidden"
        accept={DOCUMENT_TYPES.join(',')}
        aria-label="Файл другого документа"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) onPick(file);
        }}
      />

      <span className="nh-doc__mark" aria-hidden="true">
        <AppIcon name={paper.id ? 'check' : 'doc'} size={18} />
      </span>

      <span className="nh-doc__text">
        <input className="nh-input nh-input--slim" value={paper.title}
               placeholder="Название документа" aria-label="Название документа"
               onChange={(event) => onTitle(event.target.value)} />
        <small>
          {paper.name
            ? `${paper.name} · ${paper.busy ? 'загружаем…' : weigh(paper.size)}`
            : 'Файл не выбран'}
        </small>
        {paper.error && <span className="nh-bad" role="alert">{paper.error}</span>}
      </span>

      <span className="nh-doc__tools">
        <button type="button" className="nh-btn nh-btn--small" disabled={paper.busy}
                onClick={() => field.current?.click()}>
          {paper.name ? 'Заменить' : 'Загрузить'}
        </button>
        <button type="button" className="nh-doc__off" aria-label="Убрать строку"
                disabled={paper.busy} onClick={onDrop}>
          <AppIcon name="close" size={16} />
        </button>
      </span>
    </li>
  );
}

// --- подтверждение ----------------------------------------------------------

/**
 * Подтверждение поверх формы, а не отдельная страница.
 *
 * Резюме собрано из того, что человек уже ввёл: ФИО, должность, отдел,
 * регион, офис, график, Telegram и число приложенных документов. Самих
 * документов здесь нет — ни имён файлов, ни содержимого: их место в
 * карточке сотрудника, а не в окне подтверждения и тем более не в
 * сообщении боту.
 */
function Confirm({ name, office, region, department, position, date, schedule,
                   telegram, papers, sending, onBack, onConfirm }: {
  name: string;
  office: string;
  region: string;
  department: string;
  position: string;
  date: string;
  schedule: string;
  telegram: string;
  papers: number;
  sending: boolean;
  onBack: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="nh-modal" role="dialog" aria-modal="true"
         aria-label="Создать сотрудника?"
         onMouseDown={(event) => { if (event.target === event.currentTarget && !sending) onBack(); }}>
      <div className="nh-modal__box">
        <h2 className="nh-modal__title">Создать сотрудника?</h2>
        <p className="nh-modal__text">
          Проверьте данные. После создания сотруднику уйдёт приглашение
          в Telegram — документы туда не отправляются, они остаются
          в карточке.
        </p>

        {/* Подпись слева, значение справа, по строке на каждое. Раньше
            это был список определений в две строки: подпись и значение
            вставали друг под другом со сдвигом, и окно читалось как
            сломанное. */}
        <dl className="nh-sum">
          <Line title="ФИО" value={name || 'не заполнено'} />
          <Line title="Должность" value={position || 'не выбрана'} />
          <Line title="Отдел" value={department || 'не выбран'} />
          <Line title="Регион" value={region || 'не выбран'} />
          <Line title="Офис" value={office || 'не выбран'} />
          <Line title="Дата начала" value={date || 'не выбрана'} />
          <Line title="График" value={schedule || 'не выбран'} />
          <Line title="Telegram" value={telegram || 'ссылка будет подготовлена'} />
          <Line
            title="Документы"
            value={papers === 0 ? 'не приложены' : `${papers} ${papersWord(papers)}`}
          />
        </dl>

        <div className="nh-modal__foot">
          <button type="button" className="nh-btn" onClick={onBack} disabled={sending}>
            Назад и изменить
          </button>
          {/* Кнопка блокируется на время запроса: второе нажатие ушло бы
              с тем же ключом и вернуло того же человека, но человеку
              незачем видеть, как кнопка срабатывает дважды. */}
          <button type="button" className="nh-btn nh-btn--blue" onClick={onConfirm}
                  disabled={sending}>
            {sending ? 'Создаём…' : 'Создать сотрудника'}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Последнее действие HR: передать персональную ссылку новому сотруднику. */
function TelegramInvite({ link, onOpenEmployee }: {
  link: string;
  onOpenEmployee: () => void;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="nh-modal" role="dialog" aria-modal="true"
         aria-label="Ссылка для подключения Telegram">
      <div className="nh-modal__box">
        <h2 className="nh-modal__title">Ссылка для подключения Telegram готова</h2>
        <p className="nh-modal__text">
          Передайте её сотруднику. Он откроет ссылку, нажмёт Start, ознакомится
          с правилами и подтвердит условия использования.
        </p>
        <p className="nh-modal__link">
          <a href={link} target="_blank" rel="noreferrer">{link}</a>
        </p>
        <p className="nh-modal__note">
          После согласия Telegram подключится автоматически. Подтверждать
          привязку в HR больше не нужно.
        </p>
        <div className="nh-modal__foot">
          <button type="button" className="nh-btn" onClick={() => void copy()}>
            <AppIcon name={copied ? 'check' : 'send'} size={16} />
            {copied ? 'Ссылка скопирована' : 'Скопировать ссылку'}
          </button>
          <button type="button" className="nh-btn nh-btn--blue" onClick={onOpenEmployee}>
            Открыть карточку сотрудника
          </button>
        </div>
      </div>
    </div>
  );
}

/** Строка сверки в окне подтверждения: подпись слева, значение справа. */
function Line({ title, value }: { title: string; value: string }) {
  return (
    <div className="nh-sum__row">
      <dt>{title}</dt>
      <dd>{value}</dd>
    </div>
  );
}

/**
 * Окно правки: что на что меняется.
 *
 * Показывается только изменённое — перечислять всё подряд значило бы
 * прятать три правки среди пятнадцати неизменных строк.
 */
function ConfirmEdit({ name, rows, since, sending, onBack, onConfirm }: {
  name: string;
  rows: { title: string; was: string; now: string }[];
  /** Дата перевода — пусто, если назначение не меняется. */
  since: string;
  sending: boolean;
  onBack: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="nh-modal" role="dialog" aria-modal="true"
         aria-label="Сохранить изменения?"
         onMouseDown={(event) => { if (event.target === event.currentTarget && !sending) onBack(); }}>
      <div className="nh-modal__box">
        <h2 className="nh-modal__title">Сохранить изменения?</h2>
        <p className="nh-modal__text">
          {name}. Ниже — всё, что изменится.
          {since && ` Перевод вступит в силу ${since}: прежнее назначение
            закроется накануне, история сохранится.`}
        </p>

        <ul className="nh-diff">
          {rows.map((row) => (
            <li key={row.title}>
              <span className="nh-diff__title">{row.title}</span>
              <span className="nh-diff__pair">
                <s>{row.was}</s>
                <AppIcon name="arrow" size={16} />
                <b>{row.now}</b>
              </span>
            </li>
          ))}
        </ul>

        <div className="nh-modal__foot">
          <button type="button" className="nh-btn" onClick={onBack} disabled={sending}>
            Назад и изменить
          </button>
          <button type="button" className="nh-btn nh-btn--blue" onClick={onConfirm}
                  disabled={sending}>
            {sending ? 'Сохраняем…' : 'Сохранить'}
          </button>
        </div>
      </div>
    </div>
  );
}

// --- мелочи -----------------------------------------------------------------

/**
 * Карточка сотрудника — в поля формы.
 *
 * Читается то же, что показывает карточка: `current_assignment` и
 * `current_schedule` отданы вложенными объектами. ПИНФЛ и Telegram сюда
 * не попадают намеренно — они не правятся, и пустое поле честнее
 * подставленного «как бы значения».
 */
function draftOf(card: Record<string, unknown>): Draft {
  const text = (value: unknown): string =>
    value === null || value === undefined ? '' : String(value);
  const at = (card['current_assignment'] ?? {}) as Record<string, unknown>;
  const plan = (card['current_schedule'] ?? {}) as Record<string, unknown>;
  return {
    ...EMPTY,
    last_name: text(card['last_name']),
    first_name: text(card['first_name']),
    middle_name: text(card['middle_name']),
    birth_date: text(card['birth_date']),
    phone: text(card['phone']),
    corporate_email: text(card['corporate_email']),
    gender: text(card['gender']),
    marital_status: text(card['marital_status']),
    hire_date: text(card['hire_date']),
    region_id: text(at['region_id']),
    office_id: text(at['office_id']),
    department_id: text(at['department_id']),
    position_id: text(at['position_id']),
    manager_employee_id: text(at['manager_employee_id']),
    employment_type: text(at['employment_type']) || 'FULL_TIME',
    probation: card['employment_status'] === 'PROBATION' ? 'PROBATION' : '',
    probation_from: text(card['probation_from']),
    probation_to: text(card['probation_to']),
    schedule_id: text(plan['schedule_id']),
  };
}

/** Следующий день после даты «ГГГГ-ММ-ДД». */
function nextDay(day: string): string {
  const at = new Date(`${day}T12:00:00`);
  if (Number.isNaN(at.getTime())) return day;
  at.setDate(at.getDate() + 1);
  return at.toISOString().slice(0, 10);
}

/** На каком шаге живёт поле — чтобы вернуть туда, где видна ошибка. */
function stepOf(field: keyof Draft): number {
  return (NEEDED[1] ?? []).includes(field) ? 1 : 0;
}

/** «1 документ», «2 документа», «5 документов». */
function papersWord(count: number): string {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return 'документов';
  switch (count % 10) {
    case 1:
      return 'документ';
    case 2:
    case 3:
    case 4:
      return 'документа';
    default:
      return 'документов';
  }
}

/** Ключ одного нажатия: случайный и неповторимый. */
function makeKey(): string {
  const random = globalThis.crypto?.randomUUID?.();
  return random ?? `k-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Наши формулировки на отказ сервера — сервер отвечает кодом поля. */
function reasonFor(field: string): string {
  return (
    {
      pinfl: 'Сотрудник с таким ПИНФЛ уже есть',
      phone: 'Сотрудник с таким телефоном уже есть',
      corporate_email: 'Сотрудник с таким email уже есть',
      hire_date: 'Проверьте дату начала работы',
    } as Record<string, string>
  )[field] ?? 'Проверьте значение';
}

/** «4,2 МБ». Килобайт по 1024: так же считает и сервер. */
function weigh(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${Math.round(kb)} КБ`;
  return `${(kb / 1024).toFixed(1).replace('.', ',')} МБ`;
}

function wrongType(purpose: 'photo' | 'document'): string {
  return purpose === 'photo'
    ? 'Фотография должна быть JPG или PNG'
    : 'Документ должен быть PDF, JPG или PNG';
}
