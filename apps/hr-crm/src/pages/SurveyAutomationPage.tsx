/**
 * Правило автоматической рассылки: создание, карточка, история, настройки.
 *
 * Правило сработает через месяц — без кадровика и без подтверждения.
 * Единственный момент, когда ошибку ещё можно заметить, — сейчас, поэтому
 * справа настройка пересказана обычными словами: «сдвиг 1, час 10»
 * прочитать правильно нельзя, а «на следующий день после окончания
 * стажировки, в 10:00» — можно.
 *
 * Круг людей здесь не показывается числом: он определится В ДЕНЬ
 * СОБЫТИЯ. Сегодняшнее «12 человек» было бы обещанием, которого правило
 * не давало. Зато после срабатываний каждая строка истории говорит, кому
 * опрос ушёл, а кому нет — и почему.
 */

import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import { AppMultiSelect, AppPopover, AppSelectField } from '../components/AppSelect';
import {
  Confirm,
  Failed,
  Loading,
  Refusal,
  useSaving,
} from '../components/admin/Parts';
import { useBlock } from '../features/dashboard/data';
import { useSession } from '../features/auth/session';
import {
  State,
  SurveyFrame,
  TRIGGER,
  TRIGGER_ABOUT,
  atMoment,
  questionsLine,
  whenLine,
  zoneLine,
} from '../features/surveys/model';
import '../styles/admin.css';
import '../styles/surveys.css';

const KINDS: api.SurveyTriggerKind[] = [
  'PROBATION_END', 'FIRST_DAY', 'DAYS_AFTER_HIRE', 'BIRTHDAY', 'SCHEDULE',
];

/** С чего начинается история — первым шагом в «Как это будет работать». */
const EVENT_STEP: Record<api.SurveyTriggerKind, [string, AppIconName]> = {
  PROBATION_END: ['Заканчивается стажировка', 'calendar'],
  FIRST_DAY: ['Сотрудник выходит на работу', 'user-plus'],
  DAYS_AFTER_HIRE: ['Проходит N дней после выхода', 'calendar'],
  BIRTHDAY: ['День рождения сотрудника', 'calendar'],
  SCHEDULE: ['Наступает день по расписанию', 'refresh'],
};

type Draft = {
  title: string;
  template_id: string;
  trigger_kind: api.SurveyTriggerKind;
  offset_days: number;
  send_hour: number;
  send_minute: number;
  repeat_months: string;
  office_ids: string[];
  department_ids: string[];
  position_ids: string[];
};

const BLANK: Draft = {
  title: '',
  template_id: '',
  trigger_kind: 'PROBATION_END',
  offset_days: 1,
  send_hour: 10,
  send_minute: 0,
  repeat_months: '',
  office_ids: [],
  department_ids: [],
  position_ids: [],
};

function fromRule(rule: api.SurveyAutomation): Draft {
  return {
    title: rule.title,
    template_id: rule.template_id,
    trigger_kind: rule.trigger_kind,
    offset_days: rule.offset_days,
    send_hour: rule.send_hour,
    send_minute: rule.send_minute,
    repeat_months: rule.repeat_months ? String(rule.repeat_months) : '',
    office_ids: rule.scope?.office_ids ?? [],
    department_ids: rule.scope?.department_ids ?? [],
    position_ids: rule.scope?.position_ids ?? [],
  };
}

const TIMES = Array.from({ length: 29 }, (_, at) => {
  const minutes = 7 * 60 + at * 30;
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
});

function clock(hour: number, minute: number): string {
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
}

/** Правило одним предложением — как его прочтёт человек. */
function sentence(draft: Pick<Draft, 'trigger_kind' | 'offset_days' | 'send_hour' | 'send_minute' | 'repeat_months'>,
  templateTitle: string): string {
  const time = clock(draft.send_hour, draft.send_minute);
  const survey = templateTitle ? `опрос «${templateTitle}»` : 'опрос';
  if (draft.trigger_kind === 'SCHEDULE') {
    const months = Number(draft.repeat_months || 0);
    const period = months === 1 ? 'раз в месяц' : months === 3 ? 'раз в квартал'
      : months === 6 ? 'раз в полгода' : months === 12 ? 'раз в год' : 'по расписанию';
    return `Сотрудники будут получать ${survey} ${period} в ${time}.`;
  }
  const lead: Record<Exclude<api.SurveyTriggerKind, 'SCHEDULE'>, string> = {
    PROBATION_END: 'После завершения стажировки',
    FIRST_DAY: 'После выхода на работу',
    DAYS_AFTER_HIRE: 'После выхода на работу',
    BIRTHDAY: draft.offset_days === 0 ? 'В день рождения' : 'После дня рождения',
  };
  const shift = draft.offset_days === 0 ? 'в тот же день'
    : draft.offset_days === 1 ? 'на следующий день'
      : `через ${draft.offset_days} дн.`;
  return `${lead[draft.trigger_kind]} сотрудник получит ${survey} ${shift} в ${time}.`;
}

