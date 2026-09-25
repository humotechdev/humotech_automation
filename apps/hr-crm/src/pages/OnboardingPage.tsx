/**
 * «Ознакомления»: один лист, три вкладки — сотрудники, материалы, разделы.
 *
 * Лист, шапка и вкладки постоянные: при смене вкладки они не
 * пересоздаются и не меняют размер, линия под вкладкой переезжает, а
 * содержимое появляется мягко. Числа на вкладках грузятся один раз на
 * странице — и у неактивных вкладок они тоже видны.
 *
 * Ни одно число не считается здесь по своим правилам. Группы сотрудников
 * («требуют внимания», «ждут подключения», «не начали», «в процессе»,
 * «завершили»), сроки и просрочки считает сервер одним правилом — оно же
 * у чипа, у показателя и у колонки справа. Назначено и подтвердили у
 * материала — тоже с сервера: черновик никого не обязывает, и у него
 * «назначено» честно пустое.
 *
 * «Материал» — обязательный документ компании. Знакомство с компанией —
 * карточки, которые бот показывает первыми, — живёт в «Разделах»
 * закреплённой строкой: у него нет версий и согласия, только прочтение.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { messageFor } from '../api/errors';
import { AppShell, initials } from '../components/AppShell';
import { AppIcon, type AppIconName } from '../components/AppIcon';
import { AppPopover, Dropdown } from '../components/AppSelect';
import { SlideTabs } from '../components/SlideTabs';
import { useBlock, type Block } from '../features/dashboard/data';
import { useSession } from '../features/auth/session';
import {
  CategoryPanel, DocumentPanel, IntroPanel, NewMaterialPanel, PersonPanel,
  dateRu, daysUntil, materialsOf, plural, progressOf,
} from '../features/onboarding/panels';
import '../styles/admin.css';
import '../styles/onboarding.css';

type Tab = 'people' | 'materials' | 'sections';

const TABS: { key: Tab; title: string }[] = [
  { key: 'people', title: 'Сотрудники' },
  { key: 'materials', title: 'Материалы' },
  { key: 'sections', title: 'Разделы' },
];

/** Старые ссылки: `tab=documents` — это «Материалы». */
function tabOf(params: URLSearchParams): Tab {
  const raw = params.get('tab');
  if (raw === 'materials' || raw === 'documents') return 'materials';
  if (raw === 'sections') return 'sections';
  return 'people';
}

const GROUPS: { key: api.OnboardingGroup | 'all'; title: string }[] = [
  { key: 'all', title: 'Все' },
  { key: 'attention', title: 'Требуют внимания' },
  { key: 'not_started', title: 'Не начали' },
  { key: 'in_progress', title: 'В процессе' },
  { key: 'waiting', title: 'Ждут подключения к Telegram' },
  { key: 'done', title: 'Завершили' },
];

const REASON: Record<api.OnboardingReason, { title: string; tone: Tone; icon: AppIconName }> = {
  overdue: { title: 'Просрочен срок ознакомления', tone: 'red', icon: 'clock' },
  declined: { title: 'Отказ подтвердить материал', tone: 'red', icon: 'alert' },
  renewal: { title: 'Новая версия материала', tone: 'blue', icon: 'doc' },
  silent: { title: 'Нет ответа от сотрудника', tone: 'amber', icon: 'alert' },
};

type Tone = 'blue' | 'green' | 'amber' | 'red' | 'grey';

/** Состояние строки словами — без внутренних кодов. */
function stateOf(row: api.OnboardingRow): { title: string; tone: Tone } {
  const reasons = row.reasons ?? [];
  switch (row.group) {
    case 'done': return { title: 'Завершено', tone: 'green' };
    case 'attention':
      if (reasons.includes('overdue')) return { title: 'Просрочено', tone: 'red' };
      if (reasons.includes('declined')) return { title: 'Отказ подтвердить', tone: 'red' };
      if (reasons.includes('renewal')) return { title: 'Новая версия', tone: 'amber' };
      return { title: 'Требует внимания', tone: 'amber' };
    case 'waiting': return { title: 'Ждёт подключения', tone: 'grey' };
    case 'not_started': return { title: 'Не начато', tone: 'grey' };
    default: return { title: 'В процессе', tone: 'blue' };
  }
}

const PAGE = 25;

