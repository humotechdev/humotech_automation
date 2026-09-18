/**
 * Приём сотрудника: одна форма, одно подтверждение, одна операция.
 *
 * Страница намеренно не мастер из четырёх шагов. Данные о человеке не
 * делятся на этапы: должность зависит от офиса, график — от даты выхода,
 * и, разложив это по шагам, HR ходил бы между ними туда-сюда. Всё на
 * одном экране, справа — то, что получится, и список того, что система
 * сделает сама.
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
import { useNavigate } from 'react-router-dom';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { AppIcon } from '../components/AppIcon';
import { AppShell } from '../components/AppShell';
import { DatePicker } from '../components/DatePicker';
import { Dropdown } from '../components/Dropdown';
import { longDate, today, useBlock } from '../features/dashboard/data';

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
  schedule_id: string;
  gender: string;
  marital_status: string;
};

const EMPTY: Draft = {
  last_name: '', first_name: '', middle_name: '', birth_date: '',
  pinfl: '', phone: '', corporate_email: '', telegram_username: '',
  hire_date: '', region_id: '', office_id: '', department_id: '',
  position_id: '', manager_employee_id: '', employment_type: 'FULL_TIME',
  probation: '', schedule_id: '', gender: '', marital_status: '',
};

/** Поля, без которых приём не состоится. Те же, что помечены звёздочкой. */
const REQUIRED: (keyof Draft)[] = [
  'last_name', 'first_name', 'birth_date', 'pinfl', 'phone',
  'hire_date', 'region_id', 'office_id', 'department_id', 'position_id',
  'employment_type', 'schedule_id',
];

/** Сколько всего полей считает индикатор заполнения. */
const COUNTED: (keyof Draft)[] = [
  ...REQUIRED, 'middle_name', 'corporate_email', 'telegram_username',
  'manager_employee_id', 'gender', 'marital_status',
];

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

