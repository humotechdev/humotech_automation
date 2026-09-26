/**
 * Новая рассылка: шаблон → получатели → время → проверка.
 *
 * Четыре шага одной геометрии: слева — то, что выбирают, справа — что из
 * этого выйдет. Отправка необратима: сообщение уже в Telegram, и отозвать
 * его нельзя. Поэтому последний шаг ничего не спрашивает, а показывает —
 * что, кому и когда уйдёт, сколько человек опрос не получат и почему, — и
 * отправка всё равно спрашивает подтверждения.
 *
 * Кто получит, считает сервер той же функцией, что и при отправке: счёт
 * «42 получателя» на проверке и «40 из 42» в отчёте не должны расходиться
 * из-за того, что их посчитали по-разному. У запланированной рассылки
 * круг фиксируется при создании — утверждённые люди и получат опрос.
 *
 * Всё, что введено, живёт на странице и не пропадает при переходе между
 * шагами: «Назад» — это посмотреть, а не начать заново.
 */

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import * as api from '../api/crm';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import { AppSelectField } from '../components/AppSelect';
import { DatePicker } from '../components/DatePicker';
import {
  Confirm,
  Failed,
  Loading,
  Refusal,
  useSaving,
} from '../components/admin/Parts';
import { useBlock } from '../features/dashboard/data';
import { useSession } from '../features/auth/session';
import { todayIso } from '../features/employee/model';
import {
  SurveyFrame,
  TelegramPreview,
  atMoment,
  atZone,
  peopleLine,
  plural,
  questionsLine,
  zoneLine,
} from '../features/surveys/model';
import '../styles/admin.css';
import '../styles/surveys.css';

const STEPS = ['Шаблон', 'Получатели', 'Время', 'Проверка'] as const;

const KINDS: Array<[api.SurveyAudienceKind, string, string, AppIconName]> = [
  ['ALL', 'Все действующие сотрудники', 'Все, кто работает на момент отправки', 'users'],
  ['REGION', 'По региону', 'Сотрудники всех офисов региона', 'globe'],
  ['OFFICE', 'По офису', 'Сотрудники выбранных офисов', 'building'],
  ['DEPARTMENT', 'По отделу', 'Сотрудники выбранных отделов', 'grid'],
  ['POSITION', 'По должности', 'Все, кто работает на этих должностях', 'user'],
  ['EMPLOYEES', 'Конкретные сотрудники', 'Отметьте людей в списке', 'user-plus'],
];

/** Срок ответа в днях. «Без срока» — приём открыт, пока рассылку не отменят. */
const DEADLINES: Array<[string, string]> = [
  ['', 'Без срока'],
  ['3', 'Через 3 дня'],
  ['7', 'Через 7 дней'],
  ['14', 'Через 14 дней'],
  ['30', 'Через 30 дней'],
];

const REMINDERS: Array<[string, string]> = [
  ['', 'Не напоминать'],
  ['1', 'Через 1 день'],
  ['2', 'Через 2 дня'],
  ['3', 'Через 3 дня'],
  ['7', 'Через 7 дней'],
];

/** Время отправки — по получасам рабочего дня. */
const TIMES = Array.from({ length: 25 }, (_, at) => {
  const minutes = 8 * 60 + at * 30;
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
});

/** Текст приглашения — тот же, что уходит на сервере. */
function inviteText(title: string): string {
  return `HR просит пройти короткий опрос «${title}». Это займёт 2–3 минуты.`;
}