function crumbs(title: string): Array<[string, string?]> {
  return [
    ['Рабочее пространство', '/'],
    ['Опросы', '/surveys'],
    ['Автоматизации', '/surveys/automations'],
    [title],
  ];
}

export function SurveyAutomationPage() {
  const { id } = useParams();
  const fresh = !id || id === 'new';
  const navigate = useNavigate();

  if (fresh) {
    return (
      <SurveyFrame breadcrumb="Опросы / Новая автоматизация" crumbs={crumbs('Новая автоматизация')}
                   title="Новая автоматизация">
        <RuleForm current={null}
                  onCancel={() => navigate('/surveys/automations')}
                  onSaved={(rule) => navigate(`/surveys/automations/${rule.id}`)} />
      </SurveyFrame>
    );
  }
  return <RuleCard id={id} />;
}

// --- форма правила ----------------------------------------------------------------

function RuleForm({ current, onCancel, onSaved }: {
  current: api.SurveyAutomation | null;
  onCancel: () => void;
  onSaved: (rule: api.SurveyAutomation) => void;
}) {
  const session = useSession();
  const zone = session.status === 'authenticated' ? session.user.timezone : '';
  const [templates, reloadTemplates] = useBlock(
    (signal) => api.surveyTemplates({ status: 'PUBLISHED' }, signal), 'rule-templates', true,
  );
  const [offices] = useBlock(
    (signal) => api.officesPage({ status: 'ACTIVE', limit: '200' }, signal), 'rule-offices', true,
  );
  const [departments] = useBlock(
    (signal) => api.departmentsPage({ status: 'ACTIVE' }, signal), 'rule-departments', true,
  );
  const [positions] = useBlock((signal) => api.positions(signal), 'rule-positions', true);

  const [draft, setDraft] = useState<Draft>(current ? fromRule(current) : BLANK);
  const [touched, setTouched] = useState(false);
  const [shift, setShift] = useState<'same' | 'next' | 'days'>(
    !current ? 'next'
      : current.offset_days === 0 ? 'same' : current.offset_days === 1 ? 'next' : 'days',
  );
  const saving = useSaving();

  const rows = templates.state === 'ready' ? templates.data.items : [];
  // Шаблон по умолчанию — первый опубликованный: пустой выбор на
  // единственном доступном варианте лишний.
  useEffect(() => {
    if (!current && !draft.template_id && rows[0]) {
      setDraft((was) => ({ ...was, template_id: rows[0]!.id }));
    }
  }, [current, draft.template_id, rows]);

  const chosen = rows.find((one) => one.id === draft.template_id);
  const regular = draft.trigger_kind === 'SCHEDULE';
  // У «через N дней после выхода» N и есть сдвиг: ноль повторил бы
  // «первый рабочий день», а «на следующий день» — частный случай N.
  const counted = draft.trigger_kind === 'DAYS_AFTER_HIRE';
  const time = clock(draft.send_hour, draft.send_minute);
  const times = TIMES.includes(time) ? TIMES : [...TIMES, time].sort();

  const problems = useMemo(() => {
    const found: Record<string, string> = {};
    if (!draft.template_id) found['template'] = 'Выберите опубликованный шаблон';
    if (regular && !draft.repeat_months) found['repeat'] = 'У регулярной отправки нужен период';
    if (counted && draft.offset_days < 1) {
      found['offset'] = 'Укажите, через сколько дней после выхода — от 1 и больше';
    } else if (!regular && !counted && shift === 'days' && draft.offset_days < 2) {
      found['offset'] = 'Укажите, через сколько дней — от 2 и больше';
    }
    return found;
  }, [draft, regular, counted, shift]);

  const submit = () => {
    setTouched(true);
    if (Object.keys(problems).length > 0) return;
    const scope: api.SurveyAutomationScope = {};
    if (draft.office_ids.length) scope.office_ids = draft.office_ids;
    if (draft.department_ids.length) scope.department_ids = draft.department_ids;
    if (draft.position_ids.length) scope.position_ids = draft.position_ids;
    const body: api.SurveyAutomationDraft = {
      template_id: draft.template_id,
      trigger_kind: draft.trigger_kind,
      offset_days: regular ? 0 : draft.offset_days,
      send_hour: draft.send_hour,
      send_minute: draft.send_minute,
      repeat_months: regular ? Number(draft.repeat_months) : null,
      scope: Object.keys(scope).length ? scope : null,
      ...(draft.title.trim() ? { title: draft.title.trim() } : {}),
    };
    let saved: api.SurveyAutomation | null = null;
    void saving.run(
      async () => {
        saved = current
          ? await api.updateSurveyAutomation(current.id, body)
          : await api.createSurveyAutomation(body);
      },
      () => { if (saved) onSaved(saved); },
    );
  };

  const setShiftTo = (value: 'same' | 'next' | 'days') => {
    setShift(value);
    setDraft({
      ...draft,
      offset_days: value === 'same' ? 0 : value === 'next' ? 1 : Math.max(draft.offset_days, 7),
    });
  };

  return (
    <>
      <div className="sv-rulegrid">
        <ol className="sv-vsteps" aria-label="Разделы правила">
          {[['1', 'Шаблон опроса'], ['2', 'Событие'], ['3', 'Время отправки']].map(([no, label]) => (
            <li key={no}>
              <a href={`#rule-${no}`}>
                <span>{no}</span>{label}
              </a>
            </li>
          ))}
        </ol>

        <section className="sv-panel sv-ruleform" aria-label="Настройка правила">
          <div className="sv-rs" id="rule-1">
            <span className="sv-rs__no">1</span>
            <div className="sv-rs__body">
              <h2 className="sv-rs__title">Выберите шаблон опроса</h2>
              {templates.state === 'error' && <Failed onRetry={reloadTemplates} />}
              <AppSelectField label="Шаблон опроса" value={draft.template_id}
                              onChange={(value) => setDraft({ ...draft, template_id: value })}>
                <option value="">Выберите шаблон</option>
                {rows.map((one) => (
                  <option key={one.id} value={one.id}>{one.title} · v{one.version}</option>
                ))}
              </AppSelectField>
              {chosen && (
                <p className="sv-rs__note">
                  {questionsLine(chosen.questions.length)} · опубликован
                  {chosen.published_at ? ` ${atMoment(chosen.published_at, zone)}` : ''}
                </p>
              )}
              {touched && problems['template'] && <p className="sv-error" role="alert">{problems['template']}</p>}
              {rows.length === 0 && templates.state === 'ready' && (
                <p className="sv-note sv-note--inner">
                  <AppIcon name="info" size={16} />
                  Опубликованных шаблонов нет. Правило работает без присмотра и
                  черновик рассылать не вправе.
                </p>
              )}
              <label className="sv-field sv-field--top">
                <span>Название правила</span>
                <input className="sv-input" value={draft.title} maxLength={255}
                       placeholder="Необязательно — соберётся из шаблона и события"
                       onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
              </label>
            </div>
          </div>

          <div className="sv-rs" id="rule-2">
            <span className="sv-rs__no">2</span>
            <div className="sv-rs__body">
              <h2 className="sv-rs__title">Когда запустить опрос</h2>
              <AppSelectField label="Событие" value={draft.trigger_kind}
                              onChange={(value) => {
                                const offset = value === 'SCHEDULE' ? 0
                                  : value === 'DAYS_AFTER_HIRE' ? Math.max(draft.offset_days, 7)
                                    : draft.offset_days;
                                setShift(offset === 0 ? 'same' : offset === 1 ? 'next' : 'days');
                                setDraft({
                                ...draft,
                                trigger_kind: value as api.SurveyTriggerKind,
                                // Период бывает только у расписания, сдвиг —
                                // только у события: чужое значение правило
                                // никогда бы не применило.
                                repeat_months: value === 'SCHEDULE' ? (draft.repeat_months || '3') : '',
                                offset_days: offset,
                                });
                              }}>
                {KINDS.map((one) => <option key={one} value={one}>{TRIGGER[one]}</option>)}
              </AppSelectField>
              <p className="sv-rs__note">{TRIGGER_ABOUT[draft.trigger_kind]}</p>
              {regular && (
                <div className="sv-field">
                  <span>Как часто</span>
                  <AppSelectField label="Период" value={draft.repeat_months}
                                  onChange={(value) => setDraft({ ...draft, repeat_months: value })}>
                    <option value="">Выберите период</option>
                    <option value="1">Раз в месяц</option>
                    <option value="3">Раз в квартал</option>
                    <option value="6">Раз в полгода</option>
                    <option value="12">Раз в год</option>
                  </AppSelectField>
                  {touched && problems['repeat'] && <p className="sv-error" role="alert">{problems['repeat']}</p>}
                </div>
              )}
              <div className="sv-scope">
                <p className="sv-scope__title">Только для сотрудников</p>
                <div className="sv-scope__grid">
                  <AppMultiSelect
                    label="Офисы"
                    value={draft.office_ids}
                    options={(offices.state === 'ready' ? offices.data.items : [])
                      .map((one) => ({ value: one.id, label: one.name }))}
                    empty="Все офисы"
                    onChange={(next) => setDraft({ ...draft, office_ids: next })}
                  />
                  <AppMultiSelect
                    label="Отделы"
                    value={draft.department_ids}
                    options={(departments.state === 'ready' ? departments.data.items : [])
                      .map((one) => ({ value: one.id, label: one.name }))}
                    empty="Все отделы"
                    onChange={(next) => setDraft({ ...draft, department_ids: next })}
                  />
                  <AppMultiSelect
                    label="Должности"
                    value={draft.position_ids}
                    options={(positions.state === 'ready' ? positions.data.items : [])
                      .map((one) => ({ value: one.id, label: one.name }))}
                    empty="Все должности"
                    onChange={(next) => setDraft({ ...draft, position_ids: next })}
                  />
                </div>
                <small>Пусто — все, кто подошёл под событие. Офис, отдел и должность берутся на день события: людей переводят.</small>
              </div>
            </div>
          </div>

          <div className="sv-rs" id="rule-3">
            <span className="sv-rs__no">3</span>
            <div className="sv-rs__body">
              <h2 className="sv-rs__title">Настройте время отправки</h2>
              {!regular && !counted && (
                <div className="sv-seg2" role="group" aria-label="Когда отправить относительно события">
                  {([['same', 'В день события'], ['next', 'На следующий день'], ['days', 'Через N дней']] as const)
                    .map(([key, label]) => (
                      <button key={key} type="button" aria-pressed={shift === key}
                              className={shift === key ? 'sv-seg2__on' : undefined}
                              onClick={() => setShiftTo(key)}>
                        {label}
                      </button>
                    ))}
                </div>
              )}
              <div className="sv-fields sv-fields--row">
                {(counted || (!regular && shift === 'days')) && (
                  <label className="sv-field">
                    <span>{counted ? 'Через сколько дней после выхода' : 'Через сколько дней'}</span>
                    <input className="sv-input" type="number" min={counted ? 1 : 2} max={365}
                           value={draft.offset_days}
                           onChange={(event) => setDraft({ ...draft, offset_days: Number(event.target.value || 0) })} />
                  </label>
                )}
                <div className="sv-field">
                  <span>Время</span>
                  <AppSelectField label="Время отправки" value={time}
                                  onChange={(value) => {
                                    const [hour, minute] = value.split(':');
                                    setDraft({ ...draft, send_hour: Number(hour), send_minute: Number(minute) });
                                  }}>
                    {times.map((one) => <option key={one} value={one}>{one}</option>)}
                  </AppSelectField>
                </div>
              </div>
              {touched && problems['offset'] && <p className="sv-error" role="alert">{problems['offset']}</p>}
              <p className="sv-rs__note">Время организации: {zoneLine(zone)}</p>
            </div>
          </div>
          <Refusal text={saving.refusal} />
        </section>

        <aside className="sv-panel" aria-label="Как это будет работать">
          <h2 className="sv-panel__title">Как это будет работать</h2>
          <ol className="sv-flow">
            <li>
              <span className="sv-flow__mark"><AppIcon name={EVENT_STEP[draft.trigger_kind][1]} size={18} /></span>
              <span>
                <b>{EVENT_STEP[draft.trigger_kind][0]}</b>
                <small>{TRIGGER_ABOUT[draft.trigger_kind]}</small>
              </span>
            </li>
            <li>
              <span className="sv-flow__mark"><AppIcon name="send" size={18} /></span>
              <span>
                <b>Бот отправляет опрос</b>
                <small>
                  {whenLine({
                    trigger_kind: draft.trigger_kind,
                    offset_days: draft.offset_days,
                    send_hour: draft.send_hour,
                    send_minute: draft.send_minute,
                    repeat_months: regular ? Number(draft.repeat_months || 0) : null,
                  })}. Уволенным не уйдёт, по одному событию — ровно один раз.
                </small>
              </span>
            </li>
            <li>
              <span className="sv-flow__mark"><AppIcon name="chat" size={18} /></span>
              <span>
                <b>Сотрудник отвечает в Telegram</b>
                <small>Вопросы по одному, ответы сохраняются — можно продолжить позже.</small>
              </span>
            </li>
            <li>
              <span className="sv-flow__mark"><AppIcon name="chart" size={18} /></span>
              <span>
                <b>HR видит результат в CRM</b>
                <small>Каждое срабатывание — своя рассылка со своими ответами.</small>
              </span>
            </li>
          </ol>
        </aside>
      </div>

      <p className="sv-note">
        <AppIcon name="info" size={16} />
        {draft.trigger_kind === 'PROBATION_END'
          ? 'Если дату окончания стажировки изменят, отправка перенесётся сама: правило каждый день читает дату из карточки. Если стажировку закончат досрочно и уберут дату окончания — опрос не уйдёт. Без Telegram опрос не уйдёт, а в истории останется пропуск с причиной.'
          : 'Правило каждый день сверяется с карточками сотрудников: изменили дату — изменится и день отправки. Без Telegram опрос не уйдёт, а в истории останется пропуск с причиной.'}
      </p>

      <div className="sv-formfoot">
        <button type="button" className="sv-btn" onClick={onCancel}>Отмена</button>
        <button type="button" className="sv-btn sv-btn--main" disabled={saving.busy} onClick={submit}>
          {saving.busy ? 'Сохраняем…' : 'Сохранить автоматизацию'}
        </button>
      </div>
    </>
  );
}

