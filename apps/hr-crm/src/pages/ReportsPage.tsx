/**
 * «Отчёты»: выбрать вид, настроить параметры и поля, посмотреть настоящие
 * строки и заказать файл.
 *
 * Считает всё сервер. Предпросмотр — это `/reports/preview` с теми же
 * построителями, что и у файла; поля видов приходят из `/reports/catalog`;
 * заказ и история — очередь `/export-jobs/`. Ни одного числа, собранного
 * на клиенте из того, что уже показано.
 *
 * Шаги сверху не кнопки, а состояние: вид не выбран — первый, параметры
 * неполны — второй, всё готово — третий.
 *
 * Разметка и размеры — в `styles/reports.css`, классы с префиксом `rp-`.
 */

import {
  useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode,
} from 'react';
import { useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { AppShell } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { AppMultiSelect, AppPopover, AppSelectField } from '../components/AppSelect';
import { AppDateRangePicker } from '../components/DateRangePicker';
import { useSession } from '../features/auth/session';
import { formatTime, today, useBlock } from '../features/dashboard/data';
import { momentTitle, sizeTitle } from '../features/reports/format';
import {
  COLUMNS, KIND_LOOK, KIND_ORDER, OFFICES, PEOPLE, ROWS, STATUS_TITLE, TABS,
  jobParams, jobTitle, kindTitle, number, periodLong, periodMode, periodRange,
  plural, progressPercent, tabCount, type KindKey, type TabKey,
} from '../features/reports/kinds';
import { useHistory } from '../features/reports/queue';
import '../styles/reports.css';

type Fmt = 'xlsx' | 'csv';
type Person = { id: string; name: string | null };

const PREVIEW_DELAY_MS = 350;
const SHORT_PREVIEW = 5;

export function ReportsPage() {
  const session = useSession();
  const can = (code: string) =>
    session.status === 'authenticated' && session.user.permissions.includes(code);
  const me = session.status === 'authenticated' ? session.user.id : null;
  const mayExport = can('reports.export');
  const maySeeOthers = can('audit.read');
  // Чужой файл скачивается по отдельному праву; область проверяет сервер.
  const mayDownloadAny = mayExport && can('reports.download_any');

  // --- справочники ----------------------------------------------------------

  const [catalog] = useBlock((signal) => api.reportCatalog(signal), 'reports-catalog', mayExport);
  const [directory] = useBlock(
    (signal) =>
      Promise.all([api.regions(signal), api.offices(signal), api.departments(signal)]).then(
        ([r, o, d]) => ({ regions: r.items, offices: o.items, departments: d.items }),
      ),
    'reports-directory',
  );
  const [templates, reloadTemplates] = useBlock(
    (signal) => api.reportTemplates(signal).then((page) => page.items),
    'reports-templates',
    mayExport,
  );

  const regions = directory.state === 'ready' ? directory.data.regions : [];
  const allOffices = directory.state === 'ready' ? directory.data.offices : [];
  const allDepartments = directory.state === 'ready' ? directory.data.departments : [];
  const kinds = catalog.state === 'ready' ? catalog.data.kinds : [];

  // --- параметры будущего отчёта -------------------------------------------

  const [kind, setKind] = useState<KindKey | null>(null);
  const [from, setFrom] = useState(() => periodRange('this_month', today())[0]);
  const [to, setTo] = useState(() => periodRange('this_month', today())[1]);
  const [region, setRegion] = useState('');
  const [officeIds, setOfficeIds] = useState<string[]>([]);
  const [departmentIds, setDepartmentIds] = useState<string[]>([]);
  const [person, setPerson] = useState<Person | null>(null);
  const [inactive, setInactive] = useState(false);
  const [fields, setFields] = useState<string[]>([]);
  const [fmt, setFmt] = useState<Fmt>('xlsx');
  const [name, setName] = useState('');

  const chosen = kinds.find((item) => item.key === kind) ?? null;
  const offices = useMemo(
    () => (region ? allOffices.filter((item) => item.region_id === region) : allOffices),
    [allOffices, region],
  );
  const departments = useMemo(
    () => allDepartments.filter((item) =>
      !officeIds.length || !item.office_id || officeIds.includes(item.office_id)),
    [allDepartments, officeIds],
  );

  // Офис, выпавший из нового региона, снимается: уехать на сервер должно
  // ровно то, что показано.
  useEffect(() => {
    const allowed = new Set(offices.map((item) => item.id));
    setOfficeIds((was) => (was.every((id) => allowed.has(id)) ? was : was.filter((id) => allowed.has(id))));
  }, [offices]);

  function chooseKind(key: KindKey) {
    setKind(key);
    const item = kinds.find((one) => one.key === key);
    setFields(item ? item.fields.filter((one) => one.default).map((one) => one.key) : []);
  }

  // Открываем конструктор с первым доступным типом: параметры и предпросмотр
  // сразу показывают настоящие данные, но только после загрузки серверного каталога.
  useEffect(() => {
    if (kind || !mayExport || catalog.state !== 'ready') return;
    const firstAvailable = KIND_ORDER
      .map((key) => kinds.find((item) => item.key === key))
      .find((item) => item && can(item.permission));
    if (firstAvailable) {
      setKind(firstAvailable.key);
      setFields(firstAvailable.fields.filter((field) => field.default).map((field) => field.key));
    }
  }, [kind, mayExport, catalog.state, kinds]);

  const maxDays = catalog.state === 'ready' ? catalog.data.max_period_days : 366;
  const span = days(from, to);
  const badOrder = span <= 0;
  const tooLong = span > maxDays;
  const mayKind = Boolean(chosen && mayExport && can(chosen.permission));
  const valid = Boolean(chosen) && !badOrder && !tooLong && fields.length > 0;
  const step = !chosen ? 1 : valid ? 3 : 2;
  const mode = periodMode(from, to, today());

  const spec: api.ReportSpec | null = chosen && valid && mayKind
    ? {
        kind: chosen.key,
        date_from: from,
        date_to: to,
        period: mode,
        region_id: region || null,
        office_ids: officeIds,
        department_ids: departmentIds,
        employee_id: person?.id ?? null,
        include_inactive: inactive,
        fields,
        name: name.trim() || null,
      }
    : null;

  const preview = usePreview(spec, fmt, catalog.state === 'ready' ? catalog.data.preview_max_rows : 20);

  function applyFilters(key: string, format: Fmt, f: api.ExportFilters) {
    if (!KIND_ORDER.includes(key as KindKey)) return;
    const item = kinds.find((one) => one.key === key);
    setKind(key as KindKey);
    setFmt(format);
    const saved = f.period ?? 'custom';
    if (saved !== 'custom') {
      const [a, b] = periodRange(saved, today());
      setFrom(a);
      setTo(b);
    } else if (f.date_from && f.date_to) {
      setFrom(f.date_from);
      setTo(f.date_to);
    } else if (f.date) {
      setFrom(f.date);
      setTo(f.date);
    }
    setRegion(f.region_id ?? '');
    setOfficeIds(f.office_ids ?? (f.office_id ? [f.office_id] : []));
    setDepartmentIds(f.department_ids ?? []);
    setPerson(f.employee_id ? { id: f.employee_id, name: null } : null);
    setInactive(Boolean(f.include_inactive));
    setFields(f.fields ?? (item ? item.fields.filter((one) => one.default).map((one) => one.key) : []));
    setName(f.name ?? '');
    // Прокрутки к началу конструктора здесь нет: шаблон применяют,
    // стоя у списка шаблонов, и страница уезжала из-под рук.
  }

  // --- заказ ----------------------------------------------------------------

  const requestKey = useRef(newKey());
  const ordering = useRef(false);
  const [busyOrder, setBusyOrder] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [orderError, setOrderError] = useState<string | null>(null);
  const specKey = spec ? JSON.stringify([spec, fmt]) : '';

  // Другие параметры — другой заказ. Ключ повтора держится, только пока
  // человек повторяет ТОТ ЖЕ заказ после сбоя.
  useEffect(() => {
    if (!ordering.current) requestKey.current = newKey();
  }, [specKey]);

  async function order() {
    if (!spec || ordering.current) return;
    ordering.current = true;
    setBusyOrder(true);
    setNotice(null);
    setOrderError(null);
    try {
      const job = await api.orderExport({
        ...spec, fmt, builder: true, client_request_id: requestKey.current,
      });
      requestKey.current = newKey();
      setNotice(`«${job.title ?? kindTitle(job.kind)}» поставлен в очередь — файл появится в истории`);
      refresh();
    } catch (error) {
      setOrderError(orderMessage(error));
    } finally {
      ordering.current = false;
      setBusyOrder(false);
    }
  }

  // --- история --------------------------------------------------------------

  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((item) => item.key === params.get('tab'))?.key ?? 'all') as TabKey;
  const mineOnly = !maySeeOthers || params.get('authors') !== 'all';
  const [updated, setUpdated] = useState<Date | null>(null);
  const onFresh = useCallback(() => setUpdated(new Date()), []);
  const { live, loadMore, more, replace, refresh } = useHistory({
    status: TABS.find((item) => item.key === tab)?.statuses ?? '',
    mineOnly,
    onFresh,
  });
  const counts = live.state === 'ready' ? live.data.counts : null;

  const [rowBusy, setRowBusy] = useState<Record<string, boolean>>({});
  const [rowError, setRowError] = useState<{ id: string; text: string } | null>(null);

  async function act(job: api.ExportJob, what: 'cancel' | 'retry' | 'hide' | 'repeat') {
    if (rowBusy[job.id]) return;
    setRowBusy((was) => ({ ...was, [job.id]: true }));
    setRowError(null);
    try {
      if (what === 'cancel') replace(await api.cancelExport(job.id));
      if (what === 'retry') replace(await api.retryExport(job.id));
      if (what === 'hide') await api.hideExport(job.id);
      if (what === 'repeat') {
        const f = job.filters ?? {};
        if (f.builder === 2 && f.date_from && f.date_to && KIND_ORDER.includes(job.kind as KindKey)) {
          await api.orderExport({
            kind: job.kind as KindKey,
            date_from: f.date_from,
            date_to: f.date_to,
            period: 'custom',
            region_id: f.region_id ?? null,
            office_ids: f.office_ids ?? [],
            department_ids: f.department_ids ?? [],
            employee_id: f.employee_id ?? null,
            include_inactive: Boolean(f.include_inactive),
            fields: f.fields ?? [],
            name: f.name ?? null,
            fmt: job.fmt,
            builder: true,
            client_request_id: newKey(),
          });
        } else {
          await api.retryExport(job.id);
        }
      }
      refresh();
    } catch (error) {
      setRowError({
        id: job.id,
        text: error instanceof ApiFailure && error.status === 409
          ? (error.message || 'Состояние выгрузки изменилось — строка обновлена')
          : messageFor(error),
      });
      refresh();
    } finally {
      setRowBusy((was) => ({ ...was, [job.id]: false }));
    }
  }

  // --- шаблоны --------------------------------------------------------------

  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [templateNote, setTemplateNote] = useState<{ good: boolean; text: string } | null>(null);

  async function removeTemplate(template: api.ReportTemplate) {
    try {
      await api.deleteReportTemplate(template.id);
      setTemplateNote({ good: true, text: `Шаблон «${template.name}» удалён` });
      reloadTemplates();
    } catch (error) {
      setTemplateNote({ good: false, text: messageFor(error) });
    }
  }

  const retentionHours = catalog.state === 'ready' ? catalog.data.retention_hours : null;

  return (
    <AppShell breadcrumb="Отчёты" section="reports">
      <div className="rp">
        <header className="rp-head">
          <div>
            <h1 className="rp-head__title">Отчёты</h1>
            <p className="rp-head__sub">Выберите данные, настройте фильтры и сформируйте файл</p>
          </div>
          <div className="rp-head__tools">
            <div className="rp-pop-anchor">
              <button type="button" className="rp-btn rp-btn--ghost rp-templates-btn"
                      aria-expanded={templatesOpen} disabled={!mayExport}
                      onClick={() => setTemplatesOpen((was) => !was)}>
                <AppIcon name="doc" size={18} />
                Шаблоны отчётов
              </button>
              {templatesOpen && (
                <Popover onClose={() => setTemplatesOpen(false)} className="rp-templates">
                  <p className="rp-pop__title">Мои шаблоны</p>
                  {templates.state === 'loading' && <p className="rp-muted">Загружаем…</p>}
                  {templates.state === 'error' && <p className="rp-bad">Не удалось загрузить шаблоны</p>}
                  {templates.state === 'ready' && templates.data.length === 0 && (
                    <p className="rp-muted">Шаблонов пока нет. Настройте отчёт и нажмите «Сохранить как шаблон».</p>
                  )}
                  {templates.state === 'ready' && templates.data.map((template) => (
                    <div key={template.id} className="rp-template">
                      <button type="button" className="rp-template__apply"
                              onClick={() => {
                                applyFilters(template.kind, template.fmt, template.filters);
                                setTemplatesOpen(false);
                                setTemplateNote({ good: true, text: `Применён шаблон «${template.name}»` });
                              }}>
                        <b>{template.name}</b>
                        <span>{kindTitle(template.kind)} · {template.fmt === 'xlsx' ? 'Excel' : 'CSV'}</span>
                      </button>
                      <button type="button" className="rp-icon-btn" aria-label={`Удалить шаблон ${template.name}`}
                              onClick={() => void removeTemplate(template)}>
                        <AppIcon name="close" size={16} />
                      </button>
                    </div>
                  ))}
                </Popover>
              )}
            </div>
            <button type="button" className="rp-icon-btn" aria-label="Обновить историю" onClick={refresh}>
              <AppIcon name="refresh" size={18} />
            </button>
            <span className="rp-stamp">{updated ? `Обновлено в ${formatTime(updated)}` : 'Загружаем…'}</span>
          </div>
        </header>

        <ol className="rp-steps" aria-label="Шаги">
          {['Тип отчёта', 'Параметры', 'Формат и создание'].map((title, index) => {
            const number = index + 1;
            const state = number < step ? 'done' : number === step ? 'on' : 'todo';
            return (
              <li key={title} className={`rp-step rp-step--${state}`}
                  aria-current={state === 'on' ? 'step' : undefined}>
                <span className="rp-step__dot">{number}</span>
                <span className="rp-step__title">{title}</span>
                {index < 2 && <span className="rp-step__line" aria-hidden="true" />}
              </li>
            );
          })}
        </ol>

        {!mayExport && (
          <p className="rp-banner rp-banner--bad" role="alert">
            Нет права на выгрузку отчётов. Файлы уходят из системы, и это отдельное
            разрешение — попросите его у администратора.
          </p>
        )}
        {catalog.state === 'error' && (
          <p className="rp-banner rp-banner--bad" role="alert">Не удалось загрузить виды отчётов.</p>
        )}

        <div className="rp-kinds" role="radiogroup" aria-label="Тип отчёта">
          {KIND_ORDER.map((key) => {
            const look = KIND_LOOK[key];
            const item = kinds.find((one) => one.key === key);
            const permitted = Boolean(item && mayExport && can(item.permission));
            const on = key === kind;
            return (
              <button key={key} type="button" role="radio" aria-checked={on}
                      disabled={!permitted}
                      title={permitted || !item ? undefined : `Нужно право ${item.permission}`}
                      className={on ? 'rp-kind rp-kind--on' : 'rp-kind'}
                      onClick={() => chooseKind(key)}>
                <span className="rp-kind__icon" aria-hidden="true"><AppIcon name={look.icon} size={20} /></span>
                <span className="rp-kind__text">
                  <span className="rp-kind__title">{look.title}</span>
                  <span className="rp-kind__note">{permitted || !item ? look.note : 'Нет доступа к данным'}</span>
                </span>
                {on && <span className="rp-kind__mark" aria-hidden="true"><AppIcon name="check" size={16} /></span>}
              </button>
            );
          })}
        </div>

        <section className="rp-work">
          {/* --- параметры --- */}
          <div className="rp-params">
            <div className="rp-params__heading">
              <div>
                <h2 className="rp-h2">Параметры{chosen ? ` ${chosen.title.toLowerCase()}` : ' отчёта'}</h2>
                <p className="rp-muted">Настройте период и состав данных</p>
              </div>
              {chosen && <span className="rp-kind-badge">{chosen.title}</span>}
            </div>
            {chosen && (
              <div className="rp-summary" aria-live="polite">
                <AppIcon name="calendar" size={18} />
                <span><b>{chosen.title}</b> · {periodLong(from, to, true)} · {officeIds.length ? officeIds.map((id) => offices.find((item) => item.id === id)?.name).filter(Boolean).join(', ') : region ? regions.find((item) => item.id === region)?.name ?? 'Выбранный регион' : 'Все офисы'}</span>
                <button type="button" className="rp-link" onClick={() => document.querySelector('.rp-period')?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>Изменить <AppIcon name="arrow" size={16} /></button>
              </div>
            )}

            <div className="rp-field">
              <span className="rp-label">Период</span>
              <div className="rp-period">
                <AppDateRangePicker className="rp-range" from={from} to={to} now={today()} label="Период отчёта"
                  onFromChange={setFrom} onToChange={setTo} />
                {(['this_month', 'last_month'] as const).map((item) => (
                  <button key={item} type="button"
                          className={mode === item ? 'rp-chip rp-chip--on' : 'rp-chip'}
                          aria-pressed={mode === item}
                          onClick={() => {
                            const [a, b] = periodRange(item, today());
                            setFrom(a);
                            setTo(b);
                          }}>
                    {item === 'this_month' ? 'Этот месяц' : 'Прошлый месяц'}
                  </button>
                ))}
              </div>
              {badOrder && <span className="rp-bad" role="alert">Дата окончания раньше даты начала</span>}
              {!badOrder && tooLong && (
                <span className="rp-bad" role="alert">
                  В один отчёт помещается не больше {maxDays} дней; выбрано {span}
                </span>
              )}
            </div>

            <div className="rp-row3">
              <label className="rp-field">
                <span className="rp-label">Регионы</span>
                <AppSelectField className="rp-field-select" label="Регион" value={region} onChange={setRegion}>
                    <option value="">Все регионы</option>
                    {regions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </AppSelectField>
              </label>
              <div className="rp-field">
                <span className="rp-label">Офисы</span>
                <MultiPick label="Офисы" all="Все офисы"
                           options={offices} value={officeIds} onChange={setOfficeIds} />
              </div>
              <div className="rp-field">
                <span className="rp-label">Отделы</span>
                <MultiPick label="Отделы" all="Все отделы"
                           options={departments} value={departmentIds} onChange={setDepartmentIds} />
              </div>
            </div>

            <div className="rp-people">
              <div className="rp-field rp-field--grow">
                <span className="rp-label">Сотрудники</span>
                <PersonPick value={person} fallbackName={preview.data?.employee_name ?? null}
                            region={region} officeIds={officeIds} inactive={inactive}
                            onChange={setPerson} />
              </div>
              <label className="rp-check rp-check--inline">
                <input type="checkbox" checked={inactive} onChange={(event) => setInactive(event.target.checked)} />
                <span>Включить неактивных</span>
                <span className="rp-info" title="Уволенные и приостановленные сотрудники — за те дни периода, когда их назначение ещё действовало">
                  <AppIcon name="alert" size={16} />
                </span>
              </label>
            </div>

            <div className="rp-fields-head">
              <span className="rp-label rp-label--strong">Какие данные включить</span>
              {chosen && (
                <button type="button" className="rp-link"
                        onClick={() => setFields(fields.length === chosen.fields.length
                          ? [] : chosen.fields.map((one) => one.key))}>
                  {fields.length === chosen.fields.length ? 'Снять все поля' : 'Выбрать все поля'}
                </button>
              )}
            </div>
            {chosen ? (
              <div className="rp-fields"
                   style={{ '--rp-field-rows': Math.ceil(chosen.fields.length / 2) } as CSSProperties}>
                {chosen.fields.map((item) => (
                  <label key={item.key} className="rp-check">
                    <input type="checkbox" checked={fields.includes(item.key)}
                           onChange={(event) => setFields((was) => event.target.checked
                             ? [...was, item.key] : was.filter((key) => key !== item.key))} />
                    <span>{item.title}</span>
                  </label>
                ))}
              </div>
            ) : (
              <p className="rp-muted rp-fields-empty">Выберите тип отчёта — здесь появятся его поля.</p>
            )}
            {chosen && fields.length === 0 && (
              <span className="rp-bad" role="alert">Выберите хотя бы одно поле</span>
            )}

            <div className="rp-format">
              <div className="rp-field">
                <span className="rp-label rp-label--strong">Формат файла</span>
                <div className="rp-formats" role="radiogroup" aria-label="Формат файла">
                  {(['xlsx', 'csv'] as const).map((item) => (
                    <button key={item} type="button" role="radio" aria-checked={fmt === item}
                            className={fmt === item ? 'rp-fmt rp-fmt--on' : 'rp-fmt'}
                            onClick={() => setFmt(item)}>
                      <span className="rp-fmt__dot" aria-hidden="true">
                        {fmt === item && <AppIcon name="check" size={16} />}
                      </span>
                      {item === 'xlsx' ? 'Excel' : 'CSV'} <span className="rp-fmt__ext">.{item}</span>
                    </button>
                  ))}
                </div>
              </div>
              <label className="rp-field rp-field--grow">
                <span className="rp-label rp-label--strong">
                  Имя файла <span className="rp-label__soft">(необязательно)</span>
                </span>
                <input className="rp-input" value={name} maxLength={120} aria-label="Имя файла"
                       placeholder={preview.data?.file_name.replace(/\.(xlsx|csv)$/, '')
                         ?? (chosen ? `${chosen.title}_…` : 'Имя файла')}
                       onChange={(event) => setName(event.target.value)} />
              </label>
            </div>

            <p className="rp-hint">
              <AppIcon name="alert" size={16} />
              Большие отчёты формируются в фоне — страницу можно закрыть.
            </p>
          </div>

          {/* --- состав --- */}
          <Composition
            chosen={chosen}
            valid={valid && mayKind}
            fmt={fmt}
            from={from}
            to={to}
            preview={preview}
            busy={busyOrder}
            notice={notice}
            error={orderError}
            onOrder={() => void order()}
            templateNote={templateNote}
            onTemplateSaved={(template) => {
              setTemplateNote({ good: true, text: `Шаблон «${template.name}» сохранён` });
              reloadTemplates();
            }}
            spec={spec}
          />
        </section>

        {/* --- история --- */}
        <section className="rp-history">
          <div className="rp-history__head">
            <h2 className="rp-h2">Последние выгрузки</h2>
            <div className="rp-tabs" role="tablist" aria-label="Состояние выгрузок">
              {TABS.map((item) => (
                <button key={item.key} type="button" role="tab" aria-selected={item.key === tab}
                        className={item.key === tab ? 'rp-tab rp-tab--on' : 'rp-tab'}
                        onClick={() => patchParam(setParams, 'tab', item.key === 'all' ? null : item.key)}>
                  {item.title}
                  {counts && <span className="rp-tab__count">{tabCount(item.key, counts)}</span>}
                </button>
              ))}
            </div>
            <AppSelectField className="rp-select--authors" label="Чьи выгрузки показывать" value={mineOnly ? 'mine' : 'all'}
                      disabled={!maySeeOthers}
                      onChange={(value) => patchParam(setParams, 'authors', value === 'all' ? 'all' : null)}>
                <option value="mine">Только мои</option>
                {maySeeOthers && <option value="all">Все авторы</option>}
            </AppSelectField>
          </div>

          {live.state === 'loading' && <p className="rp-empty">Загружаем историю…</p>}
          {live.state === 'denied' && <p className="rp-empty">Нет доступа к истории выгрузок.</p>}
          {live.state === 'error' && (
            <p className="rp-empty rp-bad">
              Не удалось загрузить историю выгрузок.{' '}
              <button type="button" className="rp-link" onClick={refresh}>Повторить</button>
            </p>
          )}
          {live.state === 'ready' && (
            <>
              {live.stale && (
                <p className="rp-stale" role="status">Данные не обновились — показано последнее полученное состояние</p>
              )}
              {live.data.items.length === 0 ? (
                <p className="rp-empty">
                  {tab === 'all'
                    ? 'Выгрузок пока нет. Выберите тип отчёта и сформируйте первый.'
                    : 'В этом состоянии выгрузок нет.'}
                </p>
              ) : (
                <div className="rp-scroll">
                  <table className="rp-table table-cards">
                    <thead>
                      <tr>
                        <th>Отчёт</th>
                        <th>Параметры</th>
                        <th>Автор</th>
                        <th>Создан</th>
                        <th>Статус</th>
                        <th>Действия</th>
                        <th aria-label="Ещё" />
                      </tr>
                    </thead>
                    <tbody>
                      {live.data.items.map((job) => (
                        <HistoryRow
                          key={job.id}
                          job={job}
                          mine={job.requested_by_user_id === me}
                          mayDownload={job.requested_by_user_id === me || mayDownloadAny}
                          params={jobParams(job, allOffices, regions)}
                          busy={Boolean(rowBusy[job.id])}
                          error={rowError?.id === job.id ? rowError.text : null}
                          onAct={(what) => void act(job, what)}
                          onOpen={() => applyFilters(job.kind, job.fmt, job.filters ?? {})}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="rp-history__foot">
                <span className="rp-muted">
                  {shownTitle(live.data)}
                  {more.kind && <span className="rp-bad"> · не удалось дочитать список</span>}
                  {live.data.hasMore && (
                    <button type="button" className="rp-link" disabled={more.busy} onClick={() => void loadMore()}>
                      {more.busy ? ' Читаем…' : ' Показать ещё'}
                    </button>
                  )}
                </span>
                <span className="rp-lock">
                  <AppIcon name="lock" size={16} />
                  Файлы доступны только заказавшему
                  {retentionHours !== null && ` и автоматически удаляются через ${retentionTitle(retentionHours)}`}
                </span>
              </div>
            </>
          )}
        </section>
      </div>
    </AppShell>
  );
}

// --- предпросмотр ------------------------------------------------------------

type PreviewState = {
  data: api.ReportPreview | null;
  loading: boolean;
  error: string | null;
  retry: () => void;
};

/**
 * Предпросмотр под текущие параметры.
 *
 * Прежняя таблица остаётся на месте, пока идёт новый запрос: блок не
 * мигает пустотой на каждую галочку. Запрос откладывается на мгновение,
 * а устаревший отменяется — ответ на старые параметры не перетрёт новый.
 */
function usePreview(spec: api.ReportSpec | null, fmt: Fmt, limit: number): PreviewState {
  const [data, setData] = useState<api.ReportPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const key = spec ? JSON.stringify([spec, fmt, limit]) : '';

  useEffect(() => {
    if (!spec) {
      setLoading(false);
      setError(null);
      return undefined;
    }
    const controller = new AbortController();
    setLoading(true);
    const timer = setTimeout(() => {
      api.reportPreview({ ...spec, fmt, limit }, controller.signal)
        .then((answer) => {
          setData(answer);
          setError(null);
          setLoading(false);
        })
        .catch((failure: unknown) => {
          if (controller.signal.aborted) return;
          setError(previewMessage(failure));
          setLoading(false);
        });
    }, PREVIEW_DELAY_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
    // `key` — полный слепок параметров.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  const retry = useCallback(() => setNonce((value) => value + 1), []);
  return { data, loading, error, retry };
}

type CompositionProps = {
  chosen: api.ReportCatalog['kinds'][number] | null;
  valid: boolean;
  fmt: Fmt;
  from: string;
  to: string;
  preview: PreviewState;
  busy: boolean;
  notice: string | null;
  error: string | null;
  onOrder: () => void;
  templateNote: { good: boolean; text: string } | null;
  onTemplateSaved: (template: api.ReportTemplate) => void;
  spec: api.ReportSpec | null;
};

function Composition({
  chosen, valid, fmt, from, to, preview, busy, notice, error, onOrder,
  templateNote, onTemplateSaved, spec,
}: CompositionProps) {
  const [expanded, setExpanded] = useState(false);
  const data = preview.data && chosen && preview.data.kind === chosen.key ? preview.data : null;
  const blocked = data ? data.offices === 0 : false;
  const ready = valid && Boolean(data) && !preview.error && !blocked;
  const rows = data ? (expanded ? data.rows : data.rows.slice(0, SHORT_PREVIEW)) : [];
  const fmtTitle = fmt === 'xlsx' ? 'Excel (.xlsx)' : 'CSV (.csv)';

  let badge: ReactNode;
  if (!chosen) badge = <span className="rp-badge rp-badge--muted">Выберите тип отчёта</span>;
  else if (!valid) badge = <span className="rp-badge rp-badge--warn">Проверьте параметры</span>;
  else if (ready) badge = <span className="rp-badge rp-badge--good">Готов к формированию</span>;
  else if (preview.error || blocked) badge = <span className="rp-badge rp-badge--warn">Нельзя сформировать</span>;
  else badge = <span className="rp-badge rp-badge--muted">Считаем…</span>;

  return (
    <div className="rp-compose">
      <div className="rp-compose__head">
        <h2 className="rp-h2">Что попадёт в файл</h2>
        {badge}
      </div>

      <div className={preview.loading && data ? 'rp-paper rp-paper--busy' : 'rp-paper'} aria-busy={preview.loading}>
        {!chosen ? (
          <p className="rp-paper__empty">Выберите тип отчёта — здесь появятся его строки.</p>
        ) : (
          <>
            <div className="rp-paper__head">
              <div>
                <p className="rp-paper__title">{chosen.title}</p>
                <p className="rp-paper__line">{periodLong(from, to)}</p>
                <p className="rp-paper__line">
                  {data
                    ? `${number(data.offices)} ${plural(data.offices, OFFICES)} · ${number(data.employees)} ${plural(data.employees, PEOPLE)}`
                    : '—'}
                </p>
              </div>
              <span className="rp-paper__fmt">
                <span className={fmt === 'xlsx' ? 'rp-file rp-file--xlsx' : 'rp-file rp-file--csv'} aria-hidden="true">
                  {fmt === 'xlsx' ? 'X' : 'CSV'}
                </span>
                {fmtTitle}
              </span>
            </div>

            {preview.error && (
              <p className="rp-bad rp-paper__error" role="alert">
                {preview.error}{' '}
                <button type="button" className="rp-link" onClick={preview.retry}>Повторить</button>
              </p>
            )}

            {!data && !preview.error && valid && <p className="rp-paper__empty">Собираем предпросмотр…</p>}
            {!valid && <p className="rp-paper__empty">Заполните параметры, чтобы увидеть строки.</p>}

            {data && (
              <>
                <div className="rp-preview">
                  <table className="rp-preview__table">
                    <thead>
                      <tr>{data.columns.map((column) => <th key={column.key}>{column.title}</th>)}</tr>
                    </thead>
                    <tbody>
                      {rows.map((row, index) => (
                        <tr key={index}>
                          {row.map((cell, at) => (
                            <td key={at} title={cell.text ?? undefined}>
                              {cell.tone
                                ? <span className={`rp-pill rp-pill--${cell.tone}`}>{cell.text}</span>
                                : (cell.text ?? '—')}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {data.rows.length === 0 && (
                    <p className="rp-paper__empty">
                      {data.sampled_days
                        ? `За первые ${data.sampled_days} ${plural(data.sampled_days, ['день', 'дня', 'дней'])} периода строк нет.`
                        : 'Под эти параметры строк нет.'}
                    </p>
                  )}
                </div>
                {data.warnings.map((warning) => (
                  <p key={warning} className="rp-warn"><AppIcon name="alert" size={16} />{warning}</p>
                ))}
                <div className="rp-paper__foot">
                  <span>
                    <AppIcon name="doc" size={16} />
                    <span>
                      {data.estimate_exact ? '' : 'Примерно '}
                      <b>{number(data.rows_estimate)}</b> {plural(data.rows_estimate, ROWS)}
                    </span>
                  </span>
                  <span>
                    <AppIcon name="grid" size={16} />
                    <span>{data.columns.length} {plural(data.columns.length, COLUMNS)}</span>
                  </span>
                  <span><AppIcon name="sheet" size={16} /><span>{fmtTitle}</span></span>
                  {data.rows.length > SHORT_PREVIEW && (
                    <button type="button" className="rp-link rp-paper__more" onClick={() => setExpanded((was) => !was)}>
                      <AppIcon name="eye" size={16} />
                      {expanded ? 'Свернуть предпросмотр' : `Предпросмотр ${data.rows.length} ${plural(data.rows.length, ROWS)}`}
                    </button>
                  )}
                </div>
              </>
            )}
          </>
        )}
      </div>

      <div className="rp-actions">
        <SaveTemplate spec={spec} fmt={fmt} onSaved={onTemplateSaved} />
        <button type="button" className="rp-btn rp-btn--primary rp-order" disabled={!ready || busy} onClick={onOrder}>
          {busy ? 'Ставим в очередь…' : 'Сформировать отчёт'}
          {!busy && <AppIcon name="arrow" size={18} />}
        </button>
      </div>
      {error ? (
        <p className="rp-after rp-bad" role="alert">
          {error}{' '}
          <button type="button" className="rp-link" disabled={busy} onClick={onOrder}>Повторить</button>
        </p>
      ) : notice ? (
        <p className="rp-after rp-good" role="status">{notice}</p>
      ) : templateNote ? (
        <p className={templateNote.good ? 'rp-after rp-good' : 'rp-after rp-bad'} role="status">{templateNote.text}</p>
      ) : (
        <p className="rp-after">Файл появится в истории выгрузок</p>
      )}
    </div>
  );
}

function SaveTemplate({ spec, fmt, onSaved }: {
  spec: api.ReportSpec | null;
  fmt: Fmt;
  onSaved: (template: api.ReportTemplate) => void;
}) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (!spec || busy) return;
    setBusy(true);
    setError(null);
    try {
      const template = await api.saveReportTemplate({ ...spec, fmt, template_name: title.trim() });
      setOpen(false);
      setTitle('');
      onSaved(template);
    } catch (failure) {
      setError(failure instanceof ApiFailure && failure.fields['template_name']?.[0]
        ? failure.fields['template_name'][0]
        : messageFor(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rp-pop-anchor">
      <button type="button" className="rp-btn rp-btn--outline" disabled={!spec}
              aria-expanded={open}
              onClick={() => {
                setOpen((was) => !was);
                if (!title && spec) setTitle(KIND_LOOK[spec.kind].title);
              }}>
        <AppIcon name="doc" size={18} />
        Сохранить как шаблон
      </button>
      {open && (
        <Popover onClose={() => setOpen(false)} className="rp-save">
          <label className="rp-field">
            <span className="rp-label">Название шаблона</span>
            <input className="rp-input" value={title} maxLength={120} aria-label="Название шаблона"
                   autoFocus onChange={(event) => setTitle(event.target.value)}
                   onKeyDown={(event) => { if (event.key === 'Enter') void save(); }} />
          </label>
          <p className="rp-muted">Сохранятся тип, фильтры, поля, формат и имя файла. Период «Этот месяц» и «Прошлый месяц» пересчитывается при открытии.</p>
          {error && <p className="rp-bad" role="alert">{error}</p>}
          <div className="rp-save__buttons">
            <button type="button" className="rp-btn rp-btn--ghost" onClick={() => setOpen(false)}>Отмена</button>
            <button type="button" className="rp-btn rp-btn--primary" disabled={busy || !title.trim()}
                    onClick={() => void save()}>
              {busy ? 'Сохраняем…' : 'Сохранить'}
            </button>
          </div>
        </Popover>
      )}
    </div>
  );
}

// --- строка истории ----------------------------------------------------------

type RowProps = {
  job: api.ExportJob;
  mine: boolean;
  /** Своё — всегда; чужое — только с `reports.download_any`. */
  mayDownload: boolean;
  params: string;
  busy: boolean;
  error: string | null;
  onAct: (what: 'cancel' | 'retry' | 'hide' | 'repeat') => void;
  onOpen: () => void;
};

function HistoryRow({ job, mine, mayDownload, params, busy, error, onAct, onOpen }: RowProps) {
  const [menu, setMenu] = useState(false);
  const status = job.display_status;
  const percent = progressPercent(job);
  const ready = status === 'SUCCEEDED';
  const downloadable = ready && mayDownload;
  const size = sizeTitle(job.size_bytes);
  const builder = job.filters?.builder === 2;

  return (
    <tr>
      <td data-label="Отчёт">
        <span className="rp-job">
          <span className={job.fmt === 'xlsx' ? 'rp-file rp-file--xlsx' : 'rp-file rp-file--csv'} aria-hidden="true">
            {job.fmt === 'xlsx' ? 'X' : 'CSV'}
          </span>
          <span className="rp-job__title">{jobTitle(job)}</span>
        </span>
      </td>
      <td className="rp-dim" data-label="Параметры">{params}</td>
      <td data-label="Автор">{mine ? 'Вы' : (job.requested_by ?? 'Другой сотрудник')}</td>
      <td className="rp-dim" data-label="Создан">{momentTitle(job.created_at)}</td>
      <td data-label="Статус">
        {status === 'RUNNING' && percent !== null ? (
          <span className="rp-progress" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}
                aria-label="Готовность отчёта">
            <span className="rp-progress__bar"><span style={{ width: `${percent}%` }} /></span>
            <span className="rp-progress__value">{percent}%</span>
          </span>
        ) : (
          <span className="rp-status">
            <span className={`rp-dot-pill rp-dot-pill--${status.toLowerCase()}`}>{STATUS_TITLE[status]}</span>
            {status === 'RUNNING' && job.progress_rows > 0 && (
              <span className="rp-dim">{number(job.progress_rows)} {plural(job.progress_rows, ROWS)}</span>
            )}
            {ready && job.expires_at && (
              <span className="rp-dim" title={size ? `Размер ${size}` : undefined}>
                до {momentTitle(job.expires_at).replace('Сегодня, ', 'сегодня ')}
              </span>
            )}
          </span>
        )}
        {status === 'FAILED' && job.error_message && (
          <span className="rp-reason">{job.error_message}</span>
        )}
      </td>
      <td>
        {downloadable && (
          <a className="rp-action" href={api.downloadUrl(job.id)} target="_blank" rel="noopener noreferrer">
            <AppIcon name="download" size={16} />Скачать
          </a>
        )}
        {status === 'RUNNING' && <span className="rp-pill rp-pill--info">Формируется</span>}
        {status === 'QUEUED' && (
          <button type="button" className="rp-action" disabled={busy} onClick={() => onAct('cancel')}>
            {busy ? 'Отменяем…' : 'Отменить'}
          </button>
        )}
        {(status === 'FAILED' || status === 'CANCELLED') && (
          <button type="button" className="rp-action" disabled={busy} onClick={() => onAct('retry')}>
            <AppIcon name="refresh" size={16} />{busy ? 'Ставим…' : 'Повторить'}
          </button>
        )}
        {/* Повторить истёкший можно только заказ конструктора: у старого
            заказа параметров для нового задания нет, а сервер повтор
            готового отклонит. Ему остаётся «Открыть параметры». */}
        {status === 'EXPIRED' && builder && (
          <button type="button" className="rp-action" disabled={busy} onClick={() => onAct('repeat')}>
            <AppIcon name="refresh" size={16} />{busy ? 'Ставим…' : 'Повторить'}
          </button>
        )}
        {error && <span className="rp-reason" role="alert">{error}</span>}
      </td>
      <td className="rp-more-cell">
        <div className="rp-pop-anchor">
          <button type="button" className="rp-icon-btn rp-more" aria-label={`Действия: ${jobTitle(job)}`}
                  aria-expanded={menu} onClick={() => setMenu((was) => !was)}>
            <span aria-hidden="true">•••</span>
          </button>
          {menu && (
            <Popover onClose={() => setMenu(false)} className="rp-menu">
              {downloadable && (
                <a className="rp-menu__item" href={api.downloadUrl(job.id)} target="_blank" rel="noopener noreferrer"
                   onClick={() => setMenu(false)}>
                  Скачать
                </a>
              )}
              {builder && status !== 'QUEUED' && status !== 'RUNNING' && (
                <button type="button" className="rp-menu__item" onClick={() => { setMenu(false); onAct('repeat'); }}>
                  Повторить с теми же параметрами
                </button>
              )}
              <button type="button" className="rp-menu__item" onClick={() => { setMenu(false); onOpen(); }}>
                Открыть параметры
              </button>
              {status !== 'RUNNING' && (
                <button type="button" className="rp-menu__item rp-menu__item--bad"
                        onClick={() => { setMenu(false); onAct('hide'); }}>
                  Удалить из моей истории
                </button>
              )}
            </Popover>
          )}
        </div>
      </td>
    </tr>
  );
}

// --- выбор офисов, отделов и сотрудника -------------------------------------

function MultiPick({ label, all, options, value, onChange }: {
  label: string;
  all: string;
  options: { id: string; name: string }[];
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return <AppMultiSelect label={label} empty={all} options={options.map((item) => ({ value: item.id, label: item.name }))} value={value} onChange={onChange} />;
}

function PersonPick({ value, fallbackName, region, officeIds, inactive, onChange }: {
  value: Person | null;
  fallbackName: string | null;
  region: string;
  officeIds: string[];
  inactive: boolean;
  onChange: (next: Person | null) => void;
}) {
  const [text, setText] = useState('');
  const [found, setFound] = useState<api.EmployeeRow[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    const search = text.trim();
    if (search.length < 2) {
      setFound([]);
      return undefined;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api.employees({
        search,
        limit: '8',
        ...(inactive ? {} : { status: 'ACTIVE' }),
        ...(officeIds.length === 1 ? { office_id: officeIds[0]! } : region ? { region_id: region } : {}),
      }, controller.signal)
        .then((page) => {
          setFound(page.items);
          setError(false);
          setOpen(true);
        })
        .catch(() => {
          if (!controller.signal.aborted) setError(true);
        });
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [text, inactive, region, officeIds]);

  if (value) {
    return (
      <span className="rp-person rp-person--chosen">
        <AppIcon name="user" size={16} />
        <span className="rp-person__name">{value.name ?? fallbackName ?? 'Выбранный сотрудник'}</span>
        <button type="button" className="rp-icon-btn" aria-label="Все сотрудники" onClick={() => onChange(null)}>
          <AppIcon name="close" size={16} />
        </button>
      </span>
    );
  }

  return (
    <div className="rp-pop-anchor">
      <span className="rp-person">
        <AppIcon name="search" size={16} />
        <input value={text} placeholder="Все сотрудники" aria-label="Сотрудник"
               onFocus={() => found.length && setOpen(true)}
               onChange={(event) => setText(event.target.value)} />
      </span>
      {open && text.trim().length >= 2 && (
        <Popover onClose={() => setOpen(false)} className="rp-found">
          {error && <p className="rp-bad">Не удалось найти сотрудников</p>}
          {!error && found.length === 0 && <p className="rp-muted">Никого не нашлось</p>}
          {found.map((item) => (
            <button key={item.id} type="button" className="rp-menu__item"
                    onClick={() => {
                      onChange({ id: item.id, name: item.full_name });
                      setText('');
                      setOpen(false);
                    }}>
              <b>{item.full_name}</b>
              {item.employee_number && <span className="rp-dim"> · {item.employee_number}</span>}
            </button>
          ))}
        </Popover>
      )}
    </div>
  );
}

function Popover({ children, onClose, className }: {
  children: ReactNode;
  onClose: () => void;
  className: string;
}) {
  return <AppPopover open onClose={onClose} className={`rp-pop ${className}`}>{children}</AppPopover>;
}

// --- вспомогательное ---------------------------------------------------------

/** Дней в периоде включительно. Ноль и меньше — даты перепутаны. */
export function days(from: string, to: string): number {
  const a = Date.parse(`${from}T00:00:00Z`);
  const b = Date.parse(`${to}T00:00:00Z`);
  if (Number.isNaN(a) || Number.isNaN(b)) return 0;
  return Math.round((b - a) / 86_400_000) + 1;
}

/** «Показаны все N» — только когда сервер сказал, что дальше ничего нет. */
export function shownTitle(data: { items: unknown[]; hasMore: boolean; counts: { total: number } | null }): string {
  const shown = data.items.length;
  const forms: [string, string, string] = ['выгрузка', 'выгрузки', 'выгрузок'];
  if (!data.hasMore && data.counts && data.counts.total === shown) {
    return `Показаны все ${shown} ${plural(shown, forms)}`;
  }
  return `Показано ${shown} ${plural(shown, forms)}`;
}

export function retentionTitle(hours: number): string {
  if (hours % 24 === 0) {
    const value = hours / 24;
    return `${value} ${plural(value, ['день', 'дня', 'дней'])}`;
  }
  return `${hours} ${plural(hours, ['час', 'часа', 'часов'])}`;
}

function newKey(): string {
  const source = globalThis.crypto;
  if (source && typeof source.randomUUID === 'function') return source.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const random = Math.floor(Math.random() * 16);
    return (char === 'x' ? random : (random % 4) + 8).toString(16);
  });
}

function previewMessage(error: unknown): string {
  if (error instanceof ApiFailure) {
    const first = Object.values(error.fields)[0]?.[0];
    if (first) return first;
    if (error.status === 403) return 'Нет доступа к этим данным или офисам';
    if (error.status === 404) return 'Выбранный офис или сотрудник не найден';
  }
  return `Не удалось собрать предпросмотр: ${messageFor(error)}`;
}

function orderMessage(error: unknown): string {
  if (error instanceof ApiFailure) {
    if (error.status === 409) {
      return 'Слишком много незавершённых выгрузок. Дождитесь окончания или отмените лишние';
    }
    if (error.status === 403) return 'Нет доступа к этому отчёту или к выбранным офисам';
    const first = Object.values(error.fields)[0]?.[0];
    if (first) return first;
  }
  return `Отчёт не поставлен в очередь: ${messageFor(error)}`;
}

function patchParam(
  setParams: ReturnType<typeof useSearchParams>[1],
  key: string,
  value: string | null,
) {
  setParams((was) => {
    const next = new URLSearchParams(was);
    if (value) next.set(key, value);
    else next.delete(key);
    return next;
  });
}