/** Виды бумаг. Те же четыре, что знает сервер. */
const DOCUMENT_KINDS: { id: api.EmployeeDocumentKind; name: string }[] = [
  { id: 'IDENTITY', name: 'Паспорт или ID-карта' },
  { id: 'CONTRACT', name: 'Трудовой договор' },
  { id: 'HIRE_ORDER', name: 'Приказ о приёме' },
  { id: 'OTHER', name: 'Другой документ' },
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

/** Один приложенный файл в форме: до приёма он ещё ни к кому не привязан. */
type Paper = {
  /** Ключ строки: вид может повторяться у «Другой документ». */
  key: string;
  kind: api.EmployeeDocumentKind;
  /** Что показано человеку. `null` — файл ещё не выбран. */
  name: string | null;
  size: number;
  /** Знак файла на сервере. Пока `null`, приём его не возьмёт. */
  id: string | null;
  busy: boolean;
  error: string | null;
};

const EMPLOYMENT_TYPES = [
  { id: 'FULL_TIME', name: 'Полный рабочий день' },
  { id: 'PART_TIME', name: 'Неполный день' },
  { id: 'CONTRACT', name: 'Договор подряда' },
  { id: 'INTERN', name: 'Стажировка' },
];

/*
 * Испытательный срок — это статус, а не отдельное поле: у сотрудника в
 * базе есть PROBATION, и больше про испытательный срок там ничего нет.
 *
 * Поэтому здесь «да или нет», а не «три месяца или шесть», хотя в макете
 * нарисован выбор длительности. Выбор из двух сроков, которые
 * сохранялись бы одинаково, обещал бы, что система помнит дату окончания
 * — а она её не помнит. Когда появится поле с датой, вернутся и сроки.
 */
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

/** Что система сделает сама. Список не рекламный: каждая строка — шаг. */
const AUTOMATIC = [
  'Создаст карточку сотрудника',
  'Создаст первое назначение',
  'Назначит рабочий график',
  'Подготовит доступ к Telegram-боту',
  'Добавит сотрудника в учёт посещаемости',
  'Создаст необходимые записи доступа',
  'Запишет действие в журнал аудита',
];

const DOCUMENT_TITLE: Record<string, string> = {
  MISSING: 'Не добавлен',
  UPLOADED: 'Загружен',
  GENERATED_LATER: 'Будет сформирован',
  REVIEW: 'Требует проверки',
};

/** Чек-лист до приёма: те же три строки, что заведёт сервер. */
const PLANNED_DOCUMENTS = [
  { kind: 'IDENTITY', title: 'Паспорт или ID-карта', status: 'MISSING' },
  { kind: 'CONTRACT', title: 'Трудовой договор', status: 'GENERATED_LATER' },
  { kind: 'HIRE_ORDER', title: 'Приказ о приёме', status: 'GENERATED_LATER' },
];

export function NewEmployeePage() {
  const navigate = useNavigate();
  const now = today();

  const [draft, setDraft] = useState<Draft>(EMPTY);
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
  const [papers, setPapers] = useState<Paper[]>([]);
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

  const filled = COUNTED.filter((field) => draft[field].trim() !== '').length;

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
  ) {
    const types = purpose === 'photo' ? PHOTO_TYPES : DOCUMENT_TYPES;
    const limit = purpose === 'photo' ? PHOTO_MAX : DOCUMENT_MAX;
    if (!types.includes(file.type)) {
      show((was) => ({ ...was, busy: false, error: wrongType(purpose) }));
      return;
    }
    if (file.size > limit) {
      show((was) => ({
        ...was,
        busy: false,
        error: `Файл больше ${weigh(limit)} — уменьшите его`,
      }));
      return;
    }
    show((was) => ({
      ...was, name: file.name, size: file.size, id: null, busy: true, error: null,
    }));
    try {
      const saved = await api.uploadEmployeeFile(file, purpose);
      show((was) => ({ ...was, id: saved.id, busy: false, error: null }));
    } catch (error) {
      // Отказ сервера показывается как есть: он знает про содержимое
      // файла то, чего не знает браузер, — например, что расширение
      // и настоящий тип не совпали.
      show((was) => ({ ...was, id: null, busy: false, error: messageFor(error) }));
    }
  }

  function choosePhoto(file: File) {
    if (preview) URL.revokeObjectURL(preview);
    setPreview(PHOTO_TYPES.includes(file.type) ? URL.createObjectURL(file) : null);
    setPhoto({ key: 'photo', kind: 'OTHER', name: file.name, size: file.size,
               id: null, busy: true, error: null });
    setCommon(null);
    void accept(file, 'photo', (next) => setPhoto((was) => (was ? next(was) : was)));
  }

  function dropPhoto() {
    if (preview) URL.revokeObjectURL(preview);
    setPreview(null);
    setPhoto(null);
  }

  function addPaper() {
    setPapers((was) => [
      ...was,
      { key: makeKey(), kind: freeKind(was), name: null, size: 0,
        id: null, busy: false, error: null },
    ]);
  }

  function choosePaper(key: string, file: File) {
    setCommon(null);
    void accept(file, 'document', (next) =>
      setPapers((was) => was.map((one) => (one.key === key ? next(one) : one))));
  }

  const papersBusy = photo?.busy === true || papers.some((one) => one.busy);
  // Выбран, но не записан. Пропустить такую строку значило бы создать
  // сотрудника без документа, который человек только что приложил.
  const stuck = (one: Paper) => one.name !== null && one.id === null && !one.busy;
  const papersStuck = (photo !== null && stuck(photo)) || papers.some(stuck);
  // Строка без выбранного файла — не повод отменять приём: её просто
  // ещё не заполнили, и отправлять нечего.
  const papersReady = papers.filter((one) => one.id !== null);

  const named = (block: typeof offices, id: string): string => {
    if (!id || block.state !== 'ready') return '';
    const rows = block.data.items as { id: string; name: string }[];
    return rows.find((item) => item.id === id)?.name ?? '';
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

  function check(): boolean {
    const found: Partial<Record<keyof Draft, string>> = {};
    for (const field of REQUIRED) {
      if (draft[field].trim() === '') found[field] = 'Заполните поле';
    }
    // ПИНФЛ проверяется здесь же, а не только на сервере: четырнадцать
    // цифр видно сразу, и гонять запрос ради этого незачем.
    const digits = draft.pinfl.replace(/\D/g, '');
    if (draft.pinfl.trim() !== '' && digits.length !== 14) {
      found.pinfl = 'ПИНФЛ состоит из 14 цифр';
    }
    if (draft.corporate_email.trim() !== '' && !draft.corporate_email.includes('@')) {
      found.corporate_email = 'Похоже на неполный адрес';
    }
    setErrors(found);
    return Object.keys(found).length === 0;
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
        schedule_id: draft.schedule_id,
        gender: draft.gender || null,
        marital_status: draft.marital_status || null,
        // Файлы уже на сервере: приём только ссылается на них, и
        // сотрудник появляется вместе с фотографией и бумагами, а не
        // до них.
        photo_file_id: photo?.id ?? null,
        documents: papersReady.map((one) => ({
          kind: one.kind,
          file_id: one.id as string,
          ...(one.name ? { title: one.name } : {}),
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
      const named = error instanceof ApiFailure ? error.field : null;
      if (named && named in EMPTY) {
        setErrors((was) => ({ ...was, [named as keyof Draft]: reasonFor(named) }));
      } else {
        setCommon(messageFor(error));
      }
    } finally {
      setSending(false);
    }
  }

  return (
    <AppShell breadcrumb="Новый сотрудник" section="employees">
      <header className="head head--tight">
        <div>
          <h1 className="head__title">Новый сотрудник</h1>
          <p className="head__sub">
            Заполните данные — остальное система настроит автоматически
          </p>
        </div>
        <div className="hire__meter">
          <span className="hire__meter-text">
            Заполнено {filled} из {COUNTED.length}
          </span>
          <span className="hire__meter-track">
            <span
              className="hire__meter-fill"
              style={{ width: `${Math.round((filled / COUNTED.length) * 100)}%` }}
            />
          </span>
        </div>
      </header>

      <div className="hire">
        <div className="hire__main">
          <section className="panel">
            <h2 className="panel__title panel__title--row">
              <span className="panel__mark" aria-hidden="true">
                <AppIcon name="users" size={20} />
              </span>
              Личные данные
            </h2>

            <div className="hire__person">
              <PhotoBox
                photo={photo}
                preview={preview}
                onPick={choosePhoto}
                onDrop={dropPhoto}
              />

              <div className="hire__grid">
                <Text label="Фамилия" required name="last_name"
                      draft={draft} errors={errors} set={set} />
                <Text label="Имя" required name="first_name"
                      draft={draft} errors={errors} set={set} />
                <Text label="Отчество" name="middle_name"
                      draft={draft} errors={errors} set={set} />
                <Picked label="Дата рождения" required name="birth_date"
                        errors={errors}>
                  <DatePicker
                    label="Дата рождения"
                    value={draft.birth_date}
                    max={now}
                    now={now}
                    onChange={(value) => set('birth_date', value)}
                  />
                </Picked>
                <Text label="ПИНФЛ" required name="pinfl" inputMode="numeric"
                      draft={draft} errors={errors} set={set} />
                <Text label="Телефон" required name="phone" inputMode="tel"
                      draft={draft} errors={errors} set={set} />
                <Text label="Email" name="corporate_email" inputMode="email"
                      draft={draft} errors={errors} set={set} />
                <Text label="Telegram" name="telegram_username"
                      hint="Доступ будет подготовлен автоматически"
                      draft={draft} errors={errors} set={set} />
                <Chosen label="Пол" name="gender" errors={errors}
                        value={draft.gender} empty="Не указан"
                        options={GENDERS}
                        onChange={(value) => set('gender', value)} />
                <Chosen label="Семейное положение" name="marital_status"
                        errors={errors} value={draft.marital_status}
                        empty="Не указано" options={MARITAL_STATUSES}
                        onChange={(value) => set('marital_status', value)} />
              </div>
            </div>
          </section>

          <section className="panel">
            <h2 className="panel__title panel__title--row">
              <span className="panel__mark" aria-hidden="true">
                <AppIcon name="doc" size={20} />
              </span>
              Работа и назначение
            </h2>

            <div className="hire__grid">
              <Picked label="Дата начала работы" required name="hire_date"
                      errors={errors}>
                <DatePicker
                  label="Дата начала работы"
                  value={draft.hire_date}
                  now={now}
                  onChange={(value) => set('hire_date', value)}
                />
              </Picked>
              <Chosen label="Регион" required name="region_id" errors={errors}
                      value={draft.region_id} empty="Выберите регион"
                      options={regions.state === 'ready' ? regions.data.items : []}
                      onChange={(value) => set('region_id', value)} />
              <Chosen label="Офис" required name="office_id" errors={errors}
                      value={draft.office_id} empty="Выберите офис"
                      options={officeOptions}
                      onChange={(value) => {
                        set('office_id', value);
                        set('department_id', '');
                        set('manager_employee_id', '');
                      }} />
              <Chosen label="Отдел" required name="department_id" errors={errors}
                      value={draft.department_id}
                      empty={draft.office_id ? 'Выберите отдел' : 'Сначала выберите офис'}
                      options={departments.state === 'ready' ? departments.data.items : []}
                      onChange={(value) => set('department_id', value)} />
              <Chosen label="Должность" required name="position_id" errors={errors}
                      value={draft.position_id} empty="Выберите должность"
                      options={positions.state === 'ready' ? positions.data.items : []}
                      onChange={(value) => set('position_id', value)} />
              <Chosen label="Руководитель" name="manager_employee_id" errors={errors}
                      value={draft.manager_employee_id}
                      empty={draft.office_id ? 'Без руководителя' : 'Сначала выберите офис'}
                      options={
                        managers.state === 'ready'
                          ? managers.data.items.map((row) => ({
                              id: row.id,
                              name: row.full_name,
                            }))
                          : []
                      }
                      onChange={(value) => set('manager_employee_id', value)} />
              <Chosen label="Тип занятости" required name="employment_type"
                      errors={errors} value={draft.employment_type}
                      empty="Выберите тип" options={EMPLOYMENT_TYPES}
                      onChange={(value) => set('employment_type', value)} />
              <Chosen label="Выходит на" name="probation" errors={errors}
                      value={draft.probation} empty="Сразу в штат"
                      options={PROBATION}
                      onChange={(value) => set('probation', value)} />
              <Chosen label="График работы" required name="schedule_id"
                      errors={errors} value={draft.schedule_id}
                      empty="Выберите график"
                      options={schedules.state === 'ready' ? schedules.data.items : []}
                      onChange={(value) => set('schedule_id', value)} />
              {draft.hire_date && (
                <p className="hire__note">
                  <AppIcon name="clock" size={16} />
                  График начнёт действовать {longDate(draft.hire_date)}
                </p>
              )}
            </div>
          </section>

          <section className="panel">
            <h2 className="panel__title panel__title--row">
              <span className="panel__mark" aria-hidden="true">
                <AppIcon name="sheet" size={20} />
              </span>
              Документы
              <button type="button" className="btn panel__aside" onClick={addPaper}>
                <AppIcon name="plus" size={16} />
                Добавить документ
              </button>
            </h2>
            <ul className="hire__docs">
              {PLANNED_DOCUMENTS.map((item) => {
                // Приложенная бумага того же вида занимает место
                // запланированной: сервер их тоже не раздваивает, а
                // заполняет уже заведённую строку чек-листа.
                const own = papers.find(
                  (one) => one.kind === item.kind && one.id !== null,
                );
                return (
                  <li key={item.kind}>
                    <AppIcon name="doc" size={18} />
                    <span className="hire__doc-title">{item.title}</span>
                    <Status value={own ? 'UPLOADED' : item.status} />
                  </li>
                );
              })}
            </ul>

            {papers.length > 0 && (
              <ul className="hire__papers">
                {papers.map((one) => (
                  <PaperRow
                    key={one.key}
                    paper={one}
                    taken={papers.filter((other) => other.key !== one.key)}
                    onKind={(kind) =>
                      setPapers((was) =>
                        was.map((row) => (row.key === one.key ? { ...row, kind } : row)))}
                    onPick={(file) => choosePaper(one.key, file)}
                    onDrop={() =>
                      setPapers((was) => was.filter((row) => row.key !== one.key))}
                  />
                ))}
              </ul>
            )}

            <p className="hire__hint">
              PDF, JPG или PNG до {weigh(DOCUMENT_MAX)}. Бумаги сохранятся
              вместе с сотрудником — одним нажатием.
            </p>
          </section>
        </div>

        <aside className="hire__side">
          <section className="panel hire__check">
            <h2 className="panel__title panel__title--row">
              <span className="panel__mark" aria-hidden="true">
                <AppIcon name="report" size={20} />
              </span>
              Проверка перед добавлением
            </h2>

            <div className="hire__who">
              <span className="avatar" aria-hidden="true">
                {shortInitials(draft.last_name, draft.first_name)}
              </span>
              <span className="hire__who-text">
                <span className="hire__who-name">{fullName || 'Имя не заполнено'}</span>
                <span className="hire__who-role">{positionName || 'Должность не выбрана'}</span>
              </span>
            </div>

            <dl className="hire__facts">
              <Fact icon="building" title="Офис" value={officeName} />
              <Fact icon="users" title="Отдел" value={departmentName} />
              <Fact icon="calendar" title="Начало работы"
                    value={draft.hire_date ? longDate(draft.hire_date) : ''} />
              <Fact icon="clock" title="График" value={scheduleName} />
            </dl>

            <h3 className="hire__sub">Система выполнит автоматически</h3>
            <ul className="hire__auto">
              {AUTOMATIC.map((line) => (
                <li key={line}>
                  <AppIcon name="check" size={18} />
                  {line}
                </li>
              ))}
            </ul>

            <div className="hire__telegram">
              <p className="hire__telegram-head">
                <AppIcon name="send" size={20} />
                Telegram готов к подключению
              </p>
              <p className="hire__telegram-text">
                После первого запуска бот сразу откроет личный кабинет.
              </p>
              <p className="hire__telegram-note">
                <AppIcon name="alert" size={16} />
                Telegram не позволяет боту написать пользователю первым:
                ссылку открывает сам сотрудник.
              </p>
            </div>
          </section>

          {common && <p className="hire__fail" role="alert">{common}</p>}

          <div className="hire__actions">
            <button type="button" className="btn btn--wide"
                    onClick={() => navigate('/employees')}>
              Отмена
            </button>
            <button
              type="button"
              className="btn btn--dark btn--wide"
              disabled={sending || papersBusy || papersStuck}
              onClick={() => {
                if (check()) setAsking(true);
              }}
            >
              <AppIcon name="users" size={18} />
              Добавить сотрудника
            </button>
            <p className={papersStuck ? 'hire__hint hire__hint--bad' : 'hire__hint'}>
              {papersBusy
                ? 'Дождитесь загрузки файлов'
                : papersStuck
                  ? 'Файл не загрузился: повторите выбор или уберите строку'
                  : 'Откроется окно подтверждения — страница не сменится'}
            </p>
          </div>
        </aside>
      </div>

      {asking && (
        <Confirm
          name={fullName}
          office={officeName}
          region={regionName}
          department={departmentName}
          position={positionName}
          date={draft.hire_date ? longDate(draft.hire_date) : ''}
          schedule={scheduleName}
          telegram={draft.telegram_username.trim()}
          papers={papersReady.length}
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

// --- части формы ------------------------------------------------------------

function Text({ label, name, draft, errors, set, required, hint, inputMode }: {
  label: string;
  name: keyof Draft;
  draft: Draft;
  errors: Partial<Record<keyof Draft, string>>;
  set: (field: keyof Draft, value: string) => void;
  required?: boolean;
  hint?: string;
  inputMode?: 'numeric' | 'tel' | 'email';
}) {
  const id = useId();
  const bad = errors[name];
  return (
    <p className="hire__field">
      <label className="hire__label" htmlFor={id}>
        {label}
        {required && <span className="hire__star" aria-hidden="true">*</span>}
      </label>
      <input
        id={id}
        className={bad ? 'hire__input hire__input--bad' : 'hire__input'}
        value={draft[name]}
        required={required ?? false}
        aria-invalid={bad ? true : undefined}
        {...(inputMode ? { inputMode } : {})}
        onChange={(event) => set(name, event.target.value)}
      />
      {/* Ошибка стоит под своим полем. Общее окно поверх формы не
          говорит, какое из четырнадцати полей править. */}
      {bad ? <span className="hire__bad" role="alert">{bad}</span>
           : hint ? <span className="hire__hint-line">{hint}</span> : null}
    </p>
  );
}

function Picked({ label, name, required, errors, children }: {
  label: string;
  name: keyof Draft;
  required?: boolean;
  errors: Partial<Record<keyof Draft, string>>;
  children: React.ReactNode;
}) {
  const bad = errors[name];
  return (
    <p className={bad ? 'hire__field hire__field--bad' : 'hire__field'}>
      <span className="hire__label">
        {label}
        {required && <span className="hire__star" aria-hidden="true">*</span>}
      </span>
      {children}
      {bad && <span className="hire__bad" role="alert">{bad}</span>}
    </p>
  );
}

function Chosen({ label, name, required, errors, value, empty, options, onChange }: {
  label: string;
  name: keyof Draft;
  required?: boolean;
  errors: Partial<Record<keyof Draft, string>>;
  value: string;
  empty: string;
  options: { id: string; name: string }[];
  onChange: (value: string) => void;
}) {
  const bad = errors[name];
  return (
    <p className={bad ? 'hire__field hire__field--bad' : 'hire__field'}>
      <span className="hire__label">
        {label}
        {required && <span className="hire__star" aria-hidden="true">*</span>}
      </span>
      <Dropdown label={label} value={value} empty={empty} options={options}
                onChange={onChange} />
      {bad && <span className="hire__bad" role="alert">{bad}</span>}
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
function PhotoBox({ photo, preview, onPick, onDrop }: {
  photo: Paper | null;
  preview: string | null;
  onPick: (file: File) => void;
  onDrop: () => void;
}) {
  const field = useRef<HTMLInputElement>(null);
  const open = () => field.current?.click();

  return (
    <div className="hire__photo-box">
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

      {photo && preview ? (
        <>
          <button type="button" className="hire__photo hire__photo--has"
                  onClick={open} disabled={photo.busy}
                  title="Заменить фотографию">
            <img src={preview} alt="" />
            {photo.busy && <span className="hire__photo-busy">Загружаем…</span>}
          </button>
          <div className="hire__photo-tools">
            <button type="button" className="link" onClick={open}
                    disabled={photo.busy}>
              Заменить
            </button>
            <button type="button" className="link link--bad" onClick={onDrop}
                    disabled={photo.busy}>
              Удалить
            </button>
          </div>
        </>
      ) : (
        <button type="button" className="hire__photo" onClick={open}>
          <AppIcon name="plus" size={20} />
          <span>Добавить фото</span>
        </button>
      )}

      {photo?.error && (
        <p className="hire__bad hire__bad--wide" role="alert">{photo.error}</p>
      )}
      {!photo && (
        <p className="hire__photo-hint">JPG или PNG до {weigh(PHOTO_MAX)}</p>
      )}
    </div>
  );
}

/**
 * Одна приложенная бумага: вид, файл, размер.
 *
 * Вид выбирается до файла. Занятые виды из списка убраны — сервер
 * держит на них ограничение и второй паспорт отвергнет весь приём,
 * а не только лишнюю строку. «Другой документ» повторять можно.
 */
function PaperRow({ paper, taken, onKind, onPick, onDrop }: {
  paper: Paper;
  taken: Paper[];
  onKind: (kind: api.EmployeeDocumentKind) => void;
  onPick: (file: File) => void;
  onDrop: () => void;
}) {
  const field = useRef<HTMLInputElement>(null);
  const busy = taken.map((one) => one.kind);
  const kinds = DOCUMENT_KINDS.filter(
    (one) => one.id === 'OTHER' || one.id === paper.kind || !busy.includes(one.id),
  );

  return (
    <li className="hire__paper">
      <input
        ref={field}
        type="file"
        className="visually-hidden"
        accept={DOCUMENT_TYPES.join(',')}
        aria-label={`Файл документа: ${titleOf(paper.kind)}`}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) onPick(file);
        }}
      />

      <Dropdown
        label="Тип документа"
        value={paper.kind}
        empty="Выберите тип"
        options={kinds}
        onChange={(value) => onKind(value as api.EmployeeDocumentKind)}
      />

      <span className="hire__paper-file">
        {paper.name ? (
          <>
            <span className="hire__paper-name">{paper.name}</span>
            <span className="hire__paper-size">
              {paper.busy ? 'Загружаем…' : weigh(paper.size)}
            </span>
          </>
        ) : (
          <span className="hire__paper-size">Файл не выбран</span>
        )}
      </span>

      <button type="button" className="btn" onClick={() => field.current?.click()}
              disabled={paper.busy}>
        <AppIcon name={paper.name ? 'refresh' : 'plus'} size={16} />
        {paper.name ? 'Заменить' : 'Выбрать файл'}
      </button>
      <button type="button" className="pick pick--icon" onClick={onDrop}
              disabled={paper.busy} aria-label="Убрать документ">
        <AppIcon name="close" size={16} />
      </button>

      {paper.error && (
        <p className="hire__bad hire__bad--wide" role="alert">{paper.error}</p>
      )}
    </li>
  );
}

function Fact({ icon, title, value }: {
  icon: 'building' | 'users' | 'calendar' | 'clock';
  title: string;
  value: string;
}) {
  return (
    <div className="hire__fact">
      <dt>
        <AppIcon name={icon} size={18} />
        {title}
      </dt>
      {/* Пустое значение показано словами: прочерк рядом с «Офис»
          читается как «офиса нет», а не как «ещё не выбрали». */}
      <dd className={value ? undefined : 'hire__fact--empty'}>
        {value || 'не выбрано'}
      </dd>
    </div>
  );
}

function Status({ value }: { value: string }) {
  const shape =
    value === 'UPLOADED' ? 'hire__status hire__status--ok'
      : value === 'REVIEW' ? 'hire__status hire__status--wait'
        : value === 'GENERATED_LATER' ? 'hire__status hire__status--later'
          : 'hire__status';
  return (
    <span className={shape}>
      <AppIcon name={value === 'UPLOADED' ? 'check' : 'clock'} size={16} />
      {DOCUMENT_TITLE[value] ?? value}
    </span>
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
 *
 * Незаполненное поле показывается прочерком и подписью «не выбран» —
 * так видно, что именно уедет пустым, и можно вернуться.
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
    <div className="modal" role="dialog" aria-modal="true"
         aria-label="Создать сотрудника?">
      <div className="modal__box">
        <h2 className="modal__title">Создать сотрудника?</h2>
        <p className="modal__text">
          Проверьте данные. После создания сотруднику уйдёт приглашение
          в Telegram — документы туда не отправляются, они остаются
          в карточке.
        </p>
        <dl className="modal__facts">
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
        <div className="modal__actions">
          <button type="button" className="btn" onClick={onBack} disabled={sending}>
            Назад и изменить
          </button>
          {/* Кнопка блокируется на время запроса: второе нажатие ушло бы
              с тем же ключом и вернуло того же человека, но человеку
              незачем видеть, как кнопка срабатывает дважды. */}
          <button type="button" className="btn btn--dark" onClick={onConfirm}
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
    <div className="modal" role="dialog" aria-modal="true"
         aria-label="Ссылка для подключения Telegram">
      <div className="modal__box hire__invite">
        <h2 className="modal__title">Ссылка для подключения Telegram готова</h2>
        <p className="modal__text">
          Передайте её сотруднику. Он откроет ссылку, нажмёт Start, ознакомится
          с правилами и подтвердит условия использования.
        </p>
        <p className="hire__invite-link">
          <a href={link} target="_blank" rel="noreferrer">{link}</a>
        </p>
        <p className="hire__invite-note">
          После согласия Telegram подключится автоматически. Подтверждать
          привязку в HR больше не нужно.
        </p>
        <div className="modal__actions">
          <button type="button" className="btn" onClick={() => void copy()}>
            <AppIcon name={copied ? 'check' : 'send'} size={16} />
            {copied ? 'Ссылка скопирована' : 'Скопировать ссылку'}
          </button>
          <button type="button" className="btn btn--dark" onClick={onOpenEmployee}>
            Открыть карточку сотрудника
          </button>
        </div>
      </div>
    </div>
  );
}

/** Строка сверки в окне подтверждения. */
function Line({ title, value }: { title: string; value: string }) {
  return (
    <>
      <dt>{title}</dt>
      <dd>{value}</dd>
    </>
  );
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


// --- мелочи -----------------------------------------------------------------

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

function titleOf(kind: string): string {
  return DOCUMENT_KINDS.find((one) => one.id === kind)?.name ?? 'Документ';
}

/** Первый незанятый вид — чтобы новая строка не появлялась с отказом. */
function freeKind(rows: Paper[]): api.EmployeeDocumentKind {
  const busy = rows.map((one) => one.kind);
  return DOCUMENT_KINDS.find((one) => !busy.includes(one.id))?.id ?? 'OTHER';
}

function shortInitials(last: string, first: string): string {
  const letters = [last, first].map((part) => part.trim().charAt(0).toUpperCase());
  return letters.filter(Boolean).join('') || '—';
}
