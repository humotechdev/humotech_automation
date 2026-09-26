/**
 * Шаблон опроса: конструктор черновика и просмотр опубликованного.
 *
 * Два режима одной страницы, а не одна форма с выключенными полями.
 * Черновик собирают: вопросы разворачиваются по одному, рядом — как
 * вопрос придёт в Telegram и чего не хватает до публикации. Опубликованный
 * читают: по нему уже спрашивали людей, и переписанный вопрос сделал бы
 * прежние ответы ответами на другой вопрос. Его правят новой версией, а
 * страница показывает, где он используется.
 *
 * Вопросы в конструкторе свёрнуты все, кроме того, над которым работают:
 * семь развёрнутых карточек — это полтора экрана прокрутки ради одного
 * поля.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import { AppPopover, AppSelectField } from '../components/AppSelect';
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
  MAX_QUESTIONS,
  QUESTION_KINDS,
  State,
  SurveyFrame,
  TEMPLATE_STATUS,
  TelegramPreview,
  atMoment,
  blankQuestion,
  peopleLine,
  plural,
  type QuestionDraft,
} from '../features/surveys/model';
import '../styles/admin.css';
import '../styles/surveys.css';

type Draft = {
  title: string;
  description: string;
  questions: QuestionDraft[];
};

function fromTemplate(row: api.SurveyTemplate): Draft {
  return {
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

const KIND_ICON: Record<api.SurveyQuestionKind, AppIconName> = {
  TEXT: 'list',
  SCALE: 'report',
  SINGLE: 'check',
  MULTI: 'check',
};

const KIND_SHORT: Record<api.SurveyQuestionKind, string> = {
  TEXT: 'Развёрнутый ответ',
  SCALE: 'Шкала 1–5',
  SINGLE: 'Один вариант',
  MULTI: 'Несколько вариантов',
};

/** «18 сентября 2026» — день публикации без времени. */
function dayLong(at: string, zone: string): string {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric', month: 'long', year: 'numeric',
    ...(zone ? { timeZone: zone } : {}),
  }).format(date).replace(/\s?г\.$/, '');
}

function crumbs(title: string): Array<[string, string?]> {
  return [
    ['Рабочее пространство', '/'],
    ['Опросы', '/surveys'],
    ['Шаблоны', '/surveys'],
    [title],
  ];
}

export function SurveyTemplatePage() {
  const { id } = useParams();
  const fresh = !id || id === 'new';
  const [attempt, setAttempt] = useState(0);
  const [template, reload] = useBlock(
    (signal) => api.surveyTemplate(id ?? '', signal),
    `survey-template|${id}|${attempt}`,
    !fresh,
  );
  const again = useCallback(() => setAttempt((n) => n + 1), []);

  if (!fresh && template.state !== 'ready') {
    return (
      <SurveyFrame breadcrumb="Опросы / Шаблон" crumbs={crumbs('Шаблон')} title="Шаблон"
                   back={['К шаблонам', '/surveys']}>
        {template.state === 'loading' ? <Loading /> : <Failed onRetry={reload} />}
      </SurveyFrame>
    );
  }

  const row = template.state === 'ready' ? template.data : null;
  // Опубликованный и архивный читают, а не правят: по ним уже
  // спрашивали, или они убраны из работы.
  if (row && (row.status === 'PUBLISHED' || row.archived_at)) {
    return <TemplateView row={row} onChanged={again} />;
  }
  return <TemplateEditor row={row} onSaved={again} />;
}

// --- конструктор ----------------------------------------------------------------