export function SurveyWizardPage() {
  const navigate = useNavigate();
  const session = useSession();
  const zone = session.status === 'authenticated' ? session.user.timezone : '';

  const [step, setStep] = useState(0);
  const [templateId, setTemplateId] = useState('');
  const [findTemplate, setFindTemplate] = useState('');
  const [questionsOpen, setQuestionsOpen] = useState(false);
  const [kind, setKind] = useState<api.SurveyAudienceKind>('ALL');
  const [picked, setPicked] = useState<string[]>([]);
  const [findPeople, setFindPeople] = useState('');
  const [when, setWhen] = useState<'now' | 'later'>('now');
  const [day, setDay] = useState('');
  const [time, setTime] = useState('10:00');
  const [repeat, setRepeat] = useState('');
  const [anonymous, setAnonymous] = useState(false);
  const [remindIn, setRemindIn] = useState('');
  const [dueIn, setDueIn] = useState('');
  const [touched, setTouched] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [preview, setPreview] = useState<api.SurveyAudiencePreview | null>(null);
  const [counting, setCounting] = useState(false);
  const saving = useSaving();

  const [templates, reload] = useBlock(
    (signal) => api.surveyTemplates({ status: 'PUBLISHED' }, signal),
    'wizard-templates',
    true,
  );
  const [regions] = useBlock((signal) => api.regions(signal), 'wizard-regions', kind === 'REGION');
  const [offices] = useBlock(
    (signal) => api.officesPage({ status: 'ACTIVE', limit: '200' }, signal),
    'wizard-offices', kind === 'OFFICE',
  );
  const [departments] = useBlock(
    (signal) => api.departmentsPage({ status: 'ACTIVE' }, signal),
    'wizard-departments', kind === 'DEPARTMENT',
  );
  const [positions] = useBlock((signal) => api.positions(signal), 'wizard-positions', kind === 'POSITION');
  const [staff] = useBlock(
    // Без отбора по состоянию: «работает» — это и ACTIVE, и PROBATION.
    (signal) => api.employees({ limit: '200' }, signal),
    'wizard-staff', kind === 'EMPLOYEES',
  );

  const rows = templates.state === 'ready' ? templates.data.items : [];
  const chosen = rows.find((one) => one.id === templateId);
  const shownTemplates = rows.filter((one) => {
    const needle = findTemplate.trim().toLowerCase();
    return !needle || one.title.toLowerCase().includes(needle)
      || (one.description ?? '').toLowerCase().includes(needle);
  });

  // Что можно отметить для выбранного вида круга.
  const choices = useMemo(() => {
    const list: Array<{ id: string; name: string; sub: string }> = (() => {
      if (kind === 'REGION' && regions.state === 'ready') {
        return regions.data.items.map((one) => ({ id: one.id, name: one.name, sub: '' }));
      }
      if (kind === 'OFFICE' && offices.state === 'ready') {
        return offices.data.items.map((one) => ({
          id: one.id, name: one.name, sub: one.region_name ?? '',
        }));
      }
      if (kind === 'DEPARTMENT' && departments.state === 'ready') {
        return departments.data.items.map((one) => ({
          id: one.id, name: one.name, sub: one.office_name ?? '',
        }));
      }
      if (kind === 'POSITION' && positions.state === 'ready') {
        return positions.data.items.map((one) => ({ id: one.id, name: one.name, sub: '' }));
      }
      if (kind === 'EMPLOYEES' && staff.state === 'ready') {
        return staff.data.items
          .filter((one) => one.employment_status !== 'TERMINATED'
            && one.employment_status !== 'ARCHIVED')
          .map((one) => ({
            id: one.id,
            name: one.full_name,
            sub: [one.current_assignment?.position_name, one.current_assignment?.office_name]
              .filter(Boolean).join(' · '),
          }));
      }
      return [];
    })();
    const needle = findPeople.trim().toLowerCase();
    return needle
      ? list.filter((one) => `${one.name} ${one.sub}`.toLowerCase().includes(needle))
      : list;
  }, [departments, findPeople, kind, offices, positions, regions, staff]);

  const choicesLoading = kind !== 'ALL' && [regions, offices, departments, positions, staff]
    .some((block, at) => ['REGION', 'OFFICE', 'DEPARTMENT', 'POSITION', 'EMPLOYEES'][at] === kind
      && block.state === 'loading');

  // Круг считает сервер — той же функцией, что при отправке.
  useEffect(() => {
    if (kind !== 'ALL' && picked.length === 0) {
      setPreview(null);
      return;
    }
    const stop = new AbortController();
    setCounting(true);
    const timer = window.setTimeout(() => {
      api.previewSurveyAudience(
        { audience_kind: kind, ...(kind === 'ALL' ? {} : { audience_ids: picked }) },
        stop.signal,
      )
        .then((data) => setPreview(data))
        .catch(() => { if (!stop.signal.aborted) setPreview(null); })
        .finally(() => { if (!stop.signal.aborted) setCounting(false); });
    }, 300);
    return () => { stop.abort(); window.clearTimeout(timer); };
  }, [kind, picked]);

  // Часы из полей — время организации: именно это обещает подпись.
  const at = day && time ? `${day}T${time}` : '';
  const start = when === 'later' && at ? atZone(at, zone) : new Date();
  const remindAt = remindIn ? new Date(start.getTime() + Number(remindIn) * 86_400_000) : null;
  const dueAt = dueIn ? new Date(start.getTime() + Number(dueIn) * 86_400_000) : null;

  const problems = useMemo(() => {
    const found: Record<string, string> = {};
    if (!templateId) found['template'] = 'Выберите шаблон';
    if (kind !== 'ALL' && picked.length === 0) {
      found['audience'] = 'Отметьте хотя бы одного получателя';
    } else if (preview && preview.total === 0) {
      found['audience'] = 'В этот круг сейчас никто не входит';
    }
    if (when === 'later') {
      if (!at) found['at'] = 'Укажите дату и время';
      else if (atZone(at, zone).getTime() < Date.now()) {
        found['at'] = 'Дата в прошлом: отправить задним числом нельзя';
      }
    }
    if (remindAt && dueAt && remindAt >= dueAt) {
      found['remind'] = 'Напоминание позже срока ответа: напоминать будет не о чем';
    }
    return found;
  }, [templateId, kind, picked, preview, when, at, zone, remindAt, dueAt]);

  const blocking = (index: number): string | undefined => {
    if (index === 0) return problems['template'];
    if (index === 1) return problems['audience'];
    if (index === 2) return problems['at'] ?? problems['remind'];
    return undefined;
  };

  const forward = () => {
    setTouched(true);
    if (blocking(step)) return;
    setTouched(false);
    setStep(Math.min(step + 1, STEPS.length - 1));
  };

  const submit = () => {
    let made: api.SurveyCampaign | null = null;
    void saving.run(
      async () => {
        made = await api.createSurveyCampaign({
          template_id: templateId,
          audience_kind: kind,
          ...(kind === 'ALL' ? {} : { audience_ids: picked }),
          ...(when === 'later' ? { scheduled_at: atZone(at, zone).toISOString() } : {}),
          ...(repeat ? { repeat_months: Number(repeat) } : {}),
          ...(remindAt ? { remind_at: remindAt.toISOString() } : {}),
          ...(dueAt ? { due_at: dueAt.toISOString() } : {}),
          send_now: when === 'now',
          is_anonymous: anonymous,
        });
      },
      () => {
        setConfirming(false);
        if (made) navigate(`/surveys/campaigns/${(made as api.SurveyCampaign).id}`);
      },
    );
  };

  const kindTitle = KINDS.find(([key]) => key === kind)?.[1] ?? '';
  const pickedNames = kind === 'ALL'
    ? []
    : picked.length <= 3
      ? choices.filter((one) => picked.includes(one.id)).map((one) => one.name)
      : [];
  const audienceLine = kind === 'ALL'
    ? 'Все действующие сотрудники'
    : pickedNames.length
      ? `${kindTitle}: ${pickedNames.join(', ')}`
      : `${kindTitle}: выбрано ${picked.length}`;
  const last = step === STEPS.length - 1;

  return (
    <SurveyFrame
      breadcrumb="Опросы / Новая рассылка"
      crumbs={[
        ['Рабочее пространство', '/'],
        ['Опросы', '/surveys'],
        ['Рассылки', '/surveys/campaigns'],
        ['Новая рассылка'],
      ]}
      title="Новая рассылка"
      actions={(
        <>
          <button type="button" className="sv-btn"
                  onClick={() => (step === 0 ? navigate('/surveys/campaigns') : setStep(step - 1))}>
            {step === 0 ? 'Отмена' : 'Назад'}
          </button>
          {last ? (
            <button type="button" className="sv-btn sv-btn--main"
                    disabled={saving.busy || Object.keys(problems).length > 0}
                    onClick={() => setConfirming(true)}>
              {when === 'now' ? 'Отправить рассылку' : 'Запланировать рассылку'}
            </button>
          ) : (
            <button type="button" className="sv-btn sv-btn--main" onClick={forward}>
              Далее
            </button>
          )}
        </>
      )}
    >
      <ol className="sv-stepper" aria-label="Шаги создания рассылки">
        {STEPS.map((label, index) => (
          <li key={label}
              className={index === step ? 'sv-stepper__on'
                : index < step ? 'sv-stepper__done' : undefined}>
            <button type="button" disabled={index > step}
                    aria-current={index === step ? 'step' : undefined}
                    onClick={() => index < step && setStep(index)}>
              <span className="sv-stepper__no">
                {index < step ? <AppIcon name="check" size={16} /> : index + 1}
              </span>
              <span className="sv-stepper__label">{label}</span>
            </button>
          </li>
        ))}
      </ol>

      <div className="sv-cols">
        <section className="sv-panel" aria-label={STEPS[step]}>
          {step === 0 && (
            <>
              <h2 className="sv-panel__title">Выберите шаблон</h2>
              <p className="sv-panel__about">
                Шаблон определяет вопросы, которые сотрудник получит в Telegram.
                Черновики отправлять нельзя — только опубликованные.
              </p>
              {templates.state === 'loading' && <Loading />}
              {templates.state === 'error' && <Failed onRetry={reload} />}
              {templates.state === 'ready' && (
                <>
                  <label className="sv-find sv-find--wide">
                    <AppIcon name="search" size={16} />
                    <input type="search" value={findTemplate} placeholder="Найти шаблон"
                           aria-label="Найти шаблон"
                           onChange={(event) => setFindTemplate(event.target.value)} />
                  </label>
                  {rows.length === 0 ? (
                    <p className="sv-note sv-note--inner">
                      <AppIcon name="info" size={16} />
                      Опубликованных шаблонов нет. Рассылать по черновику нельзя:
                      его правят прямо сейчас, и люди получили бы неготовые вопросы.
                    </p>
                  ) : (
                    <ul className="sv-choices" role="radiogroup" aria-label="Шаблон">
                      {shownTemplates.map((one) => (
                        <li key={one.id}>
                          <label className={one.id === templateId ? 'sv-choice sv-choice--on' : 'sv-choice'}>
                            <input type="radio" name="template" checked={one.id === templateId}
                                   onChange={() => setTemplateId(one.id)} />
                            <span className="sv-choice__text">
                              <b>{one.title}</b>
                              {one.description && <small>{one.description}</small>}
                            </span>
                            <span className="sv-choice__meta">
                              {questionsLine(one.questions.length)} · v{one.version}
                              <em>Опубликован</em>
                            </span>
                          </label>
                        </li>
                      ))}
                    </ul>
                  )}
                  {touched && problems['template'] && (
                    <p className="sv-error" role="alert">{problems['template']}</p>
                  )}
                </>
              )}
            </>
          )}

          {step === 1 && (
            <>
              <h2 className="sv-panel__title">Кому отправить</h2>
              <p className="sv-panel__about">
                Опрос уйдёт только работающим сотрудникам. Тем, у кого не
                привязан Telegram, он не дойдёт — справа видно, сколько таких.
              </p>
              <ul className="sv-kinds" role="radiogroup" aria-label="Круг получателей">
                {KINDS.map(([key, label, about, icon]) => (
                  <li key={key}>
                    <label className={kind === key ? 'sv-kind sv-kind--on' : 'sv-kind'}>
                      <input type="radio" name="kind" checked={kind === key}
                             onChange={() => { setKind(key); setPicked([]); setFindPeople(''); }} />
                      <AppIcon name={icon} size={18} />
                      <span>
                        <b>{label}</b>
                        <small>{about}</small>
                      </span>
                    </label>
                  </li>
                ))}
              </ul>

              {kind !== 'ALL' && (
                <div className="sv-picker">
                  <div className="sv-picker__head">
                    <label className="sv-find">
                      <AppIcon name="search" size={16} />
                      <input type="search" value={findPeople} placeholder="Найти в списке"
                             aria-label="Найти в списке"
                             onChange={(event) => setFindPeople(event.target.value)} />
                    </label>
                    <span className="sv-picker__count">Отмечено: {picked.length}</span>
                  </div>
                  {choicesLoading ? <Loading /> : (
                    <ul className="sv-picker__list">
                      {choices.map((one) => (
                        <li key={one.id}>
                          <label>
                            <input type="checkbox" checked={picked.includes(one.id)}
                                   onChange={(event) =>
                                     setPicked(event.target.checked
                                       ? [...picked, one.id]
                                       : picked.filter((two) => two !== one.id))} />
                            <span>
                              <b>{one.name}</b>
                              {one.sub && <small>{one.sub}</small>}
                            </span>
                          </label>
                        </li>
                      ))}
                      {choices.length === 0 && (
                        <li className="sv-picker__none">Ничего не нашлось</li>
                      )}
                    </ul>
                  )}
                </div>
              )}
              {touched && problems['audience'] && (
                <p className="sv-error" role="alert">{problems['audience']}</p>
              )}
            </>
          )}

          {step === 2 && (
            <>
              <h2 className="sv-panel__title">Когда отправить</h2>
              <div className="sv-when" role="radiogroup" aria-label="Когда отправить">
                <label className={when === 'now' ? 'sv-kind sv-kind--on' : 'sv-kind'}>
                  <input type="radio" name="when" checked={when === 'now'}
                         onChange={() => setWhen('now')} />
                  <AppIcon name="send" size={18} />
                  <span>
                    <b>Отправить сейчас</b>
                    <small>Опрос уйдёт сразу после подтверждения</small>
                  </span>
                </label>
                <label className={when === 'later' ? 'sv-kind sv-kind--on' : 'sv-kind'}>
                  <input type="radio" name="when" checked={when === 'later'}
                         onChange={() => setWhen('later')} />
                  <AppIcon name="calendar" size={18} />
                  <span>
                    <b>Запланировать отправку</b>
                    <small>Опрос уйдёт сам в выбранный день и час</small>
                  </span>
                </label>
              </div>

              {when === 'later' && (
                <div className="sv-fields sv-fields--row">
                  <div className="sv-field">
                    <span>Дата</span>
                    <DatePicker label="Дата отправки" value={day} now={todayIso()}
                                min={todayIso()} onChange={setDay} allowEmpty />
                  </div>
                  <div className="sv-field">
                    <span>Время</span>
                    <AppSelectField label="Время отправки" value={time} onChange={setTime}>
                      {TIMES.map((one) => <option key={one} value={one}>{one}</option>)}
                    </AppSelectField>
                  </div>
                  <p className="sv-field__hint">Время организации: {zoneLine(zone)}</p>
                  {touched && problems['at'] && (
                    <p className="sv-error" role="alert">{problems['at']}</p>
                  )}
                </div>
              )}

              <div className="sv-fields sv-fields--grid">
                <div className="sv-field">
                  <span>Напомнить, если нет ответа</span>
                  <AppSelectField label="Напомнить" value={remindIn} onChange={setRemindIn}>
                    {REMINDERS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                  </AppSelectField>
                  <small>Только тем, кто ещё не ответил</small>
                </div>
                <div className="sv-field">
                  <span>Закрыть опрос</span>
                  <AppSelectField label="Закрыть опрос" value={dueIn} onChange={setDueIn}>
                    {DEADLINES.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                  </AppSelectField>
                  <small>После этого ответить уже нельзя</small>
                </div>
                <div className="sv-field">
                  <span>Повторять</span>
                  <AppSelectField label="Повторять" value={repeat} onChange={setRepeat}>
                    <option value="">Не повторять</option>
                    <option value="2">Раз в 2 месяца</option>
                    <option value="3">Раз в 3 месяца</option>
                  </AppSelectField>
                  <small>Каждый повтор — своя рассылка со своими ответами</small>
                </div>
              </div>
              {touched && problems['remind'] && (
                <p className="sv-error" role="alert">{problems['remind']}</p>
              )}
            </>
          )}

          {step === 3 && (
            <>
              <h2 className="sv-panel__title">Проверьте рассылку</h2>
              <dl className="sv-review-list">
                <div>
                  <dt><AppIcon name="doc" size={18} />Шаблон</dt>
                  <dd>
                    <b>{chosen?.title}</b>
                    <small>
                      {chosen ? `${questionsLine(chosen.questions.length)} · версия ${chosen.version}` : ''}
                    </small>
                  </dd>
                </div>
                <div>
                  <dt><AppIcon name="users" size={18} />Кому</dt>
                  <dd>
                    <b>{audienceLine}</b>
                    <small>
                      {preview
                        ? `Получат ${peopleLine(preview.reachable)}`
                          + (preview.no_telegram
                            ? ` · не получат ${preview.no_telegram}: не привязан Telegram` : '')
                          + (preview.not_employed
                            ? ` · ${preview.not_employed} уже не работают` : '')
                        : 'Считаем…'}
                    </small>
                  </dd>
                </div>
                <div>
                  <dt><AppIcon name="calendar" size={18} />Отправка</dt>
                  <dd>
                    <b>{when === 'now' ? 'Сразу после подтверждения' : atMoment(atZone(at, zone).toISOString(), zone)}</b>
                    <small>{zoneLine(zone)}{repeat ? ` · повтор раз в ${repeat} мес.` : ''}</small>
                  </dd>
                </div>
                <div>
                  <dt><AppIcon name="lock" size={18} />Анонимность</dt>
                  <dd>
                    <label className="sv-anon">
                      <input type="checkbox" checked={anonymous} onChange={(event) => setAnonymous(event.target.checked)} />
                      <b>Анонимный опрос</b>
                    </label>
                    <small>
                      {anonymous
                        ? 'HR увидит только сводку по вопросам — без ответов по людям и без имён в выгрузке. После отправки изменить нельзя.'
                        : 'Опрос именной: у каждого ответа будет имя сотрудника.'}
                    </small>
                  </dd>
                </div>
                <div>
                  <dt><AppIcon name="bell" size={18} />Напоминание</dt>
                  <dd>
                    <b>{remindAt ? atMoment(remindAt.toISOString(), zone) : 'Не напоминать'}</b>
                    <small>{dueAt ? `Приём ответов до ${atMoment(dueAt.toISOString(), zone)}` : 'Без срока ответа'}</small>
                  </dd>
                </div>
              </dl>
              <p className="sv-note sv-note--inner">
                <AppIcon name="info" size={16} />
                {when === 'now'
                  ? 'Рассылка запомнит версию шаблона и список получателей в момент отправки: правка шаблона или состава отдела потом её не изменит.'
                  : repeat
                    ? 'Каждый повтор соберёт получателей заново — на свой день. Версия шаблона запоминается при каждой отправке.'
                    : 'Список получателей зафиксируется сейчас: пришедшие в отдел позже опрос не получат, а уволенные к дню отправки будут отмечены с причиной.'}
              </p>
              <Refusal text={saving.refusal} />
            </>
          )}
        </section>

        <aside className="sv-side">
          {step === 1 ? (
            <section className="sv-panel" aria-label="Кому уйдёт">
              <h2 className="sv-panel__title">Кому уйдёт</h2>
              {kind !== 'ALL' && picked.length === 0 ? (
                <p className="sv-panel__about">Отметьте, кого спросить, — здесь появится число и список.</p>
              ) : counting && !preview ? (
                <Loading />
              ) : preview ? (
                <>
                  <p className="sv-big">
                    {preview.reachable}
                    <span>{plural(preview.reachable, ['получит опрос', 'получат опрос', 'получат опрос'])}</span>
                  </p>
                  {(preview.no_telegram > 0 || preview.not_employed > 0) && (
                    <ul className="sv-warns">
                      {preview.no_telegram > 0 && (
                        <li>
                          <AppIcon name="alert" size={16} />
                          {preview.no_telegram} без привязки Telegram — опрос не дойдёт
                        </li>
                      )}
                      {preview.not_employed > 0 && (
                        <li>
                          <AppIcon name="alert" size={16} />
                          {preview.not_employed} уже не работают — их пропустим
                        </li>
                      )}
                    </ul>
                  )}
                  <ul className="sv-peeks">
                    {preview.people.slice(0, 8).map((one) => (
                      <li key={one.id}>
                        <span>
                          <b>{one.full_name}</b>
                          <small>{[one.office, one.department].filter(Boolean).join(' · ') || '—'}</small>
                        </span>
                        {!one.telegram && <em>без Telegram</em>}
                      </li>
                    ))}
                  </ul>
                  {preview.total > 8 && (
                    <p className="sv-panel__about">и ещё {preview.total - 8}</p>
                  )}
                </>
              ) : (
                <p className="sv-panel__about">Не удалось посчитать получателей. Попробуйте ещё раз.</p>
              )}
            </section>
          ) : step === 3 ? (
            <section className="sv-panel" aria-label="Сообщение в Telegram">
              <h2 className="sv-panel__title">Сообщение в Telegram</h2>
              <p className="sv-panel__about">Так приглашение увидит сотрудник</p>
              <TelegramPreview title={chosen?.title ?? 'Опрос'}>
                <p className="sv-tg__step">{inviteText(chosen?.title ?? 'Опрос')}</p>
                <span className="sv-tg__button">Начать опрос</span>
              </TelegramPreview>
            </section>
          ) : (
            <section className="sv-panel" aria-label="Что получит сотрудник">
              <h2 className="sv-panel__title">Что получит сотрудник</h2>
              {chosen ? (
                <>
                  <div className="sv-sendcard">
                    <span className="sv-sendcard__mark" aria-hidden="true">
                      <AppIcon name="send" size={20} />
                    </span>
                    <span>
                      <b>{chosen.title}</b>
                      <small>{questionsLine(chosen.questions.length)} · Telegram</small>
                    </span>
                  </div>
                  <p className="sv-panel__text">
                    Сотрудник получит опрос в Telegram, ответит на вопросы по
                    одному и сможет продолжить позже — ответы сохраняются.
                  </p>
                  <button type="button" className="sv-link" onClick={() => setQuestionsOpen((was) => !was)}>
                    <AppIcon name="list" size={16} />
                    {questionsOpen ? 'Скрыть вопросы' : 'Посмотреть вопросы'}
                  </button>
                  {questionsOpen && (
                    <ol className="sv-mini-q">
                      {chosen.questions.map((one) => <li key={one.id}>{one.text}</li>)}
                    </ol>
                  )}
                  <p className="sv-note sv-note--inner">
                    <AppIcon name="info" size={16} />
                    Рассылка запомнит версию шаблона. Последующие изменения шаблона
                    не изменят уже отправленный опрос.
                  </p>
                </>
              ) : (
                <p className="sv-panel__about">
                  Выберите шаблон — здесь появится то, что получит сотрудник.
                </p>
              )}
            </section>
          )}
        </aside>
      </div>

      {confirming && (
        <Confirm
          title={when === 'now' ? 'Отправить рассылку' : 'Запланировать рассылку'}
          what={when === 'now'
            ? `«${chosen?.title}» уйдёт ${preview ? peopleLine(preview.reachable) : 'получателям'} прямо сейчас.`
            : `«${chosen?.title}» уйдёт ${atMoment(atZone(at, zone).toISOString(), zone)}.`}
          consequence="Отозвать отправленное приглашение нельзя: сообщение останется в Telegram. Запланированную рассылку можно отменить до отправки."
          confirmLabel={when === 'now' ? 'Отправить' : 'Запланировать'}
          refusal={saving.refusal}
          busy={saving.busy}
          onCancel={() => setConfirming(false)}
          onConfirm={submit}
        />
      )}
    </SurveyFrame>
  );
}