// --- карточка правила ---------------------------------------------------------------

const RESULT: Record<api.SurveyRecipientStatus, [string, 'ok' | 'wait' | 'off' | 'bad']> = {
  PENDING: ['Ждёт отправки', 'off'],
  SENT: ['Ожидает ответа', 'wait'],
  STARTED: ['Ожидает ответа', 'wait'],
  COMPLETED: ['Завершил опрос', 'ok'],
  SKIPPED: ['Пропущено', 'off'],
  EXPIRED: ['Срок истёк', 'off'],
};

function dayOnly(value: string | null): string {
  if (!value) return '—';
  const [y, m, d] = value.split('-').map(Number);
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' })
    .format(new Date(Date.UTC(y ?? 2000, (m ?? 1) - 1, d ?? 1)));
}

function RuleCard({ id }: { id: string }) {
  const navigate = useNavigate();
  const session = useSession();
  const zone = session.status === 'authenticated' ? session.user.timezone : '';
  const [params, setParams] = useSearchParams();
  const tab = (params.get('tab') as 'overview' | 'history' | 'settings' | null) ?? 'overview';
  const setTab = (next: string) => setParams(next === 'overview' ? {} : { tab: next }, { replace: true });
  const [attempt, setAttempt] = useState(0);
  const [menu, setMenu] = useState(false);
  const [removing, setRemoving] = useState(false);
  const act = useSaving();

  const [rule, reloadRule] = useBlock(
    (signal) => api.surveyAutomation(id, signal), `rule|${id}|${attempt}`, true,
  );
  const [history] = useBlock(
    (signal) => api.surveyAutomationHistory(id, signal), `rule-history|${id}|${attempt}`, true,
  );
  const row = rule.state === 'ready' ? rule.data : null;
  const [template] = useBlock(
    (signal) => api.surveyTemplate(row?.template_id ?? '', signal),
    `rule-template|${row?.template_id}`, Boolean(row),
  );

  if (!row) {
    return (
      <SurveyFrame breadcrumb="Опросы / Автоматизация" crumbs={crumbs('Автоматизация')} title="Автоматизация">
        {rule.state === 'loading' ? <Loading /> : <Failed onRetry={reloadRule} />}
      </SurveyFrame>
    );
  }

  const tpl = template.state === 'ready' ? template.data : null;
  const items = history.state === 'ready' ? history.data.items : [];
  const stats = history.state === 'ready' ? history.data.stats : null;
  const scopeParts = [
    row.scope?.office_ids?.length ? `офисов: ${row.scope.office_ids.length}` : '',
    row.scope?.department_ids?.length ? `отделов: ${row.scope.department_ids.length}` : '',
    row.scope?.position_ids?.length ? `должностей: ${row.scope.position_ids.length}` : '',
  ].filter(Boolean);

  const toggle = (next: boolean) => {
    void act.run(() => api.toggleSurveyAutomation(row.id, next), () => setAttempt((n) => n + 1));
  };

  return (
    <SurveyFrame
      breadcrumb={`Опросы / ${row.title}`}
      crumbs={crumbs(row.title)}
      title={row.title}
      meta={(
        <p className="sv-status-line">
          <label className="sv-switch">
            <input type="checkbox" checked={row.is_active} disabled={act.busy}
                   aria-label="Автоматизация включена"
                   onChange={(event) => toggle(event.target.checked)} />
            <span className="sv-switch__track" aria-hidden="true" />
            <span className={row.is_active ? 'sv-on-word' : undefined}>
              {row.is_active ? 'Автоматизация включена' : 'Автоматизация выключена'}
            </span>
          </label>
          <span>{sentence(fromRule(row), row.template_title)}</span>
        </p>
      )}
      actions={(
        <>
          <button type="button" className="sv-btn" onClick={() => setTab('settings')}>
            <AppIcon name="pencil" size={16} /> Редактировать
          </button>
          <span className="sv-more">
            <button type="button" className="sv-btn sv-btn--icon" aria-label="Ещё действия"
                    aria-expanded={menu} onClick={() => setMenu((was) => !was)}>
              <AppIcon name="more" size={18} />
            </button>
            <AppPopover open={menu} onClose={() => setMenu(false)} className="sv-menu">
              <button type="button" onClick={() => { setMenu(false); toggle(!row.is_active); }}>
                {row.is_active ? 'Выключить' : 'Включить'}
              </button>
              <button type="button" className="sv-menu__bad"
                      onClick={() => { setMenu(false); setRemoving(true); }}>
                Удалить
              </button>
            </AppPopover>
          </span>
        </>
      )}
    >
      <Refusal text={act.refusal} />
      <div className="sv-tabs" role="tablist" aria-label="Разделы правила">
        {([['overview', 'Обзор'], ['history', 'История'], ['settings', 'Настройки']] as const).map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key}
                  className={tab === key ? 'sv-tab sv-tab--on' : 'sv-tab'}
                  onClick={() => setTab(key)}>
            {label}
            {key === 'history' && items.length > 0 && <span className="sv-tab__count">{items.length}</span>}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <div className="sv-cols">
          <div className="sv-side">
            <section className="sv-panel" aria-label="Правило отправки">
              <h2 className="sv-panel__title">Правило отправки</h2>
              <ul className="sv-rulefacts">
                <li>
                  <span className="sv-rulefacts__mark"><AppIcon name="doc" size={18} /></span>
                  <span className="sv-rulefacts__label">Шаблон</span>
                  <span>
                    <b>{row.template_title}{tpl ? ` · v${tpl.version}` : ''}</b>
                    {tpl && <small>{questionsLine(tpl.questions.length)}</small>}
                  </span>
                </li>
                <li>
                  <span className="sv-rulefacts__mark"><AppIcon name="calendar" size={18} /></span>
                  <span className="sv-rulefacts__label">Событие</span>
                  <span>
                    <b>{TRIGGER[row.trigger_kind]}</b>
                    <small>{TRIGGER_ABOUT[row.trigger_kind]}</small>
                  </span>
                </li>
                <li>
                  <span className="sv-rulefacts__mark"><AppIcon name="users" size={18} /></span>
                  <span className="sv-rulefacts__label">Кому</span>
                  <span>
                    <b>{scopeParts.length ? 'С ограничениями' : 'Все работающие'}</b>
                    <small>{scopeParts.length ? `Выбрано ${scopeParts.join(', ')}` : 'Кто подошёл под событие в этот день'}</small>
                  </span>
                </li>
                <li>
                  <span className="sv-rulefacts__mark"><AppIcon name="clock" size={18} /></span>
                  <span className="sv-rulefacts__label">Время</span>
                  <span>
                    <b>{whenLine(row)}</b>
                    <small>{zoneLine(zone)}</small>
                  </span>
                </li>
              </ul>
            </section>

            <section className="sv-panel" aria-label="Последние срабатывания">
              <h2 className="sv-panel__title">Последние срабатывания</h2>
              <HistoryTable rows={items.slice(0, 5)} zone={zone} loading={history.state === 'loading'} />
              {items.length > 5 && (
                <button type="button" className="sv-more-link" onClick={() => setTab('history')}>
                  Вся история ({items.length})
                </button>
              )}
            </section>
          </div>

          <aside className="sv-panel" aria-label="За последние 30 дней">
            <h2 className="sv-panel__title">За последние 30 дней</h2>
            <ul className="sv-tallies">
              <li><b>{stats?.fired ?? '—'}</b><span>Сработало</span></li>
              <li><b>{stats?.sent ?? '—'}</b><span>Опрос отправлен</span></li>
              <li><b>{stats?.completed ?? '—'}</b><span>Завершили</span></li>
              <li><b>{stats?.skipped ?? '—'}</b><span>Пропущено</span></li>
            </ul>
            <p className="sv-note sv-note--inner">
              <AppIcon name="info" size={16} />
              Перед отправкой система проверяет, что сотрудник работает, привязан
              к Telegram и ещё не получал опрос по этому же событию.
            </p>
          </aside>
        </div>
      )}

      {tab === 'history' && (
        <section className="sv-panel" aria-label="История срабатываний">
          <h2 className="sv-panel__title">История срабатываний</h2>
          <HistoryTable rows={items} zone={zone} loading={history.state === 'loading'} wide />
        </section>
      )}

      {tab === 'settings' && (
        <RuleForm current={row}
                  onCancel={() => setTab('overview')}
                  onSaved={() => { setAttempt((n) => n + 1); setTab('overview'); }} />
      )}

      {removing && (
        <Confirm
          title="Удалить автоматизацию"
          what={`Правило «${row.title}» перестанет существовать.`}
          consequence="Если по нему уже были рассылки, сервер откажет: их историю нельзя потерять вместе с правилом. Такое правило выключают."
          confirmLabel="Удалить"
          refusal={act.refusal}
          busy={act.busy}
          onCancel={() => setRemoving(false)}
          onConfirm={() => {
            void act.run(() => api.deleteSurveyAutomation(row.id), () => navigate('/surveys/automations'));
          }}
        />
      )}
    </SurveyFrame>
  );
}

