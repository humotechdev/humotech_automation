/**
 * Опросы: шаблоны, рассылки и автоматизации.
 *
 * Три сущности, а не три взгляда на одну. Шаблон отвечает на «что
 * спрашивают», рассылка — на «кого спросили в тот день», автоматизация —
 * на «почему спросят завтра». Свести их нельзя: у правила нет
 * получателей и не будет до самого события, а у рассылки нет будущего —
 * она уже случилась.
 *
 * Порядок вкладок — Шаблоны, Рассылки, Автоматизации — это порядок
 * работы: сначала готовят вопросы, потом рассылают, потом поручают
 * правилу. Раздел открывается на шаблонах, потому что без них
 * остальные две вкладки пусты по построению.
 *
 * У трёх вкладок одна оболочка: шапка с фотографией, вкладки, строка
 * отбора со сводками справа и широкая таблица. Меняются только колонки
 * и данные — три разные геометрии для трёх списков одного раздела
 * читались бы как три разных продукта.
 *
 * Опрос ИМЕННОЙ, и модуль этого не прячет: рядом с ответом стоит
 * фамилия. Анонимный опрос — другой продукт с другими обещаниями.
 */

import { useCallback, useMemo, useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { AppShell } from '../components/AppShell';
import { SlideTabs } from '../components/SlideTabs';
import { AppIcon } from '../components/AppIcon';
import { AppPopover, AppSelectField } from '../components/AppSelect';
import {
  Confirm,
  Failed,
  Field,
  Refusal,
  useSaving,
} from '../components/admin/Parts';
import { useBlock, type Block } from '../features/dashboard/data';
import { useSession } from '../features/auth/session';
import {
  AUDIENCE,
  DEFAULT_TAB,
  State,
  TABS,
  TEMPLATE_STATUS,
  TRIGGER,
  atMoment,
  campaignTone,
  longMoment,
  peopleLine,
  plural,
  questionsLine,
  templatesLine,
  whenLine,
  type SurveyTab,
} from '../features/surveys/model';
import '../styles/admin.css';
import '../styles/surveys.css';

export { plural };

/**
 * Отбор по периоду.
 *
 * Считается от сегодняшнего дня назад. «Весь период» — без границы:
 * отбор, который нельзя снять, прячет строки молча.
 */
const PERIODS: Array<[string, string, number]> = [
  ['', 'Весь период', 0],
  ['7', 'За 7 дней', 7],
  ['30', 'За 30 дней', 30],
  ['90', 'За 3 месяца', 90],
];

function since(period: string): number | null {
  const days = PERIODS.find(([key]) => key === period)?.[2] ?? 0;
  return days ? Date.now() - days * 86_400_000 : null;
}

function PeriodPicker({ value, onChange }: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <AppSelectField label="Весь период" value={value} onChange={onChange}>
      {PERIODS.map(([key, title]) => (
        <option key={key} value={key}>{title}</option>
      ))}
    </AppSelectField>
  );
}

/** Какая вкладка открыта — решает адрес, а не состояние страницы. */
function tabOf(pathname: string): SurveyTab {
  if (pathname.startsWith('/surveys/campaigns')) return 'campaigns';
  if (pathname.startsWith('/surveys/automations')) return 'automations';
  return DEFAULT_TAB;
}

// Подзаголовок один на весь раздел: он говорит, что такое «Опросы», а
// не что на вкладке, — вкладку и так видно по подчёркиванию.
const ABOUT: Record<SurveyTab, string> = {
  templates: 'Подготовьте вопросы заранее и используйте их в рассылках и автоматизациях',
  campaigns: 'Подготовьте вопросы заранее и используйте их в рассылках и автоматизациях',
  automations: 'Подготовьте вопросы заранее и используйте их в рассылках и автоматизациях',
};

const ACTION: Record<SurveyTab, [string, string, 'plus' | 'send']> = {
  templates: ['Создать шаблон', '/surveys/templates/new', 'plus'],
  campaigns: ['Создать рассылку', '/surveys/campaigns/new', 'send'],
  automations: ['Создать автоматизацию', '/surveys/automations/new', 'plus'],
};

type TemplateSort = 'recent' | 'oldest' | 'title';

/** Порядок списка. По умолчанию — свежие сверху: их и ищут. */
const SORTS: Array<[TemplateSort, string]> = [
  ['recent', 'Сначала недавно изменённые'],
  ['oldest', 'Сначала давно изменённые'],
  ['title', 'По названию'],
];

function sortTemplates(
  rows: api.SurveyTemplate[], sort: TemplateSort,
): api.SurveyTemplate[] {
  const copy = [...rows];
  if (sort === 'title') {
    return copy.sort((a, b) => a.title.localeCompare(b.title, 'ru'));
  }
  copy.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  return sort === 'oldest' ? copy.reverse() : copy;
}