export function OnboardingPage() {
  const session = useSession();
  const zone = session.status === 'authenticated' ? session.user.timezone : '';
  const [params, setParams] = useSearchParams();
  const tab = tabOf(params);
  const [attempt, setAttempt] = useState(0);
  const refresh = useCallback(() => setAttempt((n) => n + 1), []);

  const patch = useCallback((changes: Record<string, string | null>) => {
    setParams((was) => {
      const next = new URLSearchParams(was);
      for (const [key, value] of Object.entries(changes)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      return next;
    }, { replace: true });
  }, [setParams]);

  // Числа на вкладках — один раз на странице, для всех трёх.
  const [counts] = useBlock((signal) => api.onboardingCounts(signal), `onb-counts|${attempt}`);
  const [documents] = useBlock((signal) => api.policyDocuments(signal), `onb-docs|${attempt}`);
  const [categories] = useBlock((signal) => api.policyCategories(signal), `onb-cats|${attempt}`);
  const [cards] = useBlock((signal) => api.onboardingSections(signal), `onb-cards|${attempt}`);
  const [directory] = useBlock(
    (signal) => Promise.all([api.offices(signal), api.departmentsPage({ limit: '200', status: 'ACTIVE' }, signal)])
      .then(([offices, departments]) => ({
        offices: offices.items.filter((one) => one.status === 'ACTIVE').map((one) => ({ id: one.id, name: one.name })),
        departments: departments.items.map((one) => ({ id: one.id, name: one.name })),
      })),
    'onb-directory',
  );

  const tabs = TABS.map((one) => ({
    ...one,
    count: one.key === 'people'
      ? (counts.state === 'ready' ? counts.data.groups?.all ?? counts.data['all'] ?? null : null)
      : one.key === 'materials'
        ? (documents.state === 'ready' ? documents.data.items.length : null)
        : (categories.state === 'ready' && cards.state === 'ready'
          ? categories.data.items.length + (cards.data.items.length ? 1 : 0) : null),
  }));

  const [panel, setPanel] = useState<Panel | null>(null);
  const picked = params.get('employee');
  const close = () => { setPanel(null); patch({ employee: null }); };

  const shared: Shared = {
    params, patch, attempt, refresh, zone, open: setPanel,
    documents, categories, cards,
    offices: directory.state === 'ready' ? directory.data.offices : [],
    departments: directory.state === 'ready' ? directory.data.departments : [],
  };
  const views: Record<Tab, View> = {
    people: usePeopleTab(tab === 'people', shared),
    materials: useMaterialsTab(tab === 'materials', shared),
    sections: useSectionsTab(tab === 'sections', shared),
  };
  const view = views[tab];

  return (
    <AppShell breadcrumb="Ознакомление" section="onboarding">
      <div className="on">
        <section className="on-sheet">
          <header className="on-head">
            <div className="on-head__text">
              <h1 className="on-head__title">Ознакомления</h1>
              <p className="on-head__sub">{view.sub}</p>
            </div>
            <div className="on-head__action">{view.action}</div>
          </header>

          <SlideTabs
            label="Разделы ознакомления"
            value={tab}
            items={tabs}
            onPick={(key) => patch({ tab: key === 'people' ? null : key, employee: null })}
            classes={{ list: 'on-tabs', tab: 'on-tab', on: 'on-tab--on', count: 'on-tab__count', ink: 'on-tabs__ink' }}
          />

          <div className="on-place">
            <div key={tab} className="on-pane" role="tabpanel" aria-label={TABS.find((one) => one.key === tab)?.title}>
              {view.body}
            </div>
          </div>
        </section>
      </div>

      {picked && !panel && (
        <PersonPanel id={picked} zone={zone} onClose={close} onChanged={refresh} />
      )}
      {panel?.kind === 'person' && <PersonPanel id={panel.id} zone={zone} onClose={close} onChanged={refresh} />}
      {panel?.kind === 'document' && documents.state === 'ready' && (
        <DocumentPanel id={panel.id} rows={documents.data.items}
                       categories={categories.state === 'ready' ? categories.data.items : []}
                       zone={zone} onClose={close} onChanged={refresh} />
      )}
      {panel?.kind === 'new-document' && (
        <NewMaterialPanel categories={categories.state === 'ready' ? categories.data.items : []}
                          onClose={close}
                          onCreated={(id) => { refresh(); setPanel({ kind: 'document', id }); }} />
      )}
      {panel?.kind === 'category' && (
        <CategoryPanel category={panel.category} onClose={close} onSaved={() => { close(); refresh(); }} />
      )}
      {panel?.kind === 'intro' && <IntroPanel onClose={close} onChanged={refresh} />}
    </AppShell>
  );
}

type Panel =
  | { kind: 'person'; id: string }
  | { kind: 'document'; id: string }
  | { kind: 'new-document' }
  | { kind: 'category'; category: api.PolicyCategory | null }
  | { kind: 'intro' };

type Shared = {
  params: URLSearchParams;
  patch: (changes: Record<string, string | null>) => void;
  attempt: number;
  refresh: () => void;
  zone: string;
  open: (panel: Panel) => void;
  documents: Block<{ items: api.PolicyDocument[] }>;
  categories: Block<{ items: api.PolicyCategory[] }>;
  cards: Block<{ items: api.OnboardingSection[] }>;
  offices: { id: string; name: string }[];
  departments: { id: string; name: string }[];
};

type View = { sub: string; action: ReactNode; body: ReactNode };

// --- общие мелочи -------------------------------------------------------------

function Search({ value, onChange, placeholder, wide }: { value: string; onChange: (value: string) => void; placeholder: string; wide?: boolean }) {
  return (
    <label className={wide ? 'on-search on-search--wide' : 'on-search'}>
      <AppIcon name="search" size={18} />
      <input type="search" value={value} placeholder={placeholder} aria-label={placeholder}
             onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

/** Поиск уходит на сервер с паузой — по строке на каждую букву не надо. */
function useDebounced(value: string, delay = 300): string {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

function Info({ children }: { children: ReactNode }) {
  return <p className="on-info"><AppIcon name="info" size={18} />{children}</p>;
}

function Dot({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`on-state on-state--${tone}`}><i aria-hidden="true" />{children}</span>;
}

function Skeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="on-skeleton" aria-busy="true" aria-label="Загрузка">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="on-skeleton__row"><span className="on-ghost on-ghost--round" /><span className="on-ghost" /><span className="on-ghost on-ghost--short" /></div>
      ))}
    </div>
  );
}

