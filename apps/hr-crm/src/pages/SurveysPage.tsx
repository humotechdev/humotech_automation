/**
 * Опросы сотрудников: рассылки и шаблоны.
 *
 * Шаблон — набор вопросов, который переиспользуют; рассылка — одно
 * обращение к названному кругу людей в назначенное время. Разделены не
 * для симметрии: правка шаблона не должна менять то, что уже спросили.
 *
 * Опрос ИМЕННОЙ, и страница этого не прячет. Рядом с каждым ответом
 * стоит фамилия, офис и отдел — HR идёт по ним разговаривать с
 * человеком. Сводка по вопросам здесь тоже есть, но она ответы не
 * заменяет и анонимности не добавляет: считать «в среднем по офису» и
 * при этом видеть имена — разные способы читать одни и те же данные.
 */

import { useCallback, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { AppSelectField } from '../components/AppSelect';
import {
  Confirm,
  Empty,
  Failed,
  Field,
  Loading,
  Refusal,
  SidePanel,
  useSaving,
} from '../components/admin/Parts';
import { useBlock } from '../features/dashboard/data';
import { useSession } from '../features/auth/session';
import { moment } from '../features/time/zone';
import { QuestionEditor, type QuestionDraft, blankQuestion } from
  '../components/surveys/QuestionEditor';
import { AudiencePicker } from '../components/surveys/AudiencePicker';
import '../styles/admin.css';
import '../styles/surveys.css';

type Tab = 'campaigns' | 'templates';

const STATUS: Record<string, string> = {
  DRAFT: 'Черновик',
  SCHEDULED: 'Запланирована',
  ACTIVE: 'Отправлена',
  FINISHED: 'Завершена',
  CANCELLED: 'Отменена',
};

const AUDIENCE: Record<string, string> = {
  EMPLOYEES: 'Выбранные сотрудники',
  DEPARTMENT: 'Отдел',
  OFFICE: 'Офис',
  ALL: 'Все действующие',
};

export function SurveysPage() {
  const session = useSession();
  const zone = session.status === 'authenticated' ? session.user.timezone : '';
  const navigate = useNavigate();

  const [params, setParams] = useSearchParams();
  const tab = (params.get('tab') as Tab) || 'campaigns';

  const patch = useCallback(
    (next: Record<string, string | null>) => {
      setParams(
        (was) => {
          const copy = new URLSearchParams(was);
          for (const [key, value] of Object.entries(next)) {
            if (!value) copy.delete(key);
            else copy.set(key, value);
          }
          return copy;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const [attempt, setAttempt] = useState(0);
  const [newCampaign, setNewCampaign] = useState(false);
  const [templateDraft, setTemplateDraft] = useState<TemplateDraft | null>(null);
  const [archiving, setArchiving] = useState<api.SurveyTemplate | null>(null);
  const archive = useSaving();

  // Обе загрузки идут всегда, а не по вкладке: число на вкладке —
  // утверждение, и «0 рассылок» на вкладке шаблонов было бы неправдой.
  // Списки короткие, второй запрос тут ничего не стоит.
  const [campaigns, reloadCampaigns] = useBlock(
    (signal) => api.surveyCampaigns({}, signal),
    `surveys-campaigns|${attempt}`,
    true,
  );
  const [templates, reloadTemplates] = useBlock(
    (signal) => api.surveyTemplates({}, signal),
    `surveys-templates|${attempt}`,
    true,
  );

  const done = useCallback(() => {
    setNewCampaign(false);
    setTemplateDraft(null);
    setArchiving(null);
    setAttempt((n) => n + 1);
    reloadCampaigns();
    reloadTemplates();
  }, [reloadCampaigns, reloadTemplates]);

  const templateRows = templates.state === 'ready' ? templates.data.items : [];
  const campaignRows = campaigns.state === 'ready' ? campaigns.data.items : [];
  const open = newCampaign || templateDraft !== null;

  return (
    <AppShell breadcrumb="Опросы" section="surveys">
      <header className="head head--tight">
        <div>
          <h1 className="head__title">Опросы</h1>
          <p className="head__sub">
            Короткие именные опросы: сотрудник отвечает в Telegram, ответы
            приходят сюда вместе с его именем
          </p>
        </div>
        <div className="head__actions">
          {tab === 'campaigns' ? (
            <button type="button" className="btn btn--primary"
                    disabled={templateRows.length === 0}
                    onClick={() => { setTemplateDraft(null); setNewCampaign(true); }}>
              <AppIcon name="send" size={16} /> Создать рассылку
            </button>
          ) : (
            <button type="button" className="btn btn--primary"
                    onClick={() => { setNewCampaign(false); setTemplateDraft(BLANK()); }}>
              <AppIcon name="plus" size={16} /> Новый шаблон
            </button>
          )}
        </div>
      </header>

      <div className="tabs tabs--top" role="tablist" aria-label="Разделы опросов">
        {([
          ['campaigns', 'Рассылки', campaignRows.length],
          ['templates', 'Шаблоны', templateRows.length],
        ] as Array<[Tab, string, number]>).map(([key, title, count]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key}
                  className={tab === key ? 'tab tab--on' : 'tab'}
                  onClick={() => patch({ tab: key === 'campaigns' ? null : key })}>
            {title}
            <span className="tab__count">{count}</span>
          </button>
        ))}
      </div>

      <div className={open ? 'split split--open' : 'split'}>
        <section className="panel panel--list" aria-label="Опросы">
          {tab === 'campaigns' && (
            <>
              {campaigns.state === 'loading' && <Loading />}
              {campaigns.state === 'error' && <Failed onRetry={reloadCampaigns} />}
              {campaigns.state === 'ready' && campaignRows.length === 0 && (
                <Empty
                  filtered={false}
                  nothing=""
                  none={
                    templateRows.length === 0
                      ? 'Рассылок пока нет. Сначала нужен шаблон — набор вопросов, который вы отправите сотрудникам.'
                      : 'Рассылок пока нет. Выберите шаблон и круг получателей — сотрудникам придёт одно сообщение с кнопкой.'
                  }
                  action={
                    templateRows.length === 0 ? (
                      <button type="button" className="btn btn--primary"
                              onClick={() => { patch({ tab: 'templates' }); setTemplateDraft(BLANK()); }}>
                        Создать шаблон
                      </button>
                    ) : (
                      <button type="button" className="btn btn--primary"
                              onClick={() => setNewCampaign(true)}>
                        Создать рассылку
                      </button>
                    )
                  }
                />
              )}
              {campaigns.state === 'ready' && campaignRows.length > 0 && (
                <div className="scroller">
                  <table className="grid-table table-cards" aria-label="Рассылки опросов">
                    <thead>
                      <tr>
                        <th scope="col">Опрос</th>
                        <th scope="col">Кому</th>
                        <th scope="col">Прошли</th>
                        <th scope="col">Состояние</th>
                        <th scope="col"><span className="visually-hidden">Открыть</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {campaignRows.map((row) => (
                        <tr key={row.id} className="row">
                          <td>
                            <button type="button" className="adm-name"
                                    onClick={() => navigate(`/surveys/${row.id}`)}>
                              <span className="adm-name__title">{row.title}</span>
                              <span className="adm-name__sub">
                                {row.sent_at
                                  ? `отправлена ${moment(row.sent_at, zone, false)}`
                                  : row.scheduled_at
                                    ? `запланирована на ${moment(row.scheduled_at, zone, false)}`
                                    : 'ещё не отправлена'}
                                {row.repeat_months
                                  ? ` · повтор раз в ${row.repeat_months} мес.`
                                  : ''}
                              </span>
                            </button>
                          </td>
                          <td data-label="Кому">{AUDIENCE[row.audience_kind]}</td>
                          <td data-label="Прошли">
                            {/* Пока не отправлено — прочерк: ноль здесь
                                означал бы, что никто не ответил. */}
                            {row.total === undefined || row.total === 0
                              ? '—'
                              : `${row.done ?? 0} из ${row.total}`}
                          </td>
                          <td data-label="Состояние">
                            <span className="state">
                              <i className="state__dot" />
                              {STATUS[row.status] ?? row.status}
                            </span>
                          </td>
                          <td className="num">
                            <button type="button" className="tool tool--ghost"
                                    aria-label={`Открыть ${row.title}`}
                                    onClick={() => navigate(`/surveys/${row.id}`)}>
                              <AppIcon name="arrow" size={16} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}

          {tab === 'templates' && (
            <>
              {templates.state === 'loading' && <Loading />}
              {templates.state === 'error' && <Failed onRetry={reloadTemplates} />}
              {templates.state === 'ready' && templateRows.length === 0 && (
                <Empty
                  filtered={false}
                  nothing=""
                  none="Шаблонов пока нет. Шаблон — это набор вопросов: его сохраняют один раз и рассылают сколько угодно."
                  action={
                    <button type="button" className="btn btn--primary"
                            onClick={() => setTemplateDraft(BLANK())}>
                      Создать шаблон
                    </button>
                  }
                />
              )}
              {templates.state === 'ready' && templateRows.length > 0 && (
                <div className="scroller">
                  <table className="grid-table table-cards" aria-label="Шаблоны опросов">
                    <thead>
                      <tr>
                        <th scope="col">Шаблон</th>
                        <th scope="col">Вопросов</th>
                        <th scope="col"><span className="visually-hidden">Действия</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {templateRows.map((row) => (
                        <tr key={row.id} className="row">
                          <td>
                            <button type="button" className="adm-name"
                                    onClick={() => setTemplateDraft(fromTemplate(row))}>
                              <span className="adm-name__title">{row.title}</span>
                              {row.description && (
                                <span className="adm-name__sub">{row.description}</span>
                              )}
                            </button>
                          </td>
                          <td data-label="Вопросов">{row.questions.length}</td>
                          <td className="num adm-actions">
                            <button type="button" className="btn btn--small"
                                    onClick={() => {
                                      void archive.run(
                                        () => api.copySurveyTemplate(row.id),
                                        done,
                                      );
                                    }}>
                              Копия
                            </button>
                            <button type="button" className="tool tool--ghost"
                                    aria-label={`Архивировать шаблон ${row.title}`}
                                    onClick={() => setArchiving(row)}>
                              <AppIcon name="archive" size={16} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <Refusal text={archive.refusal} />
            </>
          )}
        </section>

        {newCampaign && (
          <SidePanel title="Новая рассылка" onClose={() => setNewCampaign(false)}>
            <CampaignForm templates={templateRows} onSaved={done} />
          </SidePanel>
        )}

        {templateDraft && (
          <SidePanel
            title={templateDraft.id ? 'Шаблон опроса' : 'Новый шаблон'}
            onClose={() => setTemplateDraft(null)}
          >
            <TemplateForm draft={templateDraft} onChange={setTemplateDraft}
                          onSaved={done} />
          </SidePanel>
        )}
      </div>

      {archiving && (
        <Confirm
          title="Архивировать шаблон"
          what={`«${archiving.title}» пропадёт из списка при создании рассылки.`}
          consequence="Уже отправленные рассылки и полученные ответы останутся: архив не стирает историю."
          confirmLabel="Архивировать"
          refusal={archive.refusal}
          busy={archive.busy}
          onCancel={() => setArchiving(null)}
          onConfirm={() => {
            void archive.run(() => api.archiveSurveyTemplate(archiving.id), done);
          }}
        />
      )}
    </AppShell>
  );
}

// --- шаблон ------------------------------------------------------------------

type TemplateDraft = {
  id: string | null;
  title: string;
  description: string;
  questions: QuestionDraft[];
};

const BLANK = (): TemplateDraft => ({
  id: null,
  title: '',
  description: '',
  questions: [blankQuestion()],
});

function fromTemplate(row: api.SurveyTemplate): TemplateDraft {
  return {
    id: row.id,
    title: row.title,
    description: row.description ?? '',
    questions: row.questions.map((one) => ({
      text: one.text,
      kind: one.kind,
      is_required: one.is_required,
      options: one.options ?? [],
    })),
  };
}

function TemplateForm({ draft, onChange, onSaved }: {
  draft: TemplateDraft;
  onChange: (draft: TemplateDraft) => void;
  onSaved: () => void;
}) {
  const [touched, setTouched] = useState(false);
  const saving = useSaving();

  const problems = useMemo(() => {
    const found: Record<string, string> = {};
    if (!draft.title.trim()) found['title'] = 'Название обязательно';
    if (draft.questions.length === 0) found['questions'] = 'Добавьте хотя бы один вопрос';
    for (const [index, question] of draft.questions.entries()) {
      if (!question.text.trim()) {
        found['questions'] = `Вопрос ${index + 1}: не хватает текста`;
        break;
      }
      if (
        (question.kind === 'SINGLE' || question.kind === 'MULTI')
        && question.options.filter((one) => one.trim()).length < 2
      ) {
        found['questions'] =
          `Вопрос ${index + 1}: у вопроса с вариантами их должно быть хотя бы два`;
        break;
      }
    }
    return found;
  }, [draft]);

  const submit = () => {
    setTouched(true);
    if (Object.keys(problems).length > 0) return;
    const questions = draft.questions.map((one) => ({
      text: one.text.trim(),
      kind: one.kind,
      is_required: one.is_required,
      ...(one.kind === 'SINGLE' || one.kind === 'MULTI'
        ? { options: one.options.map((two) => two.trim()).filter(Boolean) }
        : {}),
    }));
    void saving.run(
      () =>
        draft.id
          ? api.updateSurveyTemplate(draft.id, {
              title: draft.title.trim(),
              description: draft.description.trim(),
              questions,
            })
          : api.createSurveyTemplate({
              title: draft.title.trim(),
              ...(draft.description.trim()
                ? { description: draft.description.trim() }
                : {}),
              questions,
            }),
      onSaved,
    );
  };

  return (
    <form className="adm-form"
          onSubmit={(event) => { event.preventDefault(); submit(); }}>
      <Field label="Название опроса" error={touched ? problems['title'] : undefined}
             hint="Его увидит сотрудник в сообщении бота">
        <input className="input" value={draft.title} maxLength={255}
               placeholder="Как вам работается"
               onChange={(event) => onChange({ ...draft, title: event.target.value })} />
      </Field>

      <Field label="Описание" hint="Необязательно: одна фраза о том, зачем опрос">
        <textarea className="input input--area" rows={2} value={draft.description}
                  onChange={(event) =>
                    onChange({ ...draft, description: event.target.value })} />
      </Field>

      <QuestionEditor
        questions={draft.questions}
        error={touched ? problems['questions'] : undefined}
        onChange={(questions) => onChange({ ...draft, questions })}
      />

      <Refusal text={saving.refusal} />

      <div className="adm-form__tools">
        <button type="submit" className="btn btn--primary" disabled={saving.busy}>
          {saving.busy ? 'Сохраняем…' : draft.id ? 'Сохранить' : 'Создать шаблон'}
        </button>
      </div>
      {draft.id && (
        <p className="adm-note">
          Если на вопросы уже отвечали, изменить их нельзя — сервер откажет.
          Сделайте копию шаблона: прежние ответы должны остаться ответами на
          прежние вопросы.
        </p>
      )}
    </form>
  );
}

// --- рассылка ----------------------------------------------------------------

function CampaignForm({ templates, onSaved }: {
  templates: api.SurveyTemplate[];
  onSaved: () => void;
}) {
  const [templateId, setTemplateId] = useState(templates[0]?.id ?? '');
  const [audienceKind, setAudienceKind] =
    useState<api.SurveyAudienceKind>('ALL');
  const [audience, setAudience] = useState<string[]>([]);
  const [when, setWhen] = useState<'now' | 'later'>('now');
  const [at, setAt] = useState('');
  const [repeat, setRepeat] = useState('');
  const [touched, setTouched] = useState(false);
  const saving = useSaving();

  const chosen = templates.find((one) => one.id === templateId);

  const problems = useMemo(() => {
    const found: Record<string, string> = {};
    if (!templateId) found['template'] = 'Выберите шаблон';
    if (audienceKind !== 'ALL' && audience.length === 0) {
      found['audience'] = 'Выберите хотя бы одного получателя';
    }
    if (when === 'later') {
      if (!at) found['at'] = 'Укажите дату и время';
      else if (new Date(at).getTime() < Date.now()) {
        found['at'] = 'Дата в прошлом: отправить задним числом нельзя';
      }
    }
    return found;
  }, [templateId, audienceKind, audience, when, at]);

  const submit = () => {
    setTouched(true);
    if (Object.keys(problems).length > 0) return;
    void saving.run(
      () =>
        api.createSurveyCampaign({
          template_id: templateId,
          audience_kind: audienceKind,
          ...(audienceKind === 'ALL' ? {} : { audience_ids: audience }),
          ...(when === 'later' ? { scheduled_at: new Date(at).toISOString() } : {}),
          ...(repeat ? { repeat_months: Number(repeat) } : {}),
          send_now: when === 'now',
        }),
      onSaved,
    );
  };

  return (
    <form className="adm-form"
          onSubmit={(event) => { event.preventDefault(); submit(); }}>
      <Field label="Шаблон опроса" error={touched ? problems['template'] : undefined}>
        <AppSelectField label="Шаблон опроса" value={templateId}
                        onChange={setTemplateId}>
          <option value="">Выберите шаблон</option>
          {templates.map((one) => (
            <option key={one.id} value={one.id}>{one.title}</option>
          ))}
        </AppSelectField>
      </Field>

      {chosen && (
        <p className="adm-note">
          {chosen.questions.length}{' '}
          {plural(chosen.questions.length, ['вопрос', 'вопроса', 'вопросов'])}.
          Сотруднику придёт одно сообщение с кнопкой — вопросы покажет Mini App.
        </p>
      )}

      <AudiencePicker
        kind={audienceKind}
        ids={audience}
        error={touched ? problems['audience'] : undefined}
        onChange={(kind, ids) => { setAudienceKind(kind); setAudience(ids); }}
      />

      <Field label="Когда отправить">
        <AppSelectField label="Когда отправить" value={when}
                        onChange={(value) => setWhen(value as 'now' | 'later')}>
          <option value="now">Отправить сейчас</option>
          <option value="later">Запланировать</option>
        </AppSelectField>
      </Field>

      {when === 'later' && (
        <Field label="Дата и время" error={touched ? problems['at'] : undefined}
               hint="Круг получателей считается в момент отправки: те, кто придёт до этой даты, тоже получат опрос">
          <input type="datetime-local" className="input" value={at}
                 onChange={(event) => setAt(event.target.value)} />
        </Field>
      )}

      <Field label="Повторять"
             hint="Каждый повтор — своя рассылка со своими ответами: месяцы не смешиваются">
        <AppSelectField label="Повторять" value={repeat} onChange={setRepeat}>
          <option value="">Не повторять</option>
          <option value="2">Раз в 2 месяца</option>
          <option value="3">Раз в 3 месяца</option>
        </AppSelectField>
      </Field>

      <Refusal text={saving.refusal} />

      <div className="adm-form__tools">
        <button type="submit" className="btn btn--primary" disabled={saving.busy}>
          {saving.busy
            ? 'Отправляем…'
            : when === 'now' ? 'Отправить опрос' : 'Запланировать'}
        </button>
      </div>
    </form>
  );
}

/** «1 вопрос», «2 вопроса», «5 вопросов». */
export function plural(value: number, forms: [string, string, string]): string {
  const tail = value % 100;
  if (tail >= 11 && tail <= 14) return forms[2];
  switch (value % 10) {
    case 1:
      return forms[0];
    case 2:
    case 3:
    case 4:
      return forms[1];
    default:
      return forms[2];
  }
}