export function SurveysPage() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const tab = tabOf(pathname);
  const session = useSession();
  const zone = session.status === 'authenticated' ? session.user.timezone : '';

  const [params, setParams] = useSearchParams();
  const search = params.get('search') ?? '';
  const status = params.get('status') ?? '';
  const period = params.get('period') ?? '';
  const sort = (SORTS.find(([key]) => key === params.get('sort'))?.[0]) ?? 'recent';

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

  // Все три загрузки идут всегда, а не по вкладке: число на вкладке —
  // утверждение, и «0 рассылок» на вкладке шаблонов было бы неправдой.
  // Списки короткие, лишние два запроса тут ничего не стоят.
  const [templates, reloadTemplates] = useBlock(
    (signal) => api.surveyTemplates({}, signal),
    `surveys-templates|${attempt}`,
    true,
  );
  // Архив — отдельным запросом и только когда о нём спросили: в работе
  // архивных нет, и тянуть их на каждое открытие раздела незачем.
  const archivedView = tab === 'templates' && status === 'ARCHIVED';
  const [archived, reloadArchived] = useBlock(
    (signal) => api.surveyTemplates({ status: 'ARCHIVED' }, signal),
    `surveys-templates-archived|${attempt}`,
    archivedView,
  );
  const [campaigns, reloadCampaigns] = useBlock(
    (signal) => api.surveyCampaigns({}, signal),
    `surveys-campaigns|${attempt}`,
    true,
  );
  const [rules, reloadRules] = useBlock(
    (signal) => api.surveyAutomations(signal),
    `surveys-automations|${attempt}`,
    true,
  );

  const again = useCallback(() => {
    setAttempt((n) => n + 1);
    reloadTemplates();
    reloadArchived();
    reloadCampaigns();
    reloadRules();
  }, [reloadArchived, reloadCampaigns, reloadRules, reloadTemplates]);

  const templateRows = templates.state === 'ready' ? templates.data.items : [];
  const archivedRows = archived.state === 'ready' ? archived.data.items : [];
  const campaignRows = campaigns.state === 'ready' ? campaigns.data.items : [];
  const ruleRows = rules.state === 'ready' ? rules.data.items : [];

  // Что видит человек в списке шаблонов: поиск по названию и описанию
  // и отбор по состоянию. Считается здесь, а не в самом списке: то же
  // число стоит на вкладке, и два места обязаны называть одно число.
  const shownTemplates = useMemo(() => {
    const source = archivedView ? archivedRows : templateRows;
    const needle = search.trim().toLowerCase();
    const picked = source.filter((one) => {
      if (status && status !== 'ARCHIVED' && one.status !== status) return false;
      if (!needle) return true;
      return one.title.toLowerCase().includes(needle)
        || (one.description ?? '').toLowerCase().includes(needle);
    });
    return sortTemplates(picked, sort);
  }, [archivedRows, archivedView, search, sort, status, templateRows]);

  // Рассылать можно только по опубликованному шаблону: чужой черновик
  // правят прямо сейчас, и отправить по нему — значит спросить людей
  // о том, о чём спрашивать ещё не собирались.
  const ready = templateRows.filter((one) => one.status === 'PUBLISHED');
  const blocked = ready.length === 0 && tab !== 'templates';

  const counts: Record<SurveyTab, number> = {
    templates: shownTemplates.length,
    campaigns: campaignRows.length,
    automations: ruleRows.length,
  };

  const [label, where, icon] = ACTION[tab];
  const here = TABS.find((one) => one.key === tab)?.title ?? '';
  // Хлебная крошка говорит, где человек стоит сейчас, а не в каком
  // разделе он вообще: вкладка — это место, а не состояние страницы.
  const crumb = tab === 'templates' ? 'Опросы' : `Опросы / ${here}`;

  return (
    <AppShell breadcrumb={crumb} section="surveys">
      <div className="sv-page">
        {/* Весь раздел — один белый лист: путь, заголовок, вкладки и
            список внутри него. Отдельные плашки поверх фотографии
            читались как разные экраны одного раздела. */}
        {/* На шаблонах лист не растёт за край окна: длинный список
            прокручивается внутри, а заголовок, вкладки, поиск и
            пояснение остаются на месте. */}
        {/* Лист один и той же высоты на всех вкладках: при смене вкладки
            он не пересоздаётся и не меняет размер — меняется только то,
            что внутри места под вкладку. */}
        <section className="sv-sheet sv-sheet--list">
          <p className="sv-crumbs">
            <Link to="/">Рабочее пространство</Link>
            <i aria-hidden="true">/</i>
            {tab === 'templates' ? (
              <b>Опросы</b>
            ) : (
              <>
                <Link to="/surveys">Опросы</Link>
                <i aria-hidden="true">/</i>
                <b>{here}</b>
              </>
            )}
          </p>

          <header className="sv-top">
            <div>
              <h1 className="sv-top__title">Опросы</h1>
              <p className="sv-top__about">{ABOUT[tab]}</p>
            </div>
            {/* Контурная, а не залитая: на странице списка главное — сам
                список, а кнопка создания — одно из действий рядом. */}
            <button type="button" className="sv-create"
                    disabled={blocked}
                    title={blocked ? 'Сначала нужен опубликованный шаблон' : undefined}
                    onClick={() => navigate(where)}>
              <AppIcon name={icon} size={16} /> {label}
            </button>
          </header>

          <SlideTabs
            label="Разделы опросов"
            value={tab}
            items={TABS.map((one) => ({ key: one.key, title: one.title, count: counts[one.key] }))}
            onPick={(key) => navigate(TABS.find((one) => one.key === key)?.to ?? '/surveys')}
            classes={{ list: 'sv-tabs sv-tabs--slide', tab: 'sv-tab', on: 'sv-tab--on', count: 'sv-tab__count', ink: 'sv-tabs__ink' }}
          />

          <div className="sv-pane-place">
            {/* Ключ — у содержимого, а не у места: новая вкладка мягко
                проявляется внутри того же неподвижного блока. */}
            <div key={tab} className={tab === 'templates' ? 'sv-pane sv-pane--fit' : 'sv-pane'}
                 role="tabpanel" aria-label={here}>

          {tab === 'templates' && (
            <Templates
              block={archivedView ? archived : templates}
              any={templateRows.length > 0}
              shown={shownTemplates}
              zone={zone}
              search={search}
              status={status}
              sort={sort}
              onSearch={(value) => patch({ search: value || null })}
              onStatus={(value) => patch({ status: value || null })}
              onSort={(value) => patch({ sort: value === 'recent' ? null : value })}
              // Одним изменением адреса: два подряд читают один и тот же
              // прежний адрес, и второе возвращало то, что сняло первое.
              onReset={() => patch({ search: null, status: null })}
              onRetry={archivedView ? reloadArchived : reloadTemplates}
              onChanged={again}
            />
          )}

          {tab === 'campaigns' && (
            <Campaigns
              block={campaigns}
              rows={campaignRows}
              zone={zone}
              ready={ready.length}
              search={search}
              status={status}
              period={period}
              onSearch={(value) => patch({ search: value || null })}
              onStatus={(value) => patch({ status: value || null })}
              onPeriod={(value) => patch({ period: value || null })}
              onReset={() => patch({ search: null, status: null, period: null })}
              onRetry={reloadCampaigns}
            />
          )}

          {tab === 'automations' && (
            <Automations
              block={rules}
              rows={ruleRows}
              ready={ready.length}
              search={search}
              status={status}
              event={params.get('event') ?? ''}
              onSearch={(value) => patch({ search: value || null })}
              onStatus={(value) => patch({ status: value || null })}
              onEvent={(value) => patch({ event: value || null })}
              onReset={() => patch({ search: null, status: null, event: null })}
              onRetry={reloadRules}
              onChanged={again}
            />
          )}
            </div>
          </div>
        </section>
      </div>
    </AppShell>
  );
}

