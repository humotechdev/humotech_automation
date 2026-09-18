/**
 * Одна рассылка опроса: кто получил, кто прошёл и что ответил.
 *
 * Ответы ИМЕННЫЕ и показываются именно так — фамилия, офис, отдел, дата
 * начала и завершения, каждый вопрос и ответ. Это не недосмотр
 * приватности: опрос с самого начала объявлен неанонимным, и сотрудник
 * видит это на первом экране в Telegram.
 *
 * Сводка стоит рядом, а не вместо. Средняя оценка по офису отвечает на
 * вопрос «где хуже», но не отвечает на вопрос «с кем поговорить», и
 * подменять одно другим — значит превратить инструмент разговора в
 * отчёт, который никто не читает.
 */

import { useCallback, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import {
  Confirm,
  Failed,
  Loading,
  Refusal,
  useSaving,
} from '../components/admin/Parts';
import { useBlock } from '../features/dashboard/data';
import { useSession } from '../features/auth/session';
import { moment } from '../features/time/zone';
import '../styles/admin.css';
import '../styles/surveys.css';

type Tab = 'answers' | 'recipients' | 'summary';

const RECIPIENT: Record<string, string> = {
  PENDING: 'Не отправлено',
  SENT: 'Отправлено',
  STARTED: 'Начал',
  COMPLETED: 'Завершил',
};

export function SurveyCampaignPage() {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const session = useSession();
  const zone = session.status === 'authenticated' ? session.user.timezone : '';

  const [params, setParams] = useSearchParams();
  const tab = (params.get('tab') as Tab) || 'answers';

  const [attempt, setAttempt] = useState(0);
  const [sending, setSending] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const act = useSaving();

  const [campaign, reloadCampaign] = useBlock(
    (signal) => api.surveyCampaign(id, signal),
    `survey-campaign|${id}|${attempt}`,
    Boolean(id),
  );
  const [summary, reloadSummary] = useBlock(
    (signal) => api.surveySummary(id, signal),
    `survey-summary|${id}|${attempt}`,
    Boolean(id),
  );
  const [answers, reloadAnswers] = useBlock(
    (signal) => api.surveyAnswers(id, signal),
    `survey-answers|${id}|${attempt}`,
    Boolean(id) && tab === 'answers',
  );
  const [recipients, reloadRecipients] = useBlock(
    (signal) => api.surveyRecipients(id, {}, signal),
    `survey-recipients|${id}|${attempt}`,
    Boolean(id) && tab === 'recipients',
  );

  const again = useCallback(() => {
    setSending(false);
    setCancelling(false);
    setAttempt((n) => n + 1);
    reloadCampaign();
    reloadSummary();
    reloadAnswers();
    reloadRecipients();
  }, [reloadAnswers, reloadCampaign, reloadRecipients, reloadSummary]);

  if (campaign.state === 'loading') {
    return (
      <AppShell breadcrumb="Опрос" section="surveys">
        <Loading />
      </AppShell>
    );
  }
  if (campaign.state !== 'ready') {
    return (
      <AppShell breadcrumb="Опрос" section="surveys">
        <Failed onRetry={reloadCampaign} />
      </AppShell>
    );
  }

  const row = campaign.data;
  const progress = summary.state === 'ready' ? summary.data.progress : null;
  const alive = row.status !== 'CANCELLED';

  return (
    <AppShell breadcrumb={row.title} section="surveys">
      <header className="head head--tight adm-head">
        <div>
          <p className="adm-head__up">
            <button type="button" className="link adm-back"
                    onClick={() => navigate('/surveys')}>
              <AppIcon name="back" size={16} /> Опросы
            </button>
          </p>
          <h1 className="head__title">{row.title}</h1>
          <p className="head__sub">
            {row.sent_at
              ? `Отправлена ${moment(row.sent_at, zone, false)}`
              : row.scheduled_at
                ? `Запланирована на ${moment(row.scheduled_at, zone, false)}`
                : 'Ещё не отправлена'}
            {row.repeat_months ? ` · повтор раз в ${row.repeat_months} мес.` : ''}
            {row.next_send_at
              ? ` · следующая ${moment(row.next_send_at, zone, false)}`
              : ''}
          </p>
        </div>
        <div className="head__actions">
          {alive && (
            <button type="button" className="btn btn--primary"
                    onClick={() => setSending(true)}>
              <AppIcon name="send" size={16} />{' '}
              {row.sent_at ? 'Отправить ещё раз' : 'Отправить сейчас'}
            </button>
          )}
          {alive && (
            <button type="button" className="btn btn--small"
                    onClick={() => setCancelling(true)}>
              Отменить
            </button>
          )}
        </div>
      </header>

      <ul className="summary" aria-label="Ход опроса">
        <Tile title="Получателей" value={progress?.total} icon="users" />
        <Tile title="Отправлено" value={progress?.sent} icon="send" />
        <Tile title="Начали" value={progress?.started} icon="half" />
        <Tile title="Завершили" value={progress?.completed} icon="check" />
      </ul>

      <Refusal text={act.refusal} />

      <div className="tabs tabs--top" role="tablist" aria-label="Разделы опроса">
        {([
          ['answers', 'Ответы'],
          ['recipients', 'Получатели'],
          ['summary', 'Сводка'],
        ] as Array<[Tab, string]>).map(([key, title]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key}
                  className={tab === key ? 'tab tab--on' : 'tab'}
                  onClick={() =>
                    setParams(
                      (was) => {
                        const copy = new URLSearchParams(was);
                        if (key === 'answers') copy.delete('tab');
                        else copy.set('tab', key);
                        return copy;
                      },
                      { replace: true },
                    )}>
            {title}
          </button>
        ))}
      </div>

      <section className="panel panel--list" aria-label="Содержимое опроса">
        {tab === 'answers' && (
          <Answers block={answers} zone={zone} onRetry={reloadAnswers} />
        )}
        {tab === 'recipients' && (
          <Recipients block={recipients} zone={zone} onRetry={reloadRecipients} />
        )}
        {tab === 'summary' && (
          <Summary block={summary} onRetry={reloadSummary} />
        )}
      </section>

      {sending && (
        <Confirm
          title={row.sent_at ? 'Отправить ещё раз' : 'Отправить опрос'}
          what={
            row.sent_at
              ? 'Опрос уйдёт тем, кто его ещё не получил. Уже получившим второе сообщение не придёт.'
              : 'Каждому получателю придёт одно сообщение от бота с кнопкой «Пройти опрос».'
          }
          consequence="Круг получателей считается сейчас: уволенные и без подключённого Telegram опрос не получат."
          confirmLabel="Отправить"
          refusal={act.refusal}
          busy={act.busy}
          onCancel={() => setSending(false)}
          onConfirm={() => {
            void act.run(() => api.sendSurveyCampaign(row.id), again);
          }}
        />
      )}

      {cancelling && (
        <Confirm
          title="Отменить рассылку"
          what="Повторов больше не будет, новые сообщения не уйдут."
          consequence="Уже полученные ответы останутся: отмена не стирает то, что люди написали."
          confirmLabel="Отменить рассылку"
          refusal={act.refusal}
          busy={act.busy}
          onCancel={() => setCancelling(false)}
          onConfirm={() => {
            void act.run(() => api.cancelSurveyCampaign(row.id), again);
          }}
        />
      )}
    </AppShell>
  );
}