function TemplateEditor({ row, onSaved }: {
  row: api.SurveyTemplate | null;
  onSaved: () => void;
}) {
  const navigate = useNavigate();
  const fresh = row === null;
  const [draft, setDraft] = useState<Draft>(
    row ? fromTemplate(row) : { title: '', description: '', questions: [blankQuestion()] },
  );
  const [loaded, setLoaded] = useState<string | null>(row?.id ?? null);
  const [tab, setTab] = useState<'build' | 'settings' | 'preview'>('build');
  const [open, setOpen] = useState<number | null>(0);
  const [touched, setTouched] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [dragging, setDragging] = useState<number | null>(null);
  const save = useSaving();

  // Черновик набирается из пришедшего шаблона один раз: перезаписывать
  // его на каждой перерисовке значило бы стирать правки кадровика.
  useEffect(() => {
    if (row && loaded !== row.id) {
      setDraft(fromTemplate(row));
      setLoaded(row.id);
    }
  }, [loaded, row]);

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

  const payload = () =>
    draft.questions.map((one) => ({
      text: one.text.trim(),
      kind: one.kind,
      is_required: one.is_required,
      ...(one.kind === 'SINGLE' || one.kind === 'MULTI'
        ? { options: one.options.map((two) => two.trim()).filter(Boolean) }
        : {}),
    }));

  /** Сохранить черновик; вернуть сохранённую строку. */
  const persist = async (): Promise<api.SurveyTemplate> =>
    fresh
      ? api.createSurveyTemplate({
          title: draft.title.trim(),
          ...(draft.description.trim() ? { description: draft.description.trim() } : {}),
          questions: payload(),
        })
      : api.updateSurveyTemplate(row.id, {
          title: draft.title.trim(),
          description: draft.description.trim(),
          questions: payload(),
        });

  const submit = () => {
    setTouched(true);
    if (Object.keys(problems).length > 0) return;
    let made: api.SurveyTemplate | null = null;
    void save.run(
      async () => { made = await persist(); },
      () => {
        // Новый шаблон открывается по своей ссылке: иначе «Назад»
        // вернул бы на пустую форму создания.
        if (fresh && made) {
          navigate(`/surveys/templates/${(made as api.SurveyTemplate).id}`, { replace: true });
        } else {
          onSaved();
        }
      },
    );
  };

  const publish = () => {
    let made: api.SurveyTemplate | null = null;
    void save.run(
      async () => {
        // Публикуется то, что на экране, а не последняя сохранённая
        // версия: иначе правки последних минут ушли бы в никуда.
        made = await persist();
        made = await api.publishSurveyTemplate((made as api.SurveyTemplate).id);
      },
      () => {
        setPublishing(false);
        if (made) navigate(`/surveys/templates/${(made as api.SurveyTemplate).id}`, { replace: true });
        onSaved();
      },
    );
  };

  const setAt = (index: number, next: Partial<QuestionDraft>) =>
    setDraft({
      ...draft,
      questions: draft.questions.map((one, at) => (at === index ? { ...one, ...next } : one)),
    });

  const add = () => {
    setDraft({ ...draft, questions: [...draft.questions, blankQuestion()] });
    setOpen(draft.questions.length);
  };

  const duplicate = (index: number) => {
    const copy = [...draft.questions];
    const one = copy[index] as QuestionDraft;
    copy.splice(index + 1, 0, { ...one, options: [...one.options] });
    setDraft({ ...draft, questions: copy });
    setOpen(index + 1);
  };

  const drop = (index: number) => {
    setDraft({ ...draft, questions: draft.questions.filter((_, at) => at !== index) });
    setOpen(null);
  };

  const move = (from: number, to: number) => {
    if (from === to) return;
    const copy = [...draft.questions];
    const [one] = copy.splice(from, 1);
    copy.splice(to, 0, one as QuestionDraft);
    setDraft({ ...draft, questions: copy });
    setOpen(to);
  };

  const shownIndex = open ?? 0;
  const focused = draft.questions[shownIndex] ?? draft.questions[0] ?? blankQuestion();
  const title = fresh ? 'Новый шаблон' : (row.title || 'Шаблон');
  const withText = draft.questions.every((one) => one.text.trim());

  return (
    <SurveyFrame
      breadcrumb={`Опросы / ${title}`}
      crumbs={crumbs(title)}
      back={['К шаблонам', '/surveys']}
      title={title}
      {...(row ? {
        meta: (
          <p className="sv-status-line">
            <State tone="off">{TEMPLATE_STATUS.DRAFT}</State>
            <i aria-hidden="true" />
            <span>Версия {row.version} · черновик</span>
          </p>
        ),
      } : {})}
      actions={(
        <>
          <button type="button" className="sv-btn" disabled={save.busy} onClick={submit}>
            {save.busy && !publishing ? 'Сохраняем…' : 'Сохранить черновик'}
          </button>
          <button type="button" className="sv-btn sv-btn--main"
                  disabled={save.busy}
                  onClick={() => {
                    setTouched(true);
                    if (Object.keys(problems).length === 0) setPublishing(true);
                  }}>
            Опубликовать
          </button>
        </>
      )}
    >
      <div className="sv-tabs" role="tablist" aria-label="Разделы шаблона">
        {([['build', 'Конструктор'], ['settings', 'Настройки'], ['preview', 'Предпросмотр']] as const)
          .map(([key, label]) => (
            <button key={key} type="button" role="tab" aria-selected={tab === key}
                    className={tab === key ? 'sv-tab sv-tab--on' : 'sv-tab'}
                    onClick={() => setTab(key)}>
              {label}
            </button>
          ))}
      </div>

      <div className="sv-cols">
        <section className="sv-panel" aria-label="Шаблон">
          {(tab === 'build' || tab === 'settings') && (
            <div className="sv-fields">
              <label className="sv-field">
                <span>Название шаблона</span>
                <input className="sv-input" value={draft.title} maxLength={255}
                       placeholder="Например: Итоги стажировки"
                       onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
                {touched && problems['title'] && (
                  <em role="alert">{problems['title']}</em>
                )}
              </label>
              <label className="sv-field">
                <span>Описание</span>
                <textarea className="sv-input sv-input--area" rows={2}
                          value={draft.description}
                          placeholder="Одна фраза о том, зачем опрос"
                          onChange={(event) =>
                            setDraft({ ...draft, description: event.target.value })} />
              </label>
              {tab === 'settings' && (
                <p className="sv-note sv-note--inner">
                  <AppIcon name="info" size={16} />
                  Название увидит сотрудник в сообщении бота, описание — HR в
                  списке шаблонов и при выборе шаблона для рассылки.
                </p>
              )}
            </div>
          )}

          {tab === 'build' && (
            <>
              <div className="sv-panel__head sv-panel__head--line">
                <h2 className="sv-panel__title">Вопросы</h2>
                <span className="sv-panel__count">
                  {draft.questions.length} из {MAX_QUESTIONS}
                </span>
              </div>

              <ol className="sv-qlist">
                {draft.questions.map((question, index) => {
                  const expanded = open === index;
                  const withOptions = question.kind === 'SINGLE' || question.kind === 'MULTI';
                  return (
                    <li key={index}
                        className={[
                          'sv-qi',
                          expanded ? 'sv-qi--open' : '',
                          dragging === index ? 'sv-qi--drag' : '',
                        ].filter(Boolean).join(' ')}
                        onDragOver={(event) => {
                          if (dragging !== null) event.preventDefault();
                        }}
                        onDrop={(event) => {
                          event.preventDefault();
                          if (dragging !== null) move(dragging, index);
                          setDragging(null);
                        }}>
                      <div className="sv-qi__head">
                        <span className="sv-qi__no">{index + 1}</span>
                        {/* Порядок меняют перетаскиванием за ручку: опрос
                            читается подряд, и порядок вопросов — часть
                            самого опроса. */}
                        <span className="sv-qi__grip" draggable
                              aria-label={`Перетащить вопрос ${index + 1}`}
                              onDragStart={(event) => {
                                setDragging(index);
                                event.dataTransfer.effectAllowed = 'move';
                              }}
                              onDragEnd={() => setDragging(null)}>
                          <AppIcon name="grid" size={16} />
                        </span>
                        {expanded ? (
                          <input className="sv-input sv-qi__text" value={question.text}
                                 maxLength={500} autoFocus={question.text === ''}
                                 aria-label={`Текст вопроса ${index + 1}`}
                                 placeholder="Например: насколько понятными были ваши задачи?"
                                 onChange={(event) => setAt(index, { text: event.target.value })} />
                        ) : (
                          <button type="button" className="sv-qi__title"
                                  onClick={() => setOpen(index)}>
                            {question.text.trim() || 'Новый вопрос'}
                          </button>
                        )}
                        {!expanded && (
                          <>
                            <span className="sv-qi__kind">{KIND_SHORT[question.kind]}</span>
                            <label className="sv-switch sv-switch--small">
                              <input type="checkbox" checked={question.is_required}
                                     aria-label={`Обязательный вопрос ${index + 1}`}
                                     onChange={(event) =>
                                       setAt(index, { is_required: event.target.checked })} />
                              <span className="sv-switch__track" aria-hidden="true" />
                              <span>Обязательный</span>
                            </label>
                          </>
                        )}
                        <button type="button" className="tool tool--ghost sv-qi__fold"
                                aria-expanded={expanded}
                                aria-label={expanded
                                  ? `Свернуть вопрос ${index + 1}`
                                  : `Развернуть вопрос ${index + 1}`}
                                onClick={() => setOpen(expanded ? null : index)}>
                          <AppIcon name="chevron" size={18} />
                        </button>
                      </div>

                      {expanded && (
                        <div className="sv-qi__body">
                          <div className="sv-qi__row">
                            <AppSelectField label={`Тип вопроса ${index + 1}`}
                                            value={question.kind} className="sv-qi__type"
                                            onChange={(value) =>
                                              setAt(index, {
                                                kind: value as QuestionDraft['kind'],
                                                // Варианты бывают только у выбора.
                                                // Оставить их у шкалы — хранить то,
                                                // чего человек не увидит.
                                                options: value === 'SINGLE' || value === 'MULTI'
                                                  ? (question.options.length ? question.options : ['', ''])
                                                  : [],
                                              })}>
                              {QUESTION_KINDS.map(([key, label]) => (
                                <option key={key} value={key}>{label}</option>
                              ))}
                            </AppSelectField>
                            <label className="sv-switch">
                              <input type="checkbox" checked={question.is_required}
                                     aria-label={`Обязательный вопрос ${index + 1}`}
                                     onChange={(event) =>
                                       setAt(index, { is_required: event.target.checked })} />
                              <span className="sv-switch__track" aria-hidden="true" />
                              <span>Обязательный вопрос</span>
                            </label>
                          </div>

                          {question.kind === 'TEXT' && (
                            <p className="sv-qi__sample">Сотрудник напишет ответ своими словами…</p>
                          )}
                          {question.kind === 'SCALE' && (
                            <div className="sv-qi__scale" aria-hidden="true">
                              {[1, 2, 3, 4, 5].map((one) => <span key={one}>{one}</span>)}
                              <small>Совсем нет — полностью да</small>
                            </div>
                          )}
                          {withOptions && (
                            <ul className="sv-options">
                              {question.options.map((option, at) => (
                                <li key={at} className="sv-option">
                                  <span className={question.kind === 'SINGLE'
                                    ? 'sv-option__mark' : 'sv-option__mark sv-option__mark--box'}
                                        aria-hidden="true" />
                                  <input className="sv-input" value={option} maxLength={200}
                                         aria-label={`Вариант ${at + 1} вопроса ${index + 1}`}
                                         placeholder={`Вариант ${at + 1}`}
                                         onChange={(event) =>
                                           setAt(index, {
                                             options: question.options.map((one, two) =>
                                               two === at ? event.target.value : one),
                                           })} />
                                  <button type="button" className="tool tool--ghost"
                                          aria-label={`Удалить вариант ${at + 1} вопроса ${index + 1}`}
                                          disabled={question.options.length <= 2}
                                          onClick={() =>
                                            setAt(index, {
                                              options: question.options.filter((_, two) => two !== at),
                                            })}>
                                    <AppIcon name="close" size={16} />
                                  </button>
                                </li>
                              ))}
                              <li>
                                <button type="button" className="sv-link"
                                        onClick={() =>
                                          setAt(index, { options: [...question.options, ''] })}>
                                  <AppIcon name="plus" size={16} /> Добавить вариант
                                </button>
                              </li>
                            </ul>
                          )}

                          <div className="sv-qi__foot">
                            <button type="button" className="tool tool--ghost"
                                    aria-label={`Дублировать вопрос ${index + 1}`}
                                    disabled={draft.questions.length >= MAX_QUESTIONS}
                                    onClick={() => duplicate(index)}>
                              <AppIcon name="doc" size={18} />
                            </button>
                            <button type="button" className="tool tool--ghost"
                                    aria-label={`Удалить вопрос ${index + 1}`}
                                    disabled={draft.questions.length <= 1}
                                    onClick={() => drop(index)}>
                              <AppIcon name="trash" size={18} />
                            </button>
                          </div>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ol>

              {touched && problems['questions'] && (
                <p className="sv-error" role="alert">{problems['questions']}</p>
              )}

              <button type="button" className="sv-btn sv-btn--add"
                      disabled={draft.questions.length >= MAX_QUESTIONS}
                      onClick={add}>
                <AppIcon name="plus" size={16} /> Добавить вопрос
              </button>
            </>
          )}

          {tab === 'preview' && (
            <>
              <div className="sv-panel__head">
                <h2 className="sv-panel__title">Опрос целиком</h2>
                <span className="sv-panel__count">
                  {draft.questions.length} {plural(draft.questions.length, ['вопрос', 'вопроса', 'вопросов'])}
                </span>
              </div>
              <ol className="sv-readlist">
                {draft.questions.map((one, index) => (
                  <li key={index}>
                    <span className="sv-readlist__no">{index + 1}</span>
                    <span className="sv-readlist__text">
                      {one.text.trim() || 'Вопрос без текста'}
                      {one.options.filter((two) => two.trim()).length > 0 && (
                        <small>{one.options.filter((two) => two.trim()).join(' · ')}</small>
                      )}
                    </span>
                    <span className="sv-readlist__kind">
                      <AppIcon name={KIND_ICON[one.kind]} size={16} />
                      {KIND_SHORT[one.kind]}
                      {one.is_required && <em>Обязательный</em>}
                    </span>
                  </li>
                ))}
              </ol>
            </>
          )}

          <Refusal text={save.refusal} />
        </section>

        <aside className="sv-side">
          <section className="sv-panel" aria-label="Предпросмотр в Telegram">
            <h2 className="sv-panel__title">Предпросмотр в Telegram</h2>
            <p className="sv-panel__about">Так сотрудник увидит опрос</p>
            <TelegramPreview title={draft.title.trim() || 'Название опроса'}>
              <p className="sv-tg__step">
                Вопрос {shownIndex + 1} из {draft.questions.length}
              </p>
              <p className="sv-tg__q">{focused.text.trim() || 'Текст вопроса появится здесь'}</p>
              {focused.kind === 'SCALE' && (
                <div className="sv-tg__scale">
                  {[1, 2, 3, 4, 5].map((one) => <span key={one}>{one}</span>)}
                </div>
              )}
              {(focused.kind === 'SINGLE' || focused.kind === 'MULTI') && (
                <div className="sv-tg__options">
                  {focused.options.filter((one) => one.trim()).map((one, at) => (
                    <span key={at}>{one}</span>
                  ))}
                  {focused.options.filter((one) => one.trim()).length === 0 && (
                    <span>Вариант ответа</span>
                  )}
                </div>
              )}
              {focused.kind === 'TEXT' && (
                <p className="sv-tg__ghost">Напишите ответ…</p>
              )}
              <span className="sv-tg__button">Далее</span>
            </TelegramPreview>
          </section>

          <section className="sv-panel" aria-label="Перед публикацией">
            <h2 className="sv-panel__title">Перед публикацией</h2>
            <ul className="sv-checks">
              <Check done={Boolean(draft.title.trim())}>
                {draft.title.trim() ? 'Есть название' : 'Нужно название'}
              </Check>
              <Check done={draft.questions.length > 0}>
                Добавлено {draft.questions.length}{' '}
                {plural(draft.questions.length, ['вопрос', 'вопроса', 'вопросов'])}
              </Check>
              <Check done={withText && !problems['questions']}>
                {problems['questions'] ?? 'Все вопросы заполнены'}
              </Check>
            </ul>
          </section>
        </aside>
      </div>

      {publishing && (
        <Confirm
          title="Опубликовать шаблон"
          what={`По «${draft.title.trim()}» можно будет рассылать опросы и поручать их автоматизациям.`}
          consequence="После публикации вопросы этой версии не меняются: правка заводит новую версию, а прежняя остаётся вместе со своими ответами."
          confirmLabel="Опубликовать"
          refusal={save.refusal}
          busy={save.busy}
          onCancel={() => setPublishing(false)}
          onConfirm={publish}
        />
      )}
    </SurveyFrame>
  );
}

function Check({ done, children }: { done: boolean; children: React.ReactNode }) {
  return (
    <li className={done ? 'sv-check sv-check--done' : 'sv-check'}>
      <AppIcon name={done ? 'check' : 'clock'} size={18} />
      <span>{children}</span>
    </li>
  );
}

// --- просмотр опубликованного ---------------------------------------------------

function TemplateView({ row, onChanged }: {
  row: api.SurveyTemplate;
  onChanged: () => void;
}) {
  const navigate = useNavigate();
  const session = useSession();
  const zone = session.status === 'authenticated' ? session.user.timezone : '';
  const [tab, setTab] = useState<'questions' | 'settings' | 'usage'>('questions');
  const [all, setAll] = useState(false);
  const [menu, setMenu] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const act = useSaving();

  const [campaigns] = useBlock((signal) => api.surveyCampaigns({}, signal), 'sv-view-campaigns', true);
  const [rules] = useBlock((signal) => api.surveyAutomations(signal), 'sv-view-rules', true);
  const mine = campaigns.state === 'ready'
    ? campaigns.data.items.filter((one) => one.template_id === row.id) : [];
  const ruled = rules.state === 'ready'
    ? rules.data.items.filter((one) => one.template_id === row.id) : [];
  const running = mine.filter((one) => one.status === 'ACTIVE' || one.status === 'SCHEDULED');
  const people = mine.reduce((sum, one) => sum + (one.total ?? 0), 0);
  const used = (row.campaigns_count ?? mine.length) > 0;

  const newVersion = () => {
    let next: api.SurveyTemplate | null = null;
    void act.run(
      async () => { next = await api.newSurveyTemplateVersion(row.id); },
      () => { if (next) navigate(`/surveys/templates/${(next as api.SurveyTemplate).id}`); },
    );
  };

  const shown = all ? row.questions : row.questions.slice(0, 4);
  const archived = Boolean(row.archived_at);

  return (
    <SurveyFrame
      breadcrumb={`Опросы / ${row.title}`}
      crumbs={crumbs(row.title)}
      title={row.title}
      meta={(
        <p className="sv-status-line">
          <State tone={archived ? 'off' : 'ok'}>
            {archived ? TEMPLATE_STATUS.ARCHIVED : TEMPLATE_STATUS.PUBLISHED}
          </State>
          <i aria-hidden="true" />
          <span>
            Версия {row.version}
            {row.published_at ? ` · опубликован ${dayLong(row.published_at, zone)}` : ''}
          </span>
        </p>
      )}
      actions={(
        <>
          {!archived && (
            <button type="button" className="sv-btn" disabled={act.busy} onClick={newVersion}>
              <AppIcon name="doc" size={16} /> Создать новую версию
            </button>
          )}
          <span className="sv-more">
            <button type="button" className="sv-btn sv-btn--icon" aria-label="Ещё действия"
                    aria-expanded={menu} onClick={() => setMenu((was) => !was)}>
              <AppIcon name="more" size={18} />
            </button>
            <AppPopover open={menu} onClose={() => setMenu(false)} className="sv-menu">
              <button type="button"
                      onClick={() => {
                        setMenu(false);
                        let copy: api.SurveyTemplate | null = null;
                        void act.run(
                          async () => { copy = await api.copySurveyTemplate(row.id); },
                          () => { if (copy) navigate(`/surveys/templates/${(copy as api.SurveyTemplate).id}`); },
                        );
                      }}>
                Дублировать
              </button>
              {!archived && (
                <button type="button" className="sv-menu__bad"
                        onClick={() => { setMenu(false); setArchiving(true); }}>
                  Архивировать
                </button>
              )}
            </AppPopover>
          </span>
        </>
      )}
    >
      <p className="sv-note sv-note--top">
        <AppIcon name="info" size={16} />
        {archived
          ? 'Шаблон в архиве: по нему не рассылают, но все прошлые ответы на месте.'
          : used
            ? 'Этот шаблон уже использовался в рассылках. Изменения создаются только в новой версии.'
            : 'Опубликованный шаблон не меняют на месте. Изменения создаются только в новой версии.'}
      </p>

      <div className="sv-tabs" role="tablist" aria-label="Разделы шаблона">
        <button type="button" role="tab" aria-selected={tab === 'questions'}
                className={tab === 'questions' ? 'sv-tab sv-tab--on' : 'sv-tab'}
                onClick={() => setTab('questions')}>
          Вопросы <span className="sv-tab__count">{row.questions.length}</span>
        </button>
        <button type="button" role="tab" aria-selected={tab === 'settings'}
                className={tab === 'settings' ? 'sv-tab sv-tab--on' : 'sv-tab'}
                onClick={() => setTab('settings')}>
          Настройки
        </button>
        <button type="button" role="tab" aria-selected={tab === 'usage'}
                className={tab === 'usage' ? 'sv-tab sv-tab--on' : 'sv-tab'}
                onClick={() => setTab('usage')}>
          Использование
        </button>
      </div>

      <div className="sv-cols">
        <section className="sv-panel" aria-label="Содержимое шаблона">
          {tab === 'questions' && (
            <>
              <h2 className="sv-panel__title">Вопросы</h2>
              <ol className="sv-readlist">
                {shown.map((one) => (
                  <li key={one.id}>
                    <span className="sv-readlist__no">{one.position}</span>
                    <span className="sv-readlist__text">
                      {one.text}
                      {one.options && one.options.length > 0 && (
                        <small>{one.options.join(' · ')}</small>
                      )}
                    </span>
                    <span className="sv-readlist__kind">
                      <AppIcon name={KIND_ICON[one.kind]} size={16} />
                      {KIND_SHORT[one.kind]}
                      {one.is_required && <em>Обязательный</em>}
                    </span>
                  </li>
                ))}
              </ol>
              {row.questions.length > 4 && (
                <button type="button" className="sv-more-link" onClick={() => setAll((was) => !was)}>
                  <AppIcon name="chevron" size={16} {...(all ? { className: 'sv-flip' } : {})} />
                  {all
                    ? 'Свернуть'
                    : `Ещё ${row.questions.length - 4} ${plural(row.questions.length - 4, ['вопрос', 'вопроса', 'вопросов'])}`}
                </button>
              )}
            </>
          )}

          {tab === 'settings' && (
            <>
              <h2 className="sv-panel__title">Настройки</h2>
              <dl className="sv-facts-list">
                <div><dt>Название</dt><dd>{row.title}</dd></div>
                <div><dt>Описание</dt><dd>{row.description || '—'}</dd></div>
                <div><dt>Версия</dt><dd>{row.version}</dd></div>
                <div><dt>Вопросов</dt><dd>{row.questions.length}</dd></div>
              </dl>
              <p className="sv-note sv-note--inner">
                <AppIcon name="info" size={16} />
                Чтобы изменить название или вопросы, создайте новую версию —
                эта останется как есть вместе со своими ответами.
              </p>
            </>
          )}

          {tab === 'usage' && (
            <>
              <h2 className="sv-panel__title">Где используется</h2>
              {mine.length === 0 && ruled.length === 0 ? (
                <p className="sv-panel__about">
                  По этому шаблону ещё не рассылали, и ни одно правило его не использует.
                </p>
              ) : (
                <ul className="sv-uselist">
                  {mine.map((one) => (
                    <li key={one.id}>
                      <button type="button" onClick={() => navigate(`/surveys/campaigns/${one.id}`)}>
                        <AppIcon name="send" size={16} />
                        <span>
                          <b>{one.title}</b>
                          <small>
                            {one.sent_at ? `Отправлена ${atMoment(one.sent_at, zone)}` : 'Ещё не отправлена'}
                            {one.total ? ` · ${one.done ?? 0} из ${one.total} ответили` : ''}
                          </small>
                        </span>
                        <AppIcon name="next" size={16} />
                      </button>
                    </li>
                  ))}
                  {ruled.map((one) => (
                    <li key={one.id}>
                      <button type="button" onClick={() => navigate(`/surveys/automations/${one.id}`)}>
                        <AppIcon name="refresh" size={16} />
                        <span>
                          <b>{one.title}</b>
                          <small>Автоматизация · {one.is_active ? 'включена' : 'выключена'}</small>
                        </span>
                        <AppIcon name="next" size={16} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
          <Refusal text={act.refusal} />
        </section>

        <aside className="sv-side">
          <section className="sv-panel" aria-label="Использование шаблона">
            <h2 className="sv-panel__title">Использование шаблона</h2>
            <ul className="sv-usage">
              <li><AppIcon name="send" size={18} /><span>Активные рассылки</span><b>{running.length}</b></li>
              <li><AppIcon name="refresh" size={18} /><span>Автоматизации</span><b>{ruled.length}</b></li>
              <li><AppIcon name="users" size={18} /><span>Всего получателей</span><b>{people}</b></li>
            </ul>
            <button type="button" className="sv-btn sv-btn--wide"
                    onClick={() => navigate(`/surveys/campaigns?search=${encodeURIComponent(row.title)}`)}>
              <AppIcon name="arrow" size={16} /> Открыть рассылки
            </button>
          </section>

          <section className="sv-panel" aria-label="Кратко о шаблоне">
            <h2 className="sv-panel__title">Кратко о шаблоне</h2>
            {row.description && <p className="sv-panel__about">{row.description}</p>}
            <dl className="sv-facts-list sv-facts-list--small">
              <div><dt>Автор:</dt><dd>{row.author_name ?? '—'}</dd></div>
              <div><dt>Последнее изменение:</dt><dd>{dayLong(row.updated_at, zone)}</dd></div>
              {mine.length > 0 && (
                <div><dt>Рассылок всего:</dt><dd>{mine.length} · {peopleLine(people)}</dd></div>
              )}
            </dl>
          </section>
        </aside>
      </div>

      {archiving && (
        <Confirm
          title="Архивировать шаблон"
          what={`«${row.title}» пропадёт из списка и из выбора при создании рассылки.`}
          consequence="Уже отправленные рассылки и полученные ответы останутся: архив не стирает историю."
          confirmLabel="Архивировать"
          refusal={act.refusal}
          busy={act.busy}
          onCancel={() => setArchiving(false)}
          onConfirm={() => {
            void act.run(() => api.archiveSurveyTemplate(row.id), () => {
              setArchiving(false);
              onChanged();
            });
          }}
        />
      )}
    </SurveyFrame>
  );
}

