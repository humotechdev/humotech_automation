/**
 * Одна рассылка: итоги, люди поимённо, ответы по вопросам.
 *
 * Опрос ИМЕННОЙ с самого начала, и сотрудник видит это на первом экране
 * в Telegram. Поэтому ответ стоит рядом с фамилией: анонимная цитата
 * здесь была бы не защитой приватности, а обманом обеих сторон.
 *
 * У запланированной рассылки результатов ещё нет — вместо них сводка того,
 * что и кому уйдёт, и два действия: перенести или отменить. Отправленную не
 * правят: люди уже получили приглашение.
 *
 * Аналитика только из настоящих ответов. График по дням — это даты, когда
 * люди действительно закончили опрос; нет ответов — нет столбиков, а не
 * «красивая» заглушка.
 */

import { useCallback, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppIcon } from '../components/AppIcon';
import { AppPopover, AppSelectField } from '../components/AppSelect';
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
  AUDIENCE,
  State,
  SurveyFrame,
  atMoment,
  atZone,
  campaignTone,
  peopleLine,
  plural,
  questionsLine,
  zoneLine,
} from '../features/surveys/model';
import '../styles/admin.css';
import '../styles/surveys.css';

const CAMPAIGN_WORD: Record<api.SurveyCampaign['status'], string> = {
  DRAFT: 'Черновик',
  SCHEDULED: 'Запланирована',
  ACTIVE: 'Идёт',
  FINISHED: 'Завершена',
  CANCELLED: 'Отменена',
};

/** Исход по человеку — словами, которыми о нём говорят. */
function outcome(row: api.SurveyRecipient): { title: string; tone: 'ok' | 'wait' | 'off' | 'bad' } {
  if (row.status === 'COMPLETED') return { title: 'Завершил', tone: 'ok' };
  if (row.status === 'SKIPPED') {
    return row.skip_reason === 'Уже не работает в компании'
      ? { title: 'Исключён', tone: 'off' }
      : { title: 'Не доставлено', tone: 'bad' };
  }
  if (row.status === 'EXPIRED') return { title: 'Срок истёк', tone: 'off' };
  return { title: 'Ожидает ответа', tone: 'wait' };
}

/** Ответ человека одной строкой: число, варианты или текст. */
export function show(answer: api.SurveyFilledAnswer): string {
  if (answer.number !== null && answer.number !== undefined) {
    return `${answer.number} из 5`;
  }
  if (answer.options && answer.options.length) return answer.options.join(', ');
  return answer.text ?? '—';
}

/**
 * «Сегодня, 12:24», «Вчера, 18:27», «12 сен, 10:00» — коротко, для
 * узких колонок. Полная дата с годом тут не нужна: рассылка живёт дни.
 */
function recentMoment(at: string, zone: string): string {
  const where = zone ? { timeZone: zone } : {};
  const date = new Date(at);
  const key = (d: Date) => new Intl.DateTimeFormat('en-CA', {
    year: 'numeric', month: '2-digit', day: '2-digit', ...where,
  }).format(d);
  const time = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', ...where }).format(date);
  const today = key(new Date());
  const yesterday = key(new Date(Date.now() - 86_400_000));
  if (key(date) === today) return `Сегодня, ${time}`;
  if (key(date) === yesterday) return `Вчера, ${time}`;
  const day = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', ...where })
    .format(date).replace('.', '');
  return `${day}, ${time}`;
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2)
    .map((one) => one[0]?.toUpperCase() ?? '').join('');
}

/** День в поясе организации — ключ для графика. */
function dayKey(at: string, zone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    year: 'numeric', month: '2-digit', day: '2-digit', ...(zone ? { timeZone: zone } : {}),
  }).format(new Date(at));
  return parts;
}

function dayShort(key: string): string {
  const [y, m, d] = key.split('-').map(Number);
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' })
    .format(new Date(Date.UTC(y ?? 2000, (m ?? 1) - 1, d ?? 1)))
    .replace('.', '');
}