function Tile({ title, value, icon }: {
  title: string;
  value: number | undefined;
  icon: Parameters<typeof AppIcon>[0]['name'];
}) {
  return (
    <li className="tile">
      <span className="tile__icon" aria-hidden="true"><AppIcon name={icon} size={20} /></span>
      <span className="tile__text">
        <span className="tile__title">{title}</span>
        {/* Пока сводка не пришла — прочерк: ноль означал бы, что
            получателей нет. */}
        <b className="tile__value">{value === undefined ? '—' : value}</b>
      </span>
    </li>
  );
}

// --- ответы ------------------------------------------------------------------

function Answers({ block, zone, onRetry }: {
  block: ReturnType<typeof useBlock<api.Items<api.SurveyFilled>>>[0];
  zone: string;
  onRetry: () => void;
}) {
  if (block.state === 'loading') return <Loading />;
  if (block.state !== 'ready') return <Failed onRetry={onRetry} />;

  const rows = block.data.items;
  if (rows.length === 0) {
    return (
      <p className="empty">
        Пока никто не завершил опрос. Начатые, но неотправленные ответы сюда
        не попадают: половина мнения — не мнение.
      </p>
    );
  }

  return (
    <div className="scroller sv-answers">
      {rows.map((row) => (
        <article key={row.id} className="sv-filled">
          <header className="sv-filled__head">
            <div>
              <p className="sv-filled__name">{row.full_name}</p>
              <p className="sv-filled__where">
                {[row.office_name, row.department_name]
                  .filter(Boolean)
                  .join(' · ') || 'без назначения'}
              </p>
            </div>
            <p className="sv-filled__when">
              {row.started_at && (
                <span>начал {moment(row.started_at, zone, false)}</span>
              )}
              {row.completed_at && (
                <span>завершил {moment(row.completed_at, zone, false)}</span>
              )}
            </p>
          </header>
          <dl className="sv-filled__answers">
            {row.answers.map((answer) => (
              <div key={answer.question_id} className="sv-filled__row">
                <dt>{answer.question_text}</dt>
                <dd>{show(answer)}</dd>
              </div>
            ))}
          </dl>
        </article>
      ))}
    </div>
  );
}