function Failure({ block, onRetry }: { block: Block<unknown>; onRetry: () => void }) {
  if (block.state === 'denied') return <p className="on-empty">Нет прав на этот раздел.</p>;
  return (
    <p className="on-empty on-empty--error" role="alert">
      Не удалось загрузить данные. <button type="button" className="on-link" onClick={onRetry}>Повторить</button>
    </p>
  );
}

function Menu({ label, items }: { label: string; items: { title: string; onPick: () => void; disabled?: boolean }[] }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="on-pop">
      <button type="button" className="on-more" aria-label={label} aria-haspopup="menu" aria-expanded={open}
              onClick={() => setOpen((was) => !was)}>
        <AppIcon name="dots" size={18} />
      </button>
      <AppPopover open={open} onClose={() => setOpen(false)} className="on-menu">
        <div role="menu" aria-label={label}>
          {items.map((one) => (
            <button key={one.title} type="button" role="menuitem" className="on-menu__item" disabled={one.disabled}
                    onClick={() => { setOpen(false); one.onPick(); }}>
              {one.title}
            </button>
          ))}
        </div>
      </AppPopover>
    </span>
  );
}

// --- Сотрудники -------------------------------------------------------------------

function usePeopleTab(active: boolean, shared: Shared): View {
  const { params, patch, attempt, refresh } = shared;
  const group = (GROUPS.some((one) => one.key === params.get('group')) ? params.get('group') : 'all') as api.OnboardingGroup | 'all';
  const office = params.get('office_id') ?? '';
  const department = params.get('department_id') ?? '';
  const [draft, setDraft] = useState(params.get('search') ?? '');
  const search = useDebounced(draft);
  useEffect(() => { if ((params.get('search') ?? '') !== search) patch({ search: search || null }); }, [search]); // eslint-disable-line react-hooks/exhaustive-deps

  const scope = useMemo<api.OnboardingScope>(() => ({
    ...(search.trim() ? { search: search.trim() } : {}),
    ...(office ? { office_id: office } : {}),
    ...(department ? { department_id: department } : {}),
  }), [search, office, department]);
  const scopeKey = `${JSON.stringify(scope)}|${attempt}`;

  const [counts, reloadCounts] = useBlock((signal) => api.onboardingCounts(signal, scope), `onb-counts-scope|${scopeKey}`, active);
  const [pages, setPages] = useState(1);
  useEffect(() => setPages(1), [scopeKey, group]);
  const [rows, reloadRows] = useBlock(async (signal) => {
    // Страницы по 25, «Показать ещё» дочитывает следующую.
    const items: api.OnboardingRow[] = [];
    let cursor: string | undefined;
    let more = false;
    for (let n = 0; n < pages; n += 1) {
      const page = await api.onboardingProgress({
        ...scope, ...(group !== 'all' ? { group } : {}), limit: String(PAGE), ...(cursor ? { cursor } : {}),
      }, signal);
      items.push(...page.items);
      more = page.has_more;
      cursor = page.next_cursor ?? undefined;
      if (!more || !cursor) break;
    }
    return { items, more };
  }, `onb-rows|${scopeKey}|${group}|${pages}`, active);
  const [attention, reloadAttention] = useBlock(
    (signal) => api.onboardingProgress({ ...scope, group: 'attention', limit: '200' }, signal),
    `onb-attention|${scopeKey}`,
    active,
  );

  const [exporting, setExporting] = useState<string | null>(null);
  const exportReport = () => {
    setExporting('Готовим файл…');
    api.onboardingExport(undefined, { ...scope, ...(group !== 'all' ? { group } : {}) })
      .then((body) => {
        download(toCsv(body.items));
        setExporting(body.total ? null : 'Под этот отбор никто не подходит — файл пустой');
      })
      .catch((error) => setExporting(messageFor(error)));
  };

  const [reminded, setReminded] = useState<string | null>(null);
  const [reminding, setReminding] = useState(false);
  const remindAll = (ids: string[]) => {
    setReminding(true);
    setReminded(null);
    api.remindOnboardingMany(ids)
      .then((body) => {
        const tally = (outcome: api.RemindOutcome) => body.items.filter((one) => one.outcome === outcome).length;
        const parts = [
          tally('sent') ? `Напомнили: ${tally('sent')}` : 'Никому не напомнили',
          tally('no_telegram') ? `нет Telegram: ${tally('no_telegram')}` : '',
          tally('already_today') ? `уже напоминали сегодня: ${tally('already_today')}` : '',
          tally('completed') ? `уже завершили: ${tally('completed')}` : '',
        ].filter(Boolean);
        setReminded(parts.join(' · '));
        refresh();
      })
      .catch((error) => setReminded(messageFor(error)))
      .finally(() => setReminding(false));
  };

  const groups = counts.state === 'ready' ? counts.data.groups : undefined;

  const body = (
    <>
      <div className="on-tools">
        <Search value={draft} onChange={setDraft} placeholder="Имя, отдел или табельный номер" />
        <div className="on-chips" role="group" aria-label="Состояние">
          {GROUPS.map((one) => (
            <button key={one.key} type="button" aria-pressed={group === one.key}
                    className={group === one.key ? 'on-chip on-chip--on' : 'on-chip'}
                    onClick={() => patch({ group: one.key === 'all' ? null : one.key })}>
              {one.title}
              {one.key !== 'all' && <span className="on-chip__count">{groups ? groups[one.key] ?? 0 : '·'}</span>}
            </button>
          ))}
        </div>
        <div className="on-tools__end">
          <Dropdown label="Офис" empty="Все офисы" value={office} options={shared.offices}
                    onChange={(value) => patch({ office_id: value || null })} />
          <Dropdown label="Отдел" empty="Все отделы" value={department} options={shared.departments}
                    onChange={(value) => patch({ department_id: value || null })} />
        </div>
      </div>

      <div className="on-kpis">
        {counts.state === 'ready' && groups ? (
          <>
            <Kpi icon="doc" tone="blue" value={groups.all} label="назначено" />
            <Kpi icon="check" tone="green" value={groups.done} label="подтвердили всё" />
            <Kpi icon="alert" tone="amber" value={groups.attention} label="требуют внимания" />
            <Kpi icon="clock" tone="red" value={groups.overdue} label="просрочены" />
          </>
        ) : counts.state === 'loading' ? (
          [0, 1, 2, 3].map((i) => <div key={i} className="on-kpi"><span className="on-ghost on-ghost--icon" /><span className="on-ghost on-ghost--value" /></div>)
        ) : <Failure block={counts} onRetry={reloadCounts} />}
      </div>

      <div className="on-body">
        <section className="on-main" aria-label="Ознакомления сотрудников">
          <h2 className="on-title">Ознакомления сотрудников</h2>
          {rows.state === 'loading' && <Skeleton />}
          {(rows.state === 'error' || rows.state === 'denied') && <Failure block={rows} onRetry={reloadRows} />}
          {rows.state === 'ready' && rows.data.items.length === 0 && (
            <p className="on-empty">
              {group !== 'all' || scope.search || office || department
                ? 'Под этот отбор никто не подходит.'
                : 'Пока никого не позвали. Ознакомление назначается в карточке сотрудника — кнопкой «Отправить ознакомление».'}
            </p>
          )}
          {rows.state === 'ready' && rows.data.items.length > 0 && (
            <div className="on-scroll">
              <table className="on-table" aria-label="Ознакомления сотрудников">
                <thead>
                  <tr><th>Сотрудник</th><th>Офис / Отдел</th><th>Назначено</th><th>Прогресс</th><th>Срок</th><th>Статус</th><th><span className="visually-hidden">Действия</span></th></tr>
                </thead>
                <tbody>
                  {rows.data.items.map((row) => {
                    const state = stateOf(row);
                    const due = row.due_date ?? null;
                    const count = materialsOf(row);
                    return (
                      <tr key={row.employee_id}>
                        <td>
                          <button type="button" className="on-person" onClick={() => shared.open({ kind: 'person', id: row.employee_id })}>
                            <span className="on-avatar" aria-hidden="true">{initials(row.full_name)}</span>
                            <span><b>{row.full_name}</b><small>{row.employee_number ? `#${row.employee_number}` : 'без табельного номера'}</small></span>
                          </button>
                        </td>
                        <td><span className="on-two"><b>{row.office_name ?? '—'}</b><small>{row.department_name ?? 'Без отдела'}</small></span></td>
                        <td>{count} {plural(count, 'материал', 'материала', 'материалов')}</td>
                        <td><Bar value={progressOf(row)} /></td>
                        <td className={row.overdue ? 'on-red' : ''}>{due ? dateRu(due) : <span className="on-muted">не назначен</span>}</td>
                        <td><Dot tone={state.tone}>{state.title}</Dot></td>
                        <td>
                          <Menu label={`Действия: ${row.full_name}`} items={[
                            { title: 'Открыть ознакомление', onPick: () => shared.open({ kind: 'person', id: row.employee_id }) },
                            { title: 'Напомнить', disabled: row.completed || row.telegram_state !== 'ACTIVE', onPick: () => remindAll([row.employee_id]) },
                          ]} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {rows.data.more && (
                <button type="button" className="on-link on-more-rows" onClick={() => setPages((n) => n + 1)}>Показать ещё</button>
              )}
            </div>
          )}
        </section>

        <aside className="on-side" aria-label="Требуют внимания">
          <h2 className="on-title">Требуют внимания</h2>
          {attention.state === 'loading' && <Skeleton rows={3} />}
          {(attention.state === 'error' || attention.state === 'denied') && <Failure block={attention} onRetry={reloadAttention} />}
          {attention.state === 'ready' && (attention.data.items.length ? (
            <>
              <ul className="on-alerts">
                {attention.data.items.slice(0, 4).map((row) => {
                  const reason = REASON[(row.reasons ?? ['silent'])[0] as api.OnboardingReason];
                  return (
                    <li key={row.employee_id}>
                      <span className={`on-alerts__icon on-tone--${reason.tone}`}><AppIcon name={reason.icon} size={18} /></span>
                      <span className="on-alerts__text">
                        <b>{reason.title}</b>
                        <button type="button" className="on-alerts__who" onClick={() => shared.open({ kind: 'person', id: row.employee_id })}>{row.full_name}</button>
                        <small>{alertDetail(row)}</small>
                      </span>
                    </li>
                  );
                })}
              </ul>
              {attention.data.items.length > 4 && <p className="on-muted on-alerts__more">и ещё {attention.data.items.length - 4}</p>}
              <button type="button" className="on-remind" disabled={reminding}
                      onClick={() => remindAll(attention.data.items.filter((one) => !one.completed).map((one) => one.employee_id))}>
                <AppIcon name="send" size={18} /> {reminding ? 'Отправляем…' : 'Напомнить всем'}
              </button>
              {reminded && <p className="on-note">{reminded}</p>}
            </>
          ) : <p className="on-empty">Сейчас никто не требует внимания: сроки в порядке, отказов и новых версий без ответа нет.</p>)}
        </aside>
      </div>

      <Info>Сотрудник читает материал в Telegram и подтверждает ознакомление. История действий сохраняется.</Info>
    </>
  );

  return {
    sub: 'Назначайте обязательные материалы, следите за подтверждениями и вовремя напоминайте сотрудникам.',
    action: (
      <span className="on-action-slot">
        <button type="button" className="on-outline" onClick={exportReport}>
          <AppIcon name="download" size={18} /> Выгрузить отчёт
        </button>
        {(exporting || reminded) && <span className="on-toast" role="status">{exporting ?? reminded}</span>}
      </span>
    ),
    body,
  };
}

/** Подробность для правой колонки: к какому материалу или сроку относится. */
function alertDetail(row: api.OnboardingRow): string {
  const reasons = row.reasons ?? [];
  const materials = row.materials ?? [];
  if (reasons.includes('overdue')) return `Срок был ${dateRu(row.due_date ?? '')}`;
  if (reasons.includes('declined')) return materials.filter((one) => one.state === 'declined').map((one) => `«${one.title}»`).join(', ');
  if (reasons.includes('renewal')) {
    const titles = materials.filter((one) => one.state === 'renewal').map((one) => `«${one.title}»`).join(', ');
    return `${titles} — нужно подтвердить заново`;
  }
  const since = row.invited_at ?? row.enrolled_at;
  return since ? `Приглашён(а) ${dateRu(since.slice(0, 10))}, к ознакомлению не приступил(а)` : 'К ознакомлению не приступил(а)';
}

function Kpi({ icon, tone, value, label }: { icon: AppIconName; tone: Tone; value: number; label: string }) {
  return (
    <div className="on-kpi">
      <span className={`on-kpi__icon on-kpi__icon--${tone}`}><AppIcon name={icon} size={20} /></span>
      <span><b>{value}</b><small>{label}</small></span>
    </div>
  );
}

function Bar({ value }: { value: number | null }) {
  if (value === null) return <span className="on-muted">—</span>;
  return (
    <span className="on-bar">
      <small>{value}%</small>
      <span className="on-bar__track"><span style={{ width: `${value}%` }} /></span>
    </span>
  );
}

// --- Материалы -------------------------------------------------------------------

type DocStatus = 'published' | 'draft' | 'renewal';

function statusOfDoc(doc: api.PolicyDocument): DocStatus {
  const hasDraft = doc.versions.some((one) => one.status === 'DRAFT');
  if (!doc.current_version) return 'draft';
  return hasDraft ? 'renewal' : 'published';
}

const DOC_STATUS: Record<DocStatus, { title: string; tone: Tone }> = {
  published: { title: 'Опубликован', tone: 'green' },
  draft: { title: 'Черновик', tone: 'grey' },
  renewal: { title: 'Готовится новая версия', tone: 'amber' },
};

const SORTS = [
  { id: 'changed', name: 'Сначала недавно изменённые' },
  { id: 'title', name: 'По названию' },
  { id: 'order', name: 'По порядку в разделе' },
];

function useMaterialsTab(active: boolean, shared: Shared): View {
  const { documents, categories } = shared;
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [section, setSection] = useState('');
  const [sort, setSort] = useState('');
  void active;

  const list = documents.state === 'ready' ? documents.data.items : [];
  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const rows = list.filter((doc) => {
      if (status && statusOfDoc(doc) !== status) return false;
      if (section === 'none' ? doc.category : section && doc.category?.id !== section) return false;
      if (!needle) return true;
      return `${doc.title} ${doc.description ?? ''} ${doc.category?.title ?? ''}`.toLowerCase().includes(needle);
    });
    const by = sort || 'changed';
    return [...rows].sort((a, b) => (
      by === 'title' ? a.title.localeCompare(b.title, 'ru')
        : by === 'order' ? (a.category?.title ?? '￿').localeCompare(b.category?.title ?? '￿', 'ru') || a.position - b.position
          : (b.changed_at ?? '').localeCompare(a.changed_at ?? '')
    ));
  }, [list, search, status, section, sort]);

  const sectionOptions = [
    ...(categories.state === 'ready' ? categories.data.items.map((one) => ({ id: one.id, name: one.title })) : []),
    { id: 'none', name: 'Без раздела' },
  ];
  const renewalPeople = list.reduce((sum, doc) => sum + (doc.renewal_pending ?? 0), 0);
  const soonest = list
    .filter((doc) => doc.nearest_due)
    .sort((a, b) => (a.nearest_due ?? '').localeCompare(b.nearest_due ?? ''))[0];

  const body = (
    <>
      <div className="on-tools">
        <Search value={search} onChange={setSearch} placeholder="Найти материал" />
        <div className="on-tools__end on-tools__end--grow">
          <Dropdown label="Статус" empty="Все статусы" value={status}
                    options={(Object.keys(DOC_STATUS) as DocStatus[]).map((id) => ({ id, name: DOC_STATUS[id].title }))}
                    onChange={setStatus} />
          <Dropdown label="Раздел" empty="Все разделы" value={section} options={sectionOptions} onChange={setSection} />
          <Dropdown label="Сортировка" empty="Сначала недавно изменённые" value={sort} options={SORTS.slice(1)} onChange={setSort} />
        </div>
      </div>
      <Info>После публикации материал нельзя менять — создайте новую версию, чтобы история подтверждений оставалась точной.</Info>

      <div className="on-body">
        <section className="on-main" aria-label="Материалы">
          <h2 className="on-title">Материалы</h2>
          {documents.state === 'loading' && <Skeleton />}
          {(documents.state === 'error' || documents.state === 'denied') && <Failure block={documents} onRetry={shared.refresh} />}
          {documents.state === 'ready' && shown.length === 0 && (
            <p className="on-empty">{list.length ? 'Под этот отбор материалов нет.' : 'Материалов пока нет. Создайте первый — кнопкой «Создать материал».'}</p>
          )}
          {documents.state === 'ready' && shown.length > 0 && (
            <div className="on-scroll">
              <table className="on-table" aria-label="Материалы">
                <thead>
                  <tr><th>Материал и описание</th><th>Раздел</th><th>Версия / Статус</th><th>Назначено</th><th>Подтвердили</th><th>Последнее обновление</th><th><span className="visually-hidden">Действия</span></th></tr>
                </thead>
                <tbody>
                  {shown.map((doc) => {
                    const state = DOC_STATUS[statusOfDoc(doc)];
                    const draft = doc.versions.find((one) => one.status === 'DRAFT');
                    // Необязательный, как и черновик, ни от кого не требуется.
                    const live = !!doc.current_version && doc.is_mandatory;
                    return (
                      <tr key={doc.id}>
                        <td>
                          <button type="button" className="on-doc" onClick={() => shared.open({ kind: 'document', id: doc.id })}>
                            <span className="on-doc__icon" aria-hidden="true"><AppIcon name="doc" size={18} /></span>
                            <span><b>{doc.title}</b><small>{doc.description || (doc.is_mandatory ? 'Обязательный материал' : 'Необязательный материал')}</small></span>
                          </button>
                        </td>
                        <td>{doc.category?.title ?? <span className="on-muted">Без раздела</span>}</td>
                        <td>
                          <span className="on-two">
                            <b>{doc.current_version ? `v${doc.current_version.version}` : draft ? `v${draft.version}` : '—'}</b>
                            <Dot tone={state.tone}>{state.title}</Dot>
                          </span>
                        </td>
                        <td>{live ? doc.assigned ?? 0 : <span className="on-muted" title={doc.current_version ? 'Необязательный материал ни от кого не требуется' : 'Черновик никого не обязывает'}>—</span>}</td>
                        <td>
                          {live ? (
                            <span className="on-two">
                              <b>{doc.confirmed ?? 0}</b>
                              {(doc.renewal_pending ?? 0) > 0 && <small className="on-amber">новая версия: {doc.renewal_pending}</small>}
                            </span>
                          ) : <span className="on-muted">—</span>}
                        </td>
                        <td><span className="on-two"><b>{doc.changed_at ? dateRu(doc.changed_at.slice(0, 10)) : '—'}</b><small>{doc.changed_by ?? doc.created_by ?? ''}</small></span></td>
                        <td><Menu label={`Действия: ${doc.title}`} items={[{ title: 'Открыть материал', onPick: () => shared.open({ kind: 'document', id: doc.id }) }]} /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <aside className="on-side" aria-label="Быстрый обзор">
          <h2 className="on-title">Быстрый обзор</h2>
          {documents.state === 'ready' ? (
            <>
              <ul className="on-facts">
                <Fact icon="doc" tone="blue" value={list.length} label={plural(list.length, 'материал', 'материала', 'материалов')} />
                <Fact icon="check" tone="green" value={list.filter((d) => d.current_version).length} label="опубликовано" />
                <Fact icon="pencil" tone="grey" value={list.filter((d) => !d.current_version).length} label={plural(list.filter((d) => !d.current_version).length, 'черновик', 'черновика', 'черновиков')} />
                <Fact icon="refresh" tone="blue" value={list.filter((d) => statusOfDoc(d) === 'renewal').length} label="готовится новая версия" />
              </ul>
              <h3 className="on-subtitle">Нужно проверить</h3>
              <ul className="on-checks">
                {renewalPeople > 0 && (
                  <li>
                    <span className="on-tone--amber"><AppIcon name="user" size={18} /></span>
                    <span>
                      <b>{renewalPeople} {plural(renewalPeople, 'сотрудник не подтвердил', 'сотрудника не подтвердили', 'сотрудников не подтвердили')} новую версию</b>
                      <button type="button" className="on-link" onClick={() => shared.patch({ tab: null, group: 'attention' })}>Посмотреть сотрудников</button>
                    </span>
                  </li>
                )}
                {soonest && soonest.nearest_due && (
                  <li>
                    <span className={daysUntil(soonest.nearest_due) < 0 ? 'on-tone--red' : 'on-tone--amber'}><AppIcon name="clock" size={18} /></span>
                    <span>
                      <b>{soonest.title}: {dueWords(soonest.nearest_due)}</b>
                      <button type="button" className="on-link" onClick={() => shared.open({ kind: 'document', id: soonest.id })}>Открыть материал</button>
                    </span>
                  </li>
                )}
                {!renewalPeople && !soonest && <li className="on-muted">Всё в порядке: новых версий без ответа и близких сроков нет.</li>}
              </ul>
            </>
          ) : <Skeleton rows={4} />}
        </aside>
      </div>

      <Info>При новой версии сотрудник получает материал повторно, а прежние подтверждения сохраняются в истории.</Info>
    </>
  );

  return {
    sub: 'Материалы, которые сотрудники читают и подтверждают в Telegram.',
    action: (
      <span className="on-action-slot">
        <button type="button" className="on-outline" onClick={() => shared.open({ kind: 'new-document' })}>
          <AppIcon name="plus" size={18} /> Создать материал
        </button>
      </span>
    ),
    body,
  };
}

function dueWords(day: string): string {
  const left = daysUntil(day);
  if (left < 0) return `срок прошёл ${-left} ${plural(-left, 'день', 'дня', 'дней')} назад`;
  if (left === 0) return 'срок сегодня';
  return `срок через ${left} ${plural(left, 'день', 'дня', 'дней')}`;
}

function Fact({ icon, tone, value, label }: { icon: AppIconName; tone: Tone; value: number; label: string }) {
  return (
    <li className="on-fact">
      <span className={`on-fact__icon on-tone--${tone}`}><AppIcon name={icon} size={18} /></span>
      <b>{value}</b>
      <span>{label}</span>
    </li>
  );
}

// --- Разделы ------------------------------------------------------------------------

function useSectionsTab(active: boolean, shared: Shared): View {
  const { categories, cards, documents } = shared;
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState('');
  void active;

  const list = categories.state === 'ready' ? categories.data.items : [];
  const intro = cards.state === 'ready' ? cards.data.items : [];
  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const rows = list.filter((one) => !needle || `${one.title} ${one.description ?? ''} ${one.documents.map((d) => d.title).join(' ')}`.toLowerCase().includes(needle));
    return [...rows].sort((a, b) => (sort === 'title' ? a.title.localeCompare(b.title, 'ru') : b.changed_at.localeCompare(a.changed_at)));
  }, [list, search, sort]);
  const introShown = intro.length > 0 && (!search.trim() || 'знакомство с компанией'.includes(search.trim().toLowerCase()));
  const loose = documents.state === 'ready' ? documents.data.items.filter((doc) => !doc.category).length : 0;
  const top = Math.max(1, ...list.map((one) => one.documents_count), loose);

  const body = (
    <>
      <div className="on-tools">
        <Search value={search} onChange={setSearch} placeholder="Найти раздел" wide />
        <div className="on-tools__end on-sort">
          <Dropdown label="Сортировка" empty="Сначала недавно изменённые" value={sort}
                    options={[{ id: 'title', name: 'По названию' }]} onChange={setSort} />
        </div>
      </div>
      <Info>Раздел помогает HR и сотруднику быстро найти нужный материал. Внутри раздела можно менять порядок материалов.</Info>

      <div className="on-body">
        <section className="on-main" aria-label="Разделы материалов">
          <h2 className="on-title">Разделы материалов</h2>
          {(categories.state === 'loading' || cards.state === 'loading') && <Skeleton />}
          {(categories.state === 'error' || categories.state === 'denied') && <Failure block={categories} onRetry={shared.refresh} />}
          {categories.state === 'ready' && cards.state === 'ready' && (
            shown.length === 0 && !introShown ? (
              <p className="on-empty">{list.length ? 'Под этот поиск разделов нет.' : 'Разделов пока нет. Создайте первый — кнопкой «Создать раздел».'}</p>
            ) : (
              <div className="on-scroll">
                <table className="on-table" aria-label="Разделы материалов">
                  <thead>
                    <tr><th>Раздел и описание</th><th>Материалов</th><th>Ответственный</th><th>Последнее обновление</th><th>Примеры материалов</th><th><span className="visually-hidden">Действия</span></th></tr>
                  </thead>
                  <tbody>
                    {introShown && (
                      <tr className="on-row--pinned">
                        <td>
                          <button type="button" className="on-doc" onClick={() => shared.open({ kind: 'intro' })}>
                            <span className="on-doc__icon on-doc__icon--folder" aria-hidden="true"><AppIcon name="book" size={18} /></span>
                            <span><b>Знакомство с компанией</b><small>Карточки о компании, которые бот показывает первыми. Их читают, согласие не требуется.</small></span>
                          </button>
                        </td>
                        <td><span className="on-two"><b>{intro.length}</b><small>{plural(intro.length, 'карточка', 'карточки', 'карточек')}</small></span></td>
                        <td><span className="on-muted">—</span></td>
                        <td>{dateRu([...intro].sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0]!.updated_at.slice(0, 10))}</td>
                        <td><Examples titles={intro.map((one) => one.title)} /></td>
                        <td><Menu label="Действия: Знакомство с компанией" items={[{ title: 'Открыть карточки', onPick: () => shared.open({ kind: 'intro' }) }]} /></td>
                      </tr>
                    )}
                    {shown.map((one) => (
                      <tr key={one.id}>
                        <td>
                          <button type="button" className="on-doc" onClick={() => shared.open({ kind: 'category', category: one })}>
                            <span className="on-doc__icon on-doc__icon--folder" aria-hidden="true"><AppIcon name="archive" size={18} /></span>
                            <span><b>{one.title}</b><small>{one.description ?? 'Без описания'}</small></span>
                          </button>
                        </td>
                        <td><span className="on-two"><b>{one.documents_count}</b><small>{plural(one.documents_count, 'материал', 'материала', 'материалов')}</small></span></td>
                        <td>{one.owner ? <span className="on-two"><b>{one.owner.full_name}</b><small>{one.owner.position_name ?? ''}</small></span> : <span className="on-muted">не назначен</span>}</td>
                        <td><span className="on-two"><b>{dateRu(one.changed_at.slice(0, 10))}</b><small>{one.changed_by ?? ''}</small></span></td>
                        <td>{one.documents.length ? <Examples titles={one.documents.map((d) => d.title)} /> : <span className="on-muted">материалов нет</span>}</td>
                        <td><Menu label={`Действия: ${one.title}`} items={[{ title: 'Изменить раздел', onPick: () => shared.open({ kind: 'category', category: one }) }]} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}
        </section>

        <aside className="on-side" aria-label="Структура материалов">
          <h2 className="on-title">Структура материалов</h2>
          {categories.state === 'ready' ? (
            <ul className="on-structure">
              {list.map((one) => (
                <li key={one.id}>
                  <span className="on-structure__head"><b>{one.title}</b><b>{one.documents_count}</b></span>
                  <span className="on-structure__track"><span style={{ width: `${(one.documents_count * 100) / top}%` }} /></span>
                </li>
              ))}
              {loose > 0 && (
                <li>
                  <span className="on-structure__head"><span className="on-muted">Без раздела</span><b>{loose}</b></span>
                  <span className="on-structure__track"><span className="on-structure__fill--grey" style={{ width: `${(loose * 100) / top}%` }} /></span>
                </li>
              )}
              {!list.length && !loose && <li className="on-muted">Материалов пока нет.</li>}
            </ul>
          ) : <Skeleton rows={3} />}
          <h3 className="on-subtitle">Совет</h3>
          <p className="on-tip"><span className="on-tone--amber"><AppIcon name="bulb" size={20} /></span>Сначала создайте раздел, затем добавьте материалы и опубликуйте их. Так сотруднику проще пройти ознакомление.</p>
        </aside>
      </div>

      <Info>Изменение раздела не меняет историю уже подтверждённых материалов.</Info>
    </>
  );

  return {
    sub: 'Соберите материалы по понятным разделам и назначайте их сотрудникам.',
    action: (
      <span className="on-action-slot">
        <button type="button" className="on-outline" onClick={() => shared.open({ kind: 'category', category: null })}>
          <AppIcon name="plus" size={18} /> Создать раздел
        </button>
      </span>
    ),
    body,
  };
}

function Examples({ titles }: { titles: string[] }) {
  return (
    <ul className="on-examples">
      {titles.slice(0, 3).map((title) => <li key={title}><AppIcon name="doc" size={16} />{title}</li>)}
      {titles.length > 3 && <li className="on-muted">и ещё {titles.length - 3}</li>}
    </ul>
  );
}

// --- выгрузка --------------------------------------------------------------------

const COLUMNS: Array<[string, string]> = [
  ['employee_number', 'Табельный номер'],
  ['full_name', 'Сотрудник'],
  ['office', 'Офис'],
  ['department', 'Отдел'],
  ['position', 'Должность'],
  ['sections', 'Знакомство с компанией'],
  ['policies', 'Материалы'],
  ['due_date', 'Срок'],
  ['attention', 'Требует внимания'],
  ['last_reminder_at', 'Последнее напоминание'],
  ['completed_at', 'Завершено'],
];

function toCsv(rows: Record<string, string | number | null>[]): string {
  const escape = (value: string | number | null) => {
    const text = value === null || value === undefined ? '' : String(value);
    return /[";\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const head = COLUMNS.map(([, title]) => title).join(';');
  const body = rows.map((row) => COLUMNS.map(([key]) => escape(row[key] ?? null)).join(';'));
  // BOM: без него Excel читает кириллицу в CSV как набор знаков вопроса.
  return `﻿${[head, ...body].join('\n')}`;
}

function download(csv: string): void {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = window.document.createElement('a');
  link.href = url;
  link.download = `ознакомления-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}