/** Строки-заготовки на время загрузки: высота списка та же, что будет. */
function Skeleton() {
  return (
    <div className="sv-skeleton" aria-label="Загружаем список">
      {[0, 1, 2, 3, 4].map((one) => (
        <div key={one} className="sv-skeleton__row">
          <span className="sv-skeleton__mark" />
          <span className="sv-skeleton__lines">
            <span className="sv-skeleton__bar" />
            <span className="sv-skeleton__bar sv-skeleton__bar--short" />
          </span>
          <span className="sv-skeleton__bar sv-skeleton__bar--tail" />
        </div>
      ))}
    </div>
  );
}

/** Общая заготовка состояний загрузки для всех трёх списков. */
function Shell({ block, onRetry, children }: {
  block: Block<unknown>;
  onRetry: () => void;
  children: React.ReactNode;
}) {
  if (block.state === 'loading') return <Skeleton />;
  if (block.state === 'denied') {
    return <p className="empty">Раздел «Опросы» закрыт правами.</p>;
  }
  if (block.state === 'error') return <Failed onRetry={onRetry} />;
  return <>{children}</>;
}

// --- шаблоны ------------------------------------------------------------------

/** Состояние шаблона словами и точкой. Архив перевешивает редакцию. */
function templateState(row: api.SurveyTemplate): { tone: 'ok' | 'off'; title: string } {
  if (row.archived_at) return { tone: 'off', title: TEMPLATE_STATUS.ARCHIVED };
  return row.status === 'PUBLISHED'
    ? { tone: 'ok', title: TEMPLATE_STATUS.PUBLISHED }
    : { tone: 'off', title: TEMPLATE_STATUS.DRAFT };
}