/** Ответ человеческим текстом: оценка читается как оценка. */
export function show(answer: api.SurveyFilledAnswer): string {
  if (answer.number !== null && answer.number !== undefined) {
    return `${answer.number} из 5`;
  }
  if (answer.options && answer.options.length) return answer.options.join(', ');
  return answer.text ?? '—';
}

// --- получатели ---------------------------------------------------------------

function Recipients({ block, zone, onRetry }: {
  block: ReturnType<typeof useBlock<api.Items<api.SurveyRecipient>>>[0];
  zone: string;
  onRetry: () => void;
}) {
  if (block.state === 'loading') return <Loading />;
  if (block.state !== 'ready') return <Failed onRetry={onRetry} />;

  const rows = block.data.items;
  if (rows.length === 0) {
    return (
      <p className="empty">
        Получателей ещё нет: список собирается в момент отправки.
      </p>
    );
  }

  return (
    <div className="scroller">
      <table className="grid-table table-cards" aria-label="Получатели опроса">
        <thead>
          <tr>
            <th scope="col">Сотрудник</th>
            <th scope="col">Состояние</th>
            <th scope="col">Отправлено</th>
            <th scope="col">Завершил</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="row">
              <td>{row.full_name}</td>
              <td data-label="Состояние">
                <span className="state">
                  <i className="state__dot" />
                  {RECIPIENT[row.status] ?? row.status}
                </span>
              </td>
              <td data-label="Отправлено">
                {row.sent_at ? moment(row.sent_at, zone, false) : '—'}
              </td>
              <td data-label="Завершил">
                {row.completed_at ? moment(row.completed_at, zone, false) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- сводка -------------------------------------------------------------------

function Summary({ block, onRetry }: {
  block: ReturnType<typeof useBlock<api.SurveySummary>>[0];
  onRetry: () => void;
}) {
  if (block.state === 'loading') return <Loading />;
  if (block.state !== 'ready') return <Failed onRetry={onRetry} />;

  const data = block.data;
  return (
    <div className="scroller sv-summary">
      <p className="adm-note">
        Сводка не делает опрос анонимным: те же ответы с именами — на
        вкладке «Ответы».
      </p>

      {data.questions.map((question) => (
        <article key={question.id} className="sv-stat">
          <header className="sv-stat__head">
            <h3 className="sv-stat__title">{question.text}</h3>
            <span className="sv-stat__count">ответов: {question.answered}</span>
          </header>

          {question.kind === 'SCALE' && (
            <>
              <p className="sv-stat__avg">
                Средняя оценка:{' '}
                <b>{question.average === null || question.average === undefined
                  ? '—'
                  : question.average}</b>
              </p>
              <Bars data={question.distribution ?? {}} total={question.answered} />
            </>
          )}

          {(question.kind === 'SINGLE' || question.kind === 'MULTI') && (
            <Bars data={question.distribution ?? {}} total={question.answered} />
          )}

          {question.kind === 'TEXT' && (
            <ul className="sv-texts">
              {(question.texts ?? []).length === 0 && (
                <li className="muted">ответов нет</li>
              )}
              {(question.texts ?? []).map((text, index) => (
                <li key={index}>{text}</li>
              ))}
            </ul>
          )}
        </article>
      ))}

      <Places title="По офисам" rows={data.offices} />
      <Places title="По отделам" rows={data.departments} />
    </div>
  );
}

function Bars({ data, total }: { data: Record<string, number>; total: number }) {
  const entries = Object.entries(data);
  // Доля считается от числа ответивших, а не от числа получателей:
  // иначе «60 % за график» означало бы совсем другое.
  const base = Math.max(total, 1);
  return (
    <ul className="sv-bars">
      {entries.map(([label, value]) => (
        <li key={label} className="sv-bar">
          <span className="sv-bar__label">{label}</span>
          <span className="sv-bar__track">
            <span className="sv-bar__fill"
                  style={{ width: `${Math.round((value / base) * 100)}%` }} />
          </span>
          <span className="sv-bar__value">{value}</span>
        </li>
      ))}
    </ul>
  );
}

function Places({ title, rows }: {
  title: string;
  rows: Array<{ name: string; total: number; completed: number }>;
}) {
  if (rows.length === 0) return null;
  return (
    <article className="sv-stat">
      <h3 className="sv-stat__title">{title}</h3>
      <ul className="sv-bars">
        {rows.map((row) => (
          <li key={row.name} className="sv-bar">
            <span className="sv-bar__label">{row.name}</span>
            <span className="sv-bar__track">
              <span className="sv-bar__fill"
                    style={{
                      width: `${Math.round(
                        (row.completed / Math.max(row.total, 1)) * 100,
                      )}%`,
                    }} />
            </span>
            <span className="sv-bar__value">
              {row.completed} из {row.total}
            </span>
          </li>
        ))}
      </ul>
    </article>
  );
}