function HistoryTable({ rows, zone, loading, wide }: {
  rows: api.SurveyAutomationHistory['items'];
  zone: string;
  loading: boolean;
  wide?: boolean;
}) {
  if (loading) return <Loading />;
  if (rows.length === 0) {
    return (
      <p className="sv-panel__about">
        Правило ещё не срабатывало. Когда наступит событие, здесь появится,
        кому опрос ушёл, а кому нет — и почему.
      </p>
    );
  }
  return (
    <ul className={wide ? 'sv-htable sv-htable--wide' : 'sv-htable'}>
      <li className="sv-htable__head" aria-hidden="true">
        <span>Сотрудник</span><span>Дата события</span><span>Отправка</span><span>Результат</span>
        {wide && <span>Причина</span>}
      </li>
      {rows.map((one) => {
        const [title, tone] = RESULT[one.status];
        return (
          <li key={one.id} className="sv-htable__row">
            <span className="sv-htable__who">{one.full_name}</span>
            <span>{dayOnly(one.event_day)}</span>
            <span>{one.status === 'SKIPPED' ? 'Не отправлено' : one.fired_at ? atMoment(one.fired_at, zone) : '—'}</span>
            <span>
              <State tone={tone}>{one.status === 'SKIPPED' && !wide && one.skip_reason ? one.skip_reason : title}</State>
            </span>
            {wide && <span className="sv-htable__why">{one.skip_reason ?? '—'}</span>}
          </li>
        );
      })}
    </ul>
  );
}