function Templates({
  block, any, shown, zone, search, status, sort,
  onSearch, onStatus, onSort, onReset, onRetry, onChanged,
}: {
  block: Block<unknown>;
  /** Есть ли у организации хоть один рабочий шаблон. */
  any: boolean;
  shown: api.SurveyTemplate[];
  zone: string;
  search: string;
  status: string;
  sort: TemplateSort;
  onSearch: (value: string) => void;
  onStatus: (value: string) => void;
  onSort: (value: TemplateSort) => void;
  onReset: () => void;
  onRetry: () => void;
  onChanged: () => void;
}) {
  const navigate = useNavigate();
  const [menu, setMenu] = useState<string | null>(null);
  const [sorting, setSorting] = useState(false);
  const [archiving, setArchiving] = useState<api.SurveyTemplate | null>(null);
  const [removing, setRemoving] = useState<api.SurveyTemplate | null>(null);
  const [renaming, setRenaming] = useState<api.SurveyTemplate | null>(null);
  const act = useSaving();

  const filtered = Boolean(search || status);
  const reset = onReset;
  const done = () => {
    setArchiving(null);
    setRemoving(null);
    setRenaming(null);
    onChanged();
  };
  const open = (row: api.SurveyTemplate) => navigate(`/surveys/templates/${row.id}`);

  // Шаблонов нет вовсе — искать не в чем. Пустой лист с поиском над
  // ним выглядел бы как «ничего не нашлось», а это другое: пока не
  // создали.
  const nothing = block.state === 'ready' && !any && !filtered;

  return (
    <>
      <div className="sv-box">
        <Shell block={block} onRetry={onRetry}>
          {nothing ? (
            <div className="sv-empty">
              <span className="sv-empty__mark" aria-hidden="true">
                <AppIcon name="doc" size={20} />
              </span>
              <p className="sv-empty__title">Шаблонов пока нет</p>
              <p className="sv-empty__about">
                Создайте первый шаблон — затем его можно будет отправить
                сотрудникам или подключить к автоматизации.
              </p>
              <button type="button" className="sv-create"
                      onClick={() => navigate('/surveys/templates/new')}>
                <AppIcon name="plus" size={18} /> Создать шаблон
              </button>
            </div>
          ) : (
            <>
              <div className="sv-box__tools">
                <label className="sv-find">
                  <AppIcon name="search" size={16} />
                  <input type="search" value={search}
                         placeholder="Найти шаблон" aria-label="Найти шаблон"
                         onChange={(event) => onSearch(event.target.value)} />
                </label>
                <AppSelectField label="Все статусы" value={status} onChange={onStatus}
                                className="sv-status">
                  <option value="">Все статусы</option>
                  <option value="DRAFT">Черновики</option>
                  <option value="PUBLISHED">Опубликованные</option>
                  <option value="ARCHIVED">Архивные</option>
                </AppSelectField>
              </div>

              <div className="sv-box__bar">
                <span className="sv-box__count">{templatesLine(shown.length)}</span>
                {shown.length > 1 && (
                  <span className="sv-sort">
                    <button type="button" className="sv-sort__button"
                            aria-haspopup="listbox" aria-expanded={sorting}
                            onClick={() => setSorting((was) => !was)}>
                      <AppIcon name="filter-list" size={16} />
                      {SORTS.find(([key]) => key === sort)?.[1]}
                      <AppIcon name="chevron" size={16} />
                    </button>
                    <AppPopover open={sorting} onClose={() => setSorting(false)}
                                className="sv-menu sv-menu--sort">
                      {SORTS.map(([key, title]) => (
                        <button key={key} type="button" role="option"
                                aria-selected={key === sort}
                                className={key === sort ? 'sv-menu__on' : undefined}
                                onClick={() => { setSorting(false); onSort(key); }}>
                          {title}
                        </button>
                      ))}
                    </AppPopover>
                  </span>
                )}
              </div>

              {shown.length === 0 ? (
                <div className="sv-empty sv-empty--found">
                  <p className="sv-empty__title">По этим условиям шаблонов нет</p>
                  <p className="sv-empty__about">
                    Попробуйте изменить поиск или сбросить фильтры.
                  </p>
                  <button type="button" className="sv-create" onClick={reset}>
                    Сбросить фильтры
                  </button>
                </div>
              ) : (
                <ul className="sv-rows">
                  {shown.map((row) => {
                    const state = templateState(row);
                    const used = (row.campaigns_count ?? 0) > 0;
                    const draft = row.status === 'DRAFT' && !row.archived_at;
                    const live = row.status === 'PUBLISHED' && !row.archived_at;
                    return (
                      <li key={row.id} className="sv-row" tabIndex={0}
                          onClick={() => open(row)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') open(row);
                          }}>
                        <span className="sv-row__mark" aria-hidden="true">
                          <AppIcon name="doc" size={18} />
                        </span>
                        <span className="sv-row__name">
                          <b>{row.title}</b>
                          {row.description && <small>{row.description}</small>}
                        </span>
                        <span className="sv-row__count">
                          {questionsLine(row.questions.length)}
                        </span>
                        <State tone={state.tone}>
                          {/* Редакция стоит всегда, и у черновика тоже:
                              правило «иногда показываем номер» читается
                              как разница между строками, которой нет. */}
                          <span>{state.title} · v{row.version}</span>
                        </State>
                        <span className="sv-row__when">
                          Обновлён {atMoment(row.updated_at, zone)}
                        </span>
                        <span className="sv-row__menu"
                              onClick={(event) => event.stopPropagation()}
                              onKeyDown={(event) => event.stopPropagation()}>
                          <button type="button" className="tool tool--ghost"
                                  aria-label={`Действия: ${row.title}`}
                                  aria-expanded={menu === row.id}
                                  onClick={() => setMenu(menu === row.id ? null : row.id)}>
                            <AppIcon name="more" size={16} />
                          </button>
                          <AppPopover open={menu === row.id}
                                      onClose={() => setMenu(null)}
                                      className="sv-menu">
                            <button type="button"
                                    onClick={() => { setMenu(null); open(row); }}>
                              Открыть
                            </button>
                            {draft && (
                              <button type="button"
                                      onClick={() => { setMenu(null); setRenaming(row); }}>
                                Переименовать
                              </button>
                            )}
                            {live && (
                              <button type="button"
                                      onClick={() => {
                                        setMenu(null);
                                        let next: api.SurveyTemplate | null = null;
                                        void act.run(
                                          async () => {
                                            next = await api.newSurveyTemplateVersion(row.id);
                                          },
                                          () => {
                                            if (next) {
                                              navigate(
                                                `/surveys/templates/${(next as api.SurveyTemplate).id}`,
                                              );
                                            }
                                          },
                                        );
                                      }}>
                                Создать новую версию
                              </button>
                            )}
                            <button type="button"
                                    onClick={() => {
                                      setMenu(null);
                                      void act.run(
                                        () => api.copySurveyTemplate(row.id),
                                        onChanged,
                                      );
                                    }}>
                              Дублировать
                            </button>
                            {/* Удалить можно только черновик, по которому
                                не спрашивали: у остального есть ответы, и
                                без шаблона они потеряют свои вопросы. */}
                            {draft && !used && (
                              <button type="button" className="sv-menu__bad"
                                      onClick={() => { setMenu(null); setRemoving(row); }}>
                                Удалить
                              </button>
                            )}
                            {(live || (draft && used)) && (
                              <button type="button" className="sv-menu__bad"
                                      onClick={() => { setMenu(null); setArchiving(row); }}>
                                Архивировать
                              </button>
                            )}
                          </AppPopover>
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
            </>
          )}
        </Shell>
        <Refusal text={act.refusal} />
      </div>

      <p className="sv-note">
        <AppIcon name="info" size={16} />
        Опубликованный шаблон нельзя изменить. Создайте новую версию, чтобы
        ответы из прошлых рассылок оставались сопоставимыми.
      </p>

      {renaming && (
        <Rename
          template={renaming}
          busy={act.busy}
          refusal={act.refusal}
          onCancel={() => setRenaming(null)}
          onSave={(title) => {
            void act.run(
              () => api.updateSurveyTemplate(renaming.id, { title }),
              done,
            );
          }}
        />
      )}

      {archiving && (
        <Confirm
          title="Архивировать шаблон"
          what={`«${archiving.title}» пропадёт из списка и из выбора при создании рассылки.`}
          consequence="Уже отправленные рассылки и полученные ответы останутся: архив не стирает историю. Найти шаблон можно в отборе «Архивные»."
          confirmLabel="Архивировать"
          refusal={act.refusal}
          busy={act.busy}
          onCancel={() => setArchiving(null)}
          onConfirm={() => {
            void act.run(() => api.archiveSurveyTemplate(archiving.id), done);
          }}
        />
      )}

      {removing && (
        <Confirm
          title="Удалить шаблон"
          what={`Черновик «${removing.title}» и его вопросы удалятся.`}
          consequence="Вернуть его будет нельзя. По нему ещё не спрашивали, поэтому ответов он не держит."
          confirmLabel="Удалить"
          refusal={act.refusal}
          busy={act.busy}
          onCancel={() => setRemoving(null)}
          onConfirm={() => {
            void act.run(() => api.deleteSurveyTemplate(removing.id), done);
          }}
        />
      )}
    </>
  );
}

/**
 * Переименование черновика.
 *
 * Отдельное окно, а не правка в строке: название шаблона видит сотрудник
 * в сообщении бота, и менять его случайным попаданием по тексту нельзя.
 */
function Rename({ template, busy, refusal, onCancel, onSave }: {
  template: api.SurveyTemplate;
  busy: boolean;
  refusal: string | null;
  onCancel: () => void;
  onSave: (title: string) => void;
}) {
  const [title, setTitle] = useState(template.title);
  const clean = title.trim();

  return (
    <div className="adm-ask" role="dialog" aria-modal="true"
         aria-label="Переименовать шаблон">
      <div className="adm-ask__box">
        <h2 className="adm-ask__title">Переименовать шаблон</h2>
        <p className="adm-ask__what">
          Название увидит сотрудник в сообщении бота.
        </p>
        <Field label="Название">
          <input className="input" value={title} maxLength={255} autoFocus
                 onChange={(event) => setTitle(event.target.value)}
                 onKeyDown={(event) => {
                   if (event.key === 'Enter' && clean) onSave(clean);
                 }} />
        </Field>
        <Refusal text={refusal} />
        <div className="adm-ask__tools">
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            Отмена
          </button>
          <button type="button" className="btn btn--primary"
                  disabled={busy || !clean || clean === template.title}
                  onClick={() => onSave(clean)}>
            {busy ? 'Сохраняем…' : 'Сохранить'}
          </button>
        </div>
      </div>
    </div>
  );
}

// --- рассылки -----------------------------------------------------------------

/** Состояние рассылки словами — такими, какими о ней говорят. */
const CAMPAIGN_WORD: Record<api.SurveyCampaign['status'], string> = {
  DRAFT: 'Черновик',
  SCHEDULED: 'Запланирована',
  ACTIVE: 'Идёт',
  FINISHED: 'Завершена',
  CANCELLED: 'Отменена',
};

function Campaigns({
  block, rows, zone, ready, search, status, period,
  onSearch, onStatus, onPeriod, onReset, onRetry,
}: {
  block: Block<unknown>;
  rows: api.SurveyCampaign[];
  zone: string;
  ready: number;
  search: string;
  status: string;
  period: string;
  onSearch: (value: string) => void;
  onStatus: (value: string) => void;
  onPeriod: (value: string) => void;
  onReset: () => void;
  onRetry: () => void;
}) {
  const navigate = useNavigate();

  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const edge = since(period);
    return rows.filter((one) => {
      if (status && one.status !== status) return false;
      // Период считается по дню отправки, а у неотправленной — по
      // назначенной дате: иначе запланированная на завтра пропадала бы
      // из отбора «за 7 дней», хотя именно её и ищут.
      const when = one.sent_at ?? one.scheduled_at ?? one.created_at;
      if (edge && Date.parse(when) < edge) return false;
      if (!needle) return true;
      return one.title.toLowerCase().includes(needle)
        || (one.template_description ?? '').toLowerCase().includes(needle);
    });
  }, [period, rows, search, status]);

  // Сколько человек ещё не ответили по идущим рассылкам — та работа,
  // ради которой кадровик открыл вкладку.
  const waiting = rows
    .filter((one) => one.status === 'ACTIVE')
    .reduce((sum, one) => sum + Math.max((one.total ?? 0) - (one.done ?? 0), 0), 0);
  const next = rows
    .filter((one) => one.status === 'SCHEDULED' || (one.next_send_at && one.status !== 'CANCELLED'))
    .map((one) => (one.next_send_at ?? one.scheduled_at) as string)
    .filter(Boolean)
    .sort()[0];

  const nothing = block.state === 'ready' && rows.length === 0;

  return (
    <>
      {!nothing && (
        <div className="sv-box sv-box--tools">
          <div className="sv-filters">
            <label className="sv-find">
              <AppIcon name="search" size={16} />
              <input type="search" value={search}
                     placeholder="Найти рассылку" aria-label="Найти рассылку"
                     onChange={(event) => onSearch(event.target.value)} />
            </label>
            <AppSelectField label="Все статусы" value={status} onChange={onStatus}
                            className="sv-status">
              <option value="">Все статусы</option>
              <option value="ACTIVE">Идут</option>
              <option value="SCHEDULED">Запланированы</option>
              <option value="FINISHED">Завершены</option>
              <option value="CANCELLED">Отменены</option>
            </AppSelectField>
            <span className="sv-period">
              <AppIcon name="calendar" size={16} />
              <PeriodPicker value={period} onChange={onPeriod} />
            </span>
            <span className="sv-filters__gap" />
            <p className="sv-tally sv-tally--warn">
              <AppIcon name="alert" size={18} />
              <span>
                Требуют внимания
                <b>
                  {waiting > 0
                    ? `${waiting} не ${plural(waiting, ['ответил', 'ответили', 'ответили'])}`
                    : 'Все ответили'}
                </b>
              </span>
            </p>
            <p className="sv-tally">
              <AppIcon name="calendar" size={18} />
              <span>
                Ближайшая отправка
                <b>{next ? atMoment(next, zone) : 'Не запланирована'}</b>
              </span>
            </p>
          </div>
        </div>
      )}

      <Shell block={block} onRetry={onRetry}>
        {nothing ? (
          <div className="sv-box">
            <div className="sv-empty">
              <span className="sv-empty__mark" aria-hidden="true">
                <AppIcon name="send" size={20} />
              </span>
              <p className="sv-empty__title">Рассылок пока нет</p>
              <p className="sv-empty__about">
                {ready === 0
                  ? 'Сначала опубликуйте шаблон: по черновику рассылать нельзя, его правят прямо сейчас.'
                  : 'Выберите шаблон и круг получателей — каждому придёт одно сообщение в Telegram с кнопкой опроса.'}
              </p>
              <button type="button" className="sv-create"
                      onClick={() => navigate(ready === 0 ? '/surveys' : '/surveys/campaigns/new')}>
                <AppIcon name={ready === 0 ? 'doc' : 'send'} size={16} />
                {ready === 0 ? 'К шаблонам' : 'Создать рассылку'}
              </button>
            </div>
          </div>
        ) : shown.length === 0 ? (
          <div className="sv-box">
            <div className="sv-empty sv-empty--found">
              <p className="sv-empty__title">По этим условиям рассылок нет</p>
              <p className="sv-empty__about">
                Попробуйте изменить поиск или сбросить фильтры.
              </p>
              <button type="button" className="sv-create" onClick={onReset}>
                Сбросить фильтры
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="sv-heads sv-grid sv-grid--campaign" aria-hidden="true">
              <span className="sv-heads__first">Рассылка</span>
              <span className="sv-col-people">Получатели</span>
              <span>Прогресс</span>
              <span className="sv-col-when">Отправка</span>
              <span>Состояние</span>
              <span />
            </div>
            <ul className="sv-box sv-list" aria-label="Рассылки опросов">
              {shown.map((row) => {
                const total = row.total ?? 0;
                const done = row.done ?? 0;
                const sent = Boolean(row.sent_at);
                const share = total > 0 ? Math.round((done / total) * 100) : 0;
                const people = sent ? total : (row.planned ?? null);
                const open = () => navigate(`/surveys/campaigns/${row.id}`);
                return (
                  <li key={row.id} className="sv-line sv-grid sv-grid--campaign"
                      tabIndex={0} onClick={open}
                      onKeyDown={(event) => { if (event.key === 'Enter') open(); }}>
                    <span className="sv-row__mark" aria-hidden="true">
                      <AppIcon name={row.automation_id ? 'refresh' : 'chat'} size={18} />
                    </span>
                    <span className="sv-row__name">
                      <b>{row.title}</b>
                      <small>
                        {row.automation_id
                          ? 'Отправлена автоматизацией'
                          : row.template_description || `Шаблон «${row.template_title}»`}
                      </small>
                    </span>
                    <span className="sv-line__muted sv-col-people">
                      {people !== null ? peopleLine(people) : AUDIENCE[row.audience_kind]}
                    </span>
                    <span className="sv-meter">
                      {/* Пока не отправляли — полоса пустая и доли нет:
                          «0 %» означало бы, что никто не ответил. */}
                      <span className="sv-meter__text">
                        {sent && total > 0
                          ? `${done} из ${total}`
                          : row.status === 'SCHEDULED' && row.scheduled_at
                            ? `Запланирована на ${dayOnly(row.scheduled_at, zone)}`
                            : 'Ещё не отправлена'}
                      </span>
                      <span className="sv-meter__bar">
                        <span className="sv-meter__track">
                          <span className="sv-meter__fill"
                                style={{ width: sent ? `${share}%` : '0%' }} />
                        </span>
                        {sent && total > 0 && (
                          <span className="sv-meter__share">{share}%</span>
                        )}
                      </span>
                    </span>
                    <span className="sv-line__muted sv-col-when">
                      {row.sent_at
                        ? shortMoment(row.sent_at, zone)
                        : row.scheduled_at
                          ? shortMoment(row.scheduled_at, zone)
                          : '—'}
                    </span>
                    <State tone={campaignTone(row.status)}>
                      {CAMPAIGN_WORD[row.status] ?? row.status}
                    </State>
                    <AppIcon name="next" size={18} className="sv-go" />
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </Shell>
    </>
  );
}

/** «25 сентября» — день без времени, для «Запланирована на …». */
function dayOnly(at: string, zone: string): string {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric', month: 'long', ...(zone ? { timeZone: zone } : {}),
  }).format(date);
}

/** «12 сентября, 10:00» — компактно, для колонки списка. */
function shortMoment(at: string, zone: string): string {
  return longMoment(at, zone);
}

// --- автоматизации ------------------------------------------------------------

/** Что стоит за событием — одной строкой под его названием. */
const TRIGGER_ABOUT: Record<api.SurveyTriggerKind, string> = {
  PROBATION_END: 'Последний день стажировки из карточки',
  FIRST_DAY: 'Дата приёма из карточки',
  DAYS_AFTER_HIRE: 'Отсчёт от даты приёма',
  BIRTHDAY: 'Дата рождения из карточки',
  SCHEDULE: 'Всем, кто подходит под правило',
};

function Automations({
  block, rows, ready, search, status, event,
  onSearch, onStatus, onEvent, onReset, onRetry, onChanged,
}: {
  block: Block<unknown>;
  rows: api.SurveyAutomation[];
  ready: number;
  search: string;
  status: string;
  event: string;
  onSearch: (value: string) => void;
  onStatus: (value: string) => void;
  onEvent: (value: string) => void;
  onReset: () => void;
  onRetry: () => void;
  onChanged: () => void;
}) {
  const navigate = useNavigate();
  const [menu, setMenu] = useState<string | null>(null);
  const [removing, setRemoving] = useState<api.SurveyAutomation | null>(null);
  const act = useSaving();

  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return rows.filter((one) => {
      if (status === 'on' && !one.is_active) return false;
      if (status === 'off' && one.is_active) return false;
      if (event && one.trigger_kind !== event) return false;
      if (!needle) return true;
      return one.title.toLowerCase().includes(needle)
        || one.template_title.toLowerCase().includes(needle);
    });
  }, [event, rows, search, status]);

  const nothing = block.state === 'ready' && rows.length === 0;
  const open = (row: api.SurveyAutomation) => navigate(`/surveys/automations/${row.id}`);

  return (
    <>
      <Shell block={block} onRetry={onRetry}>
        {nothing ? (
          <div className="sv-box">
            <div className="sv-empty">
              <span className="sv-empty__mark" aria-hidden="true">
                <AppIcon name="refresh" size={20} />
              </span>
              <p className="sv-empty__title">Автоматизаций пока нет</p>
              <p className="sv-empty__about">
                {ready === 0
                  ? 'Сначала опубликуйте шаблон: правило работает без присмотра и черновик рассылать не вправе.'
                  : 'Правило настраивают один раз — дальше оно само отправляет опрос по событию: например, на следующий день после окончания стажировки.'}
              </p>
              <button type="button" className="sv-create"
                      onClick={() => navigate(ready === 0 ? '/surveys' : '/surveys/automations/new')}>
                <AppIcon name={ready === 0 ? 'doc' : 'plus'} size={16} />
                {ready === 0 ? 'К шаблонам' : 'Создать автоматизацию'}
              </button>
            </div>
          </div>
        ) : (
          <div className="sv-box">
            <div className="sv-box__tools">
              <label className="sv-find">
                <AppIcon name="search" size={16} />
                <input type="search" value={search}
                       placeholder="Найти автоматизацию" aria-label="Найти автоматизацию"
                       onChange={(ev) => onSearch(ev.target.value)} />
              </label>
              <AppSelectField label="Все статусы" value={status} onChange={onStatus}
                              className="sv-status">
                <option value="">Все статусы</option>
                <option value="on">Включённые</option>
                <option value="off">Выключенные</option>
              </AppSelectField>
              <AppSelectField label="Все события" value={event} onChange={onEvent}
                              className="sv-status sv-status--wide">
                <option value="">Все события</option>
                {Object.entries(TRIGGER).map(([key, title]) => (
                  <option key={key} value={key}>{title}</option>
                ))}
              </AppSelectField>
              <span className="sv-filters__gap" />
              <p className="sv-lead">
                <AppIcon name="info" size={16} />
                Автоматизация отправит опрос сама, когда произойдёт событие.
              </p>
            </div>

            {shown.length === 0 ? (
              <div className="sv-empty sv-empty--found">
                <p className="sv-empty__title">По этим условиям правил нет</p>
                <p className="sv-empty__about">
                  Попробуйте изменить поиск или сбросить фильтры.
                </p>
                <button type="button" className="sv-create" onClick={onReset}>
                  Сбросить фильтры
                </button>
              </div>
            ) : (
              <>
                <div className="sv-heads sv-heads--inner sv-grid sv-grid--rule" aria-hidden="true">
                  <span className="sv-heads__first">Автоматизация</span>
                  <span>Событие</span>
                  <span className="sv-col-when">Когда отправить</span>
                  <span>Статус</span>
                  <span />
                  <span />
                </div>
                <ul className="sv-list sv-list--flat" aria-label="Автоматизации опросов">
                  {shown.map((row) => (
                    <li key={row.id} className="sv-line sv-grid sv-grid--rule" tabIndex={0}
                        onClick={() => open(row)}
                        onKeyDown={(ev) => { if (ev.key === 'Enter') open(row); }}>
                      <span className="sv-row__mark" aria-hidden="true">
                        <AppIcon name="refresh" size={18} />
                      </span>
                      <span className="sv-row__name">
                        <b>{row.title}</b>
                        <small>Шаблон «{row.template_title}»</small>
                      </span>
                      <span className="sv-row__name sv-row__name--plain">
                        <b>{TRIGGER[row.trigger_kind]}</b>
                        <small>{TRIGGER_ABOUT[row.trigger_kind]}</small>
                      </span>
                      <span className="sv-line__muted sv-col-when">{whenLine(row)}</span>
                      <span onClick={(ev) => ev.stopPropagation()}
                            onKeyDown={(ev) => ev.stopPropagation()}>
                        <label className="sv-switch">
                          <input type="checkbox" checked={row.is_active}
                                 aria-label={`Правило «${row.title}» включено`}
                                 onChange={(ev) => {
                                   void act.run(
                                     () => api.toggleSurveyAutomation(row.id, ev.target.checked),
                                     onChanged,
                                   );
                                 }} />
                          <span className="sv-switch__track" aria-hidden="true" />
                        </label>
                      </span>
                      <span className="sv-row__menu"
                            onClick={(ev) => ev.stopPropagation()}
                            onKeyDown={(ev) => ev.stopPropagation()}>
                        <button type="button" className="tool tool--ghost"
                                aria-label={`Действия: ${row.title}`}
                                aria-expanded={menu === row.id}
                                onClick={() => setMenu(menu === row.id ? null : row.id)}>
                          <AppIcon name="more" size={16} />
                        </button>
                        <AppPopover open={menu === row.id}
                                    onClose={() => setMenu(null)}
                                    className="sv-menu">
                          <button type="button"
                                  onClick={() => { setMenu(null); open(row); }}>
                            Открыть
                          </button>
                          <button type="button"
                                  onClick={() => {
                                    setMenu(null);
                                    navigate(`/surveys/automations/${row.id}?tab=settings`);
                                  }}>
                            Редактировать
                          </button>
                          <button type="button" className="sv-menu__bad"
                                  onClick={() => { setMenu(null); setRemoving(row); }}>
                            Удалить
                          </button>
                        </AppPopover>
                      </span>
                      <AppIcon name="next" size={18} className="sv-go" />
                    </li>
                  ))}
                </ul>
              </>
            )}

            <p className="sv-note sv-note--inner">
              <AppIcon name="info" size={16} />
              Перед отправкой система проверит, что сотрудник работает, привязан
              к Telegram и ещё не получал опрос по этому же событию.
            </p>
          </div>
        )}
      </Shell>
      <Refusal text={act.refusal} />

      {removing && (
        <Confirm
          title="Удалить автоматизацию"
          what={`Правило «${removing.title}» перестанет существовать.`}
          consequence="Если по нему уже были рассылки, сервер откажет: их историю нельзя потерять вместе с правилом. Такое правило выключают."
          confirmLabel="Удалить"
          refusal={act.refusal}
          busy={act.busy}
          onCancel={() => setRemoving(null)}
          onConfirm={() => {
            void act.run(
              () => api.deleteSurveyAutomation(removing.id),
              () => { setRemoving(null); onChanged(); },
            );
          }}
        />
      )}
    </>
  );
}