const TIMES = Array.from({ length: 25 }, (_, at) => {
  const minutes = 8 * 60 + at * 30;
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
});

type Tab = 'results' | 'people' | 'questions' | 'settings';

export function SurveyCampaignPage() {
  const { id = '' } = useParams();
  const session = useSession();
  const zone = session.status === 'authenticated' ? session.user.timezone : '';

  const [attempt, setAttempt] = useState(0);
  const [tab, setTab] = useState<Tab>('results');
  const [menu, setMenu] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [sending, setSending] = useState(false);
  const [editing, setEditing] = useState(false);
  const [look, setLook] = useState('');
  const [opened, setOpened] = useState<string | null>(null);
  const [allRecent, setAllRecent] = useState(false);
  const [told, setTold] = useState<Record<string, string>>({});
  const act = useSaving();

  const [campaign, reloadCampaign] = useBlock(
    (signal) => api.surveyCampaign(id, signal), `survey-campaign|${id}|${attempt}`, Boolean(id),
  );
  const [summary] = useBlock(
    (signal) => api.surveySummary(id, signal), `survey-summary|${id}|${attempt}`, Boolean(id),
  );
  const [answers] = useBlock(
    (signal) => api.surveyAnswers(id, signal), `survey-answers|${id}|${attempt}`, Boolean(id),
  );
  const [recipients] = useBlock(
    (signal) => api.surveyRecipients(id, {}, signal), `survey-recipients|${id}|${attempt}`, Boolean(id),
  );

  const again = useCallback(() => setAttempt((n) => n + 1), []);

  const people = recipients.state === 'ready' ? recipients.data.items : [];
  const filled = answers.state === 'ready' ? answers.data.items : [];
  const byRecipient = useMemo(() => {
    const map = new Map<string, api.SurveyFilled>();
    for (const one of filled) map.set(one.id, one);
    return map;
  }, [filled]);

  const shown = useMemo(() => {
    const needle = look.trim().toLowerCase();
    return needle
      ? people.filter((one) => one.full_name.toLowerCase().includes(needle))
      : people;
  }, [look, people]);

  const crumbsFor = (title: string): Array<[string, string?]> => [
    ['Рабочее пространство', '/'],
    ['Опросы', '/surveys'],
    ['Рассылки', '/surveys/campaigns'],
    [title],
  ];

  if (campaign.state !== 'ready') {
    return (
      <SurveyFrame breadcrumb="Опросы / Рассылка" crumbs={crumbsFor('Рассылка')} title="Рассылка">
        {campaign.state === 'loading' ? <Loading /> : <Failed onRetry={reloadCampaign} />}
      </SurveyFrame>
    );
  }

  const row = campaign.data;
  const count = summary.state === 'ready' ? summary.data.progress : null;
  const scheduled = row.status === 'SCHEDULED';
  const live = row.status === 'ACTIVE';
  const closedByDate = row.due_at ? Date.parse(row.due_at) <= Date.now() : false;
  const canRemind = live && !closedByDate;

  const remind = (ids?: string[]) => {
    let result: { reminded: number; already: number } | null = null;
    void act.run(
      async () => { result = await api.remindSurveyCampaign(row.id, ids); },
      () => {
        const got = result as { reminded: number; already: number } | null;
        if (!got) return;
        const text = got.reminded > 0
          ? `Напомнили: ${got.reminded}`
          : 'Сегодня уже напоминали';
        if (ids?.length === 1) setTold({ ...told, [ids[0] as string]: text });
        else setTold({ ...told, all: text });
      },
    );
  };

  const statusLine = scheduled
    ? `Уйдёт ${row.scheduled_at ? atMoment(row.scheduled_at, zone) : '—'}`
    : row.remind_at && !row.reminded_at && live
      ? `Напоминание не ответившим — ${atMoment(row.remind_at, zone)}`
      : row.due_at
        ? `Приём ответов ${closedByDate ? 'закрыт' : 'до'} ${atMoment(row.due_at, zone)}`
        : 'Без срока ответа';

  const reachable = count ? count.reachable : 0;
  const done = count ? count.completed : 0;
  const waiting = count ? Math.max(count.reachable - count.completed - count.expired, 0) : 0;
  const share = reachable > 0 ? Math.round((done / reachable) * 100) : 0;

  // График: сколько человек закончили опрос в каждый день после отправки.
  const days = (() => {
    if (!row.sent_at) return [] as Array<[string, number]>;
    const start = dayKey(row.sent_at, zone);
    const end = dayKey(new Date().toISOString(), zone);
    const keys: string[] = [];
    const cursor = new Date(`${start}T12:00:00Z`);
    while (keys.length < 14) {
      const key = cursor.toISOString().slice(0, 10);
      keys.push(key);
      if (key >= end) break;
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
    const counts = new Map(keys.map((key) => [key, 0]));
    for (const one of people) {
      if (!one.completed_at) continue;
      const key = dayKey(one.completed_at, zone);
      if (counts.has(key)) counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()];
  })();
  const peak = Math.max(1, ...days.map(([, n]) => n));

  const recent = [...people]
    .filter((one) => one.completed_at)
    .sort((a, b) => (b.completed_at ?? '').localeCompare(a.completed_at ?? ''));

  // Именные ответы на текстовые вопросы — из ответов людей, а не из
  // сводки: в сводке тексты без фамилий.
  const namedTexts = (questionId: string) =>
    filled.flatMap((one) =>
      one.answers
        .filter((answer) => answer.question_id === questionId && answer.text)
        .map((answer) => ({ name: one.full_name, text: answer.text as string })));

  return (
    <SurveyFrame
      breadcrumb={`Опросы / ${row.title}`}
      crumbs={crumbsFor(row.title)}
      title={row.title}
      meta={(
        <>
          <p className="sv-lede">
            {row.sent_at ? `Отправлена ${atMoment(row.sent_at, zone)}` : 'Ещё не отправлена'}
            {' · '}Шаблон «{row.template_title}»
            {row.template_version ? ` · версия ${row.template_version}` : ''}
            {row.automation_id ? ' · завела автоматизация' : ''}
          </p>
          <p className="sv-status-line">
            <State tone={campaignTone(row.status)}>{CAMPAIGN_WORD[row.status]}</State>
            <i aria-hidden="true" />
            <span>{statusLine}</span>
          </p>
        </>
      )}
      actions={(
        <>
          {!scheduled && (
            <a className="sv-btn" href={api.surveyExportUrl(row.id)}>
              <AppIcon name="download" size={16} /> Экспорт
            </a>
          )}
          {scheduled && row.automation_id === null && (
            <button type="button" className="sv-btn" onClick={() => setEditing(true)}>
              <AppIcon name="pencil" size={16} /> Редактировать
            </button>
          )}
          {(scheduled || live) && (
            <span className="sv-more">
              <button type="button" className="sv-btn sv-btn--icon" aria-label="Ещё действия"
                      aria-expanded={menu} onClick={() => setMenu((was) => !was)}>
                <AppIcon name="more" size={18} />
              </button>
              <AppPopover open={menu} onClose={() => setMenu(false)} className="sv-menu">
                {scheduled && (
                  <button type="button" onClick={() => { setMenu(false); setSending(true); }}>
                    Отправить сейчас
                  </button>
                )}
                {live && (
                  <button type="button" onClick={() => { setMenu(false); setSending(true); }}>
                    Отправить ещё раз
                  </button>
                )}
                {canRemind && waiting > 0 && (
                  <button type="button" onClick={() => { setMenu(false); remind(); }}>
                    Напомнить всем, кто не ответил
                  </button>
                )}
                <button type="button" className="sv-menu__bad"
                        onClick={() => { setMenu(false); setCancelling(true); }}>
                  Отменить рассылку
                </button>
              </AppPopover>
            </span>
          )}
        </>
      )}
    >
      {told['all'] && <p className="sv-note sv-note--top"><AppIcon name="check" size={16} />{told['all']}</p>}
      <Refusal text={act.refusal} />

      {scheduled ? (
        <div className="sv-cols">
          <section className="sv-panel" aria-label="Сводка рассылки">
            <h2 className="sv-panel__title">Что уйдёт</h2>
            <dl className="sv-review-list">
              <div>
                <dt><AppIcon name="doc" size={18} />Шаблон</dt>
                <dd>
                  <b>{row.template_title}</b>
                  <small>{row.template_question_count ? questionsLine(row.template_question_count) : ''}</small>
                </dd>
              </div>
              <div>
                <dt><AppIcon name="users" size={18} />Кому</dt>
                <dd>
                  <b>{AUDIENCE[row.audience_kind]}</b>
                  <small>
                    {row.planned != null
                      ? `${peopleLine(row.planned)} — круг зафиксирован при создании`
                      : 'Круг соберётся в день отправки'}
                  </small>
                </dd>
              </div>
              <div>
                <dt><AppIcon name="calendar" size={18} />Отправка</dt>
                <dd>
                  <b>{row.scheduled_at ? atMoment(row.scheduled_at, zone) : '—'}</b>
                  <small>{zoneLine(zone)}{row.repeat_months ? ` · повтор раз в ${row.repeat_months} мес.` : ''}</small>
                </dd>
              </div>
              <div>
                <dt><AppIcon name="bell" size={18} />Напоминание</dt>
                <dd>
                  <b>{row.remind_at ? atMoment(row.remind_at, zone) : 'Не напоминать'}</b>
                  <small>{row.due_at ? `Приём ответов до ${atMoment(row.due_at, zone)}` : 'Без срока ответа'}</small>
                </dd>
              </div>
            </dl>
          </section>
          <aside className="sv-side">
            <section className="sv-panel">
              <h2 className="sv-panel__title">До отправки</h2>
              <p className="sv-panel__text">
                Рассылку можно перенести или отменить. После отправки её уже не
                меняют: люди получат приглашение, и другой срок или другой круг
                сделали бы их ответы ответами на другой опрос.
              </p>
              {row.automation_id === null && (
                <button type="button" className="sv-btn sv-btn--wide" onClick={() => setEditing(true)}>
                  <AppIcon name="pencil" size={16} /> Редактировать
                </button>
              )}
              <button type="button" className="sv-btn sv-btn--wide sv-btn--danger" style={{ marginTop: 10 }}
                      onClick={() => setCancelling(true)}>
                Отменить рассылку
              </button>
            </section>
          </aside>
        </div>
      ) : (
        <>
          <div className="sv-tabs" role="tablist" aria-label="Разделы рассылки">
            {([
              ['results', 'Результаты', null],
              ['people', 'Получатели', people.length],
              ['questions', 'Вопросы', summary.state === 'ready' ? summary.data.questions.length : null],
              ['settings', 'Настройки', null],
            ] as Array<[Tab, string, number | null]>).map(([key, label, n]) => (
              <button key={key} type="button" role="tab" aria-selected={tab === key}
                      className={tab === key ? 'sv-tab sv-tab--on' : 'sv-tab'}
                      onClick={() => setTab(key)}>
                {label}
                {n !== null && <span className="sv-tab__count">{n}</span>}
              </button>
            ))}
          </div>

          {tab === 'results' && (
            <>
              <div className="sv-kpis">
                <div className="sv-kpi">
                  <b>{done} из {reachable}</b>
                  <span>Завершили опрос</span>
                </div>
                <div className="sv-kpi">
                  <b>{waiting}</b>
                  <span>Ожидают ответа</span>
                </div>
                <div className="sv-kpi">
                  <b>{count ? count.skipped : 0}</b>
                  <span>Не доставлено или исключены</span>
                </div>
                <div className="sv-kpi">
                  <b>{share}%</b>
                  <span>Прохождение</span>
                </div>
              </div>

              <div className="sv-cols sv-cols--half">
                <div className="sv-side">
                  <section className="sv-panel" aria-label="Динамика ответов">
                    <div className="sv-panel__head sv-panel__head--spread">
                      <h2 className="sv-panel__title">Динамика ответов</h2>
                      <span className="sv-legend"><i />Ответили</span>
                    </div>
                    {done === 0 ? (
                      <p className="sv-panel__about">Пока никто не закончил опрос — столбики появятся с первыми ответами.</p>
                    ) : (
                      <div className="sv-chart" role="img"
                           aria-label={`Ответы по дням: ${days.map(([k, n]) => `${dayShort(k)} — ${n}`).join(', ')}`}>
                        {days.map(([key, n]) => (
                          <div key={key} className="sv-chart__col">
                            <span className="sv-chart__n">{n || ''}</span>
                            <span className="sv-chart__bar" style={{ height: `${(n / peak) * 100}%` }} />
                            <span className="sv-chart__day">{dayShort(key)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className="sv-meter sv-meter--wide">
                      <span className="sv-meter__text">Прогресс: {done} из {reachable}</span>
                      <span className="sv-meter__bar">
                        <span className="sv-meter__track">
                          <span className="sv-meter__fill" style={{ width: `${share}%` }} />
                        </span>
                        <span className="sv-meter__share">{share}%</span>
                      </span>
                    </div>
                  </section>

                  <section className="sv-panel" aria-label="Последние ответы">
                    <h2 className="sv-panel__title">Последние ответы</h2>
                    {recent.length === 0 ? (
                      <p className="sv-panel__about">Ответов пока нет.</p>
                    ) : (
                      <>
                        <ul className="sv-people-list">
                          {(allRecent ? recent : recent.slice(0, 5)).map((one) => (
                            <li key={one.id}>
                              <button type="button" onClick={() => { setTab('people'); setOpened(one.id); setLook(one.full_name); }}>
                                <span className="sv-face">{initials(one.full_name)}</span>
                                <span className="sv-people-list__name">{one.full_name}</span>
                                <span className="sv-people-list__when">{recentMoment(one.completed_at as string, zone)}</span>
                                <AppIcon name="next" size={16} />
                              </button>
                            </li>
                          ))}
                        </ul>
                        {recent.length > 5 && (
                          <button type="button" className="sv-more-link" onClick={() => setAllRecent((was) => !was)}>
                            <AppIcon name="chevron" size={16} {...(allRecent ? { className: 'sv-flip' } : {})} />
                            {allRecent ? 'Свернуть' : 'Показать ещё'}
                          </button>
                        )}
                      </>
                    )}
                  </section>
                </div>

                <section className="sv-panel" aria-label="Статус по сотрудникам">
                  <div className="sv-panel__head sv-panel__head--spread">
                    <h2 className="sv-panel__title">Статус по сотрудникам</h2>
                    <label className="sv-find sv-find--small">
                      <AppIcon name="search" size={16} />
                      <input type="search" value={look} placeholder="Найти сотрудника"
                             aria-label="Найти сотрудника"
                             onChange={(event) => setLook(event.target.value)} />
                    </label>
                  </div>
                  <PeopleTable rows={shown.slice(0, 8)} zone={zone} canRemind={canRemind}
                               told={told} busy={act.busy}
                               onRemind={(one) => remind([one.id])} compact />
                  {shown.length > 8 && (
                    <button type="button" className="sv-more-link" onClick={() => setTab('people')}>
                      Все получатели ({shown.length})
                    </button>
                  )}
                </section>
              </div>
            </>
          )}

          {tab === 'people' && (
            <section className="sv-panel" aria-label="Получатели">
              <div className="sv-panel__head sv-panel__head--spread">
                <h2 className="sv-panel__title">Получатели</h2>
                <label className="sv-find sv-find--small">
                  <AppIcon name="search" size={16} />
                  <input type="search" value={look} placeholder="Найти сотрудника"
                         aria-label="Найти сотрудника"
                         onChange={(event) => setLook(event.target.value)} />
                </label>
              </div>
              <PeopleTable rows={shown} zone={zone} canRemind={canRemind} told={told}
                           busy={act.busy} onRemind={(one) => remind([one.id])}
                           opened={opened} onOpen={(one) => setOpened(opened === one.id ? null : one.id)}
                           answersOf={(one) => byRecipient.get(one.id) ?? null} />
            </section>
          )}

          {tab === 'questions' && (
            <div className="sv-qresults">
              <p className="sv-note">
                <AppIcon name="info" size={16} />
                {campaign.state === 'ready' && campaign.data.is_anonymous
                  ? 'Анонимный опрос: видна только сводка по вопросам — ответов по людям и имён в выгрузке нет.'
                  : 'Опрос именной: сводка по вопросам не делает опрос анонимным — у каждого текстового ответа стоит имя.'}
              </p>
              {summary.state !== 'ready' ? <Loading /> : summary.data.questions.map((one, index) => (
                <section key={one.id} className="sv-panel" aria-label={one.text}>
                  <div className="sv-qresult__head">
                    <span className="sv-readlist__no">{index + 1}</span>
                    <span>
                      <b>{one.text}</b>
                      <small>{one.answered} {plural(one.answered, ['ответ', 'ответа', 'ответов'])}</small>
                    </span>
                    {one.kind === 'SCALE' && one.average != null && (
                      <span className="sv-qresult__avg">{String(one.average).replace('.', ',')}<small>из 5</small></span>
                    )}
                  </div>
                  {one.answered === 0 ? (
                    <p className="sv-panel__about">На этот вопрос ещё не ответили.</p>
                  ) : one.kind === 'TEXT' ? (
                    <ul className="sv-said">
                      {namedTexts(one.id).map((item, at) => (
                        <li key={at}><b>{item.name}</b><span>{item.text}</span></li>
                      ))}
                    </ul>
                  ) : (
                    <ul className={one.kind === 'SCALE' ? 'sv-dist sv-dist--scale' : 'sv-dist'}>
                      {Object.entries(one.distribution ?? {}).map(([label, n]) => {
                        const total = one.kind === 'SCALE'
                          ? Object.values(one.distribution ?? {}).reduce((s, v) => s + v, 0)
                          : one.answered;
                        const part = total > 0 ? Math.round((n / total) * 100) : 0;
                        return (
                          <li key={label}>
                            <span className="sv-dist__label">{label}</span>
                            <span className="sv-meter__track"><span className="sv-meter__fill" style={{ width: `${part}%` }} /></span>
                            <span className="sv-dist__n">{n} · {part}%</span>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>
              ))}
            </div>
          )}

          {tab === 'settings' && (
            <section className="sv-panel" aria-label="Настройки рассылки">
              <h2 className="sv-panel__title">Настройки</h2>
              <dl className="sv-facts-list">
                <div><dt>Шаблон</dt><dd>{row.template_title}{row.template_version ? ` · версия ${row.template_version}` : ''}</dd></div>
                <div><dt>Кому</dt><dd>{AUDIENCE[row.audience_kind]}</dd></div>
                <div><dt>Отправлена</dt><dd>{row.sent_at ? atMoment(row.sent_at, zone) : '—'}</dd></div>
                <div><dt>Напоминание</dt><dd>{row.remind_at ? atMoment(row.remind_at, zone) : 'Не напоминать'}</dd></div>
                <div><dt>Срок ответа</dt><dd>{row.due_at ? atMoment(row.due_at, zone) : 'Без срока'}</dd></div>
                <div><dt>Повтор</dt><dd>{row.repeat_months ? `Раз в ${row.repeat_months} мес.` : 'Нет'}</dd></div>
              </dl>
              <p className="sv-note sv-note--inner">
                <AppIcon name="info" size={16} />
                Отправленную рассылку не меняют: аудиторию и шаблон зафиксировали
                в момент отправки.
              </p>
            </section>
          )}
        </>
      )}

      {cancelling && (
        <Confirm
          title="Отменить рассылку"
          what={`«${row.title}» ${scheduled ? 'не уйдёт' : 'перестанет принимать ответы'}.`}
          consequence="Уже полученные ответы останутся на месте: отмена не стирает историю."
          confirmLabel="Отменить рассылку"
          refusal={act.refusal}
          busy={act.busy}
          onCancel={() => setCancelling(false)}
          onConfirm={() => {
            void act.run(() => api.cancelSurveyCampaign(row.id), () => { setCancelling(false); again(); });
          }}
        />
      )}

      {sending && (
        <Confirm
          title={scheduled ? 'Отправить сейчас' : 'Отправить ещё раз'}
          what={scheduled
            ? `«${row.title}» уйдёт прямо сейчас, не дожидаясь назначенного времени.`
            : 'Приглашение получат те, кто вошёл в круг после отправки. Кому опрос уже ушёл, второго сообщения не будет.'}
          consequence="Отозвать отправленное приглашение нельзя: сообщение останется в Telegram."
          confirmLabel="Отправить"
          refusal={act.refusal}
          busy={act.busy}
          onCancel={() => setSending(false)}
          onConfirm={() => {
            void act.run(() => api.sendSurveyCampaign(row.id), () => { setSending(false); again(); });
          }}
        />
      )}

      {editing && (
        <EditScheduled row={row} zone={zone} onClose={() => setEditing(false)}
                       onSaved={() => { setEditing(false); again(); }} />
      )}
    </SurveyFrame>
  );
}

function PeopleTable({
  rows, zone, canRemind, told, busy, onRemind, compact, opened, onOpen, answersOf,
}: {
  rows: api.SurveyRecipient[];
  zone: string;
  canRemind: boolean;
  told: Record<string, string>;
  busy: boolean;
  onRemind: (row: api.SurveyRecipient) => void;
  compact?: boolean;
  opened?: string | null;
  onOpen?: (row: api.SurveyRecipient) => void;
  answersOf?: (row: api.SurveyRecipient) => api.SurveyFilled | null;
}) {
  if (rows.length === 0) {
    return <p className="sv-panel__about">Никого не нашлось.</p>;
  }
  return (
    <ul className={compact ? 'sv-ptable sv-ptable--compact' : 'sv-ptable'}>
      {!compact && (
        <li className="sv-ptable__head" aria-hidden="true">
          <span>Сотрудник</span><span>Офис · отдел</span><span>Статус</span><span>Последняя активность</span><span />
        </li>
      )}
      {rows.map((one) => {
        const state = outcome(one);
        const last = one.completed_at ?? one.started_at ?? one.sent_at;
        const waiting = one.status === 'SENT' || one.status === 'STARTED';
        const filled = answersOf?.(one) ?? null;
        const open = opened === one.id && filled;
        return (
          <li key={one.id} className="sv-ptable__row">
            <div className="sv-ptable__line">
              <span className="sv-ptable__who">
                <span className="sv-face">{initials(one.full_name)}</span>
                <b>{one.full_name}</b>
              </span>
              {!compact && (
                <span className="sv-ptable__muted">
                  {[one.office_name, one.department_name].filter(Boolean).join(' · ') || '—'}
                </span>
              )}
              <span>
                <State tone={state.tone}>{state.title}</State>
                {one.skip_reason && <small className="sv-ptable__why">{one.skip_reason}</small>}
              </span>
              <span className="sv-ptable__muted">{last ? recentMoment(last, zone) : '—'}</span>
              <span className="sv-ptable__act">
                {told[one.id] ? (
                  <small className="sv-ptable__told">{told[one.id]}</small>
                ) : canRemind && waiting ? (
                  <button type="button" className="sv-link" disabled={busy} onClick={() => onRemind(one)}>
                    <AppIcon name="bell" size={16} /> Напомнить
                  </button>
                ) : filled && onOpen ? (
                  <button type="button" className="sv-link" onClick={() => onOpen(one)}>
                    {open ? 'Скрыть ответы' : 'Ответы'}
                  </button>
                ) : null}
              </span>
            </div>
            {open && filled && (
              <dl className="sv-answers">
                {filled.answers.map((answer) => (
                  <div key={answer.question_id}>
                    <dt>{answer.question_text}</dt>
                    <dd>{show(answer)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Перенос запланированной рассылки: день, час, напоминание, срок. */
function EditScheduled({ row, zone, onClose, onSaved }: {
  row: api.SurveyCampaign;
  zone: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const initial = row.scheduled_at ? new Date(row.scheduled_at) : new Date();
  const local = (date: Date) => {
    const parts = new Intl.DateTimeFormat('en-CA', {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
      hourCycle: 'h23', ...(zone ? { timeZone: zone } : {}),
    }).formatToParts(date);
    const get = (type: string) => parts.find((one) => one.type === type)?.value ?? '';
    return { day: `${get('year')}-${get('month')}-${get('day')}`, time: `${get('hour')}:${get('minute')}` };
  };
  const start = local(initial);
  const [day, setDay] = useState(start.day);
  const [time, setTime] = useState(TIMES.includes(start.time) ? start.time : '10:00');
  const [remindIn, setRemindIn] = useState(
    row.remind_at && row.scheduled_at
      ? String(Math.round((Date.parse(row.remind_at) - Date.parse(row.scheduled_at)) / 86_400_000)) : '',
  );
  const [dueIn, setDueIn] = useState(
    row.due_at && row.scheduled_at
      ? String(Math.round((Date.parse(row.due_at) - Date.parse(row.scheduled_at)) / 86_400_000)) : '',
  );
  const save = useSaving();
  const at = atZone(`${day}T${time}`, zone);

  return (
    <div className="adm-ask" role="dialog" aria-modal="true" aria-label="Изменить рассылку">
      <div className="adm-ask__box">
        <h2 className="adm-ask__title">Изменить рассылку</h2>
        <p className="adm-ask__what">Меняется только время и напоминания: шаблон и круг получателей зафиксированы.</p>
        <div className="sv-fields sv-fields--row">
          <div className="sv-field">
            <span>Дата</span>
            <DatePicker label="Дата отправки" value={day} now={todayIso()} min={todayIso()} onChange={setDay} />
          </div>
          <div className="sv-field">
            <span>Время</span>
            <AppSelectField label="Время" value={time} onChange={setTime}>
              {TIMES.map((one) => <option key={one} value={one}>{one}</option>)}
            </AppSelectField>
          </div>
        </div>
        <div className="sv-fields sv-fields--row">
          <div className="sv-field">
            <span>Напомнить</span>
            <AppSelectField label="Напомнить" value={remindIn} onChange={setRemindIn}>
              <option value="">Не напоминать</option>
              {['1', '2', '3', '7'].map((one) => <option key={one} value={one}>Через {one} дн.</option>)}
            </AppSelectField>
          </div>
          <div className="sv-field">
            <span>Закрыть опрос</span>
            <AppSelectField label="Закрыть опрос" value={dueIn} onChange={setDueIn}>
              <option value="">Без срока</option>
              {['3', '7', '14', '30'].map((one) => <option key={one} value={one}>Через {one} дн.</option>)}
            </AppSelectField>
          </div>
        </div>
        <Refusal text={save.refusal} />
        <div className="adm-ask__tools">
          <button type="button" className="sv-btn" onClick={onClose} disabled={save.busy}>Отмена</button>
          <button type="button" className="sv-btn sv-btn--main" disabled={save.busy || !day}
                  onClick={() => {
                    void save.run(
                      () => api.updateSurveyCampaign(row.id, {
                        scheduled_at: at.toISOString(),
                        remind_at: remindIn ? new Date(at.getTime() + Number(remindIn) * 86_400_000).toISOString() : null,
                        due_at: dueIn ? new Date(at.getTime() + Number(dueIn) * 86_400_000).toISOString() : null,
                      }),
                      onSaved,
                    );
                  }}>
            {save.busy ? 'Сохраняем…' : 'Сохранить'}
          </button>
        </div>
      </div>
    </div>
  );
}
