/**
 * Настройки рабочего пространства.
 *
 * Что здесь редактируется, а что только показывается — решает не
 * оформление, а наличие настройки в предметной модели. Правило одно:
 * переключатель без поведения на сервере хуже его отсутствия, потому
 * что он обещает изменение, которого не произойдёт. Поэтому допуск
 * опоздания, радиус геозоны и сроки жизни QR-кодов показаны указателями
 * на владельца, а каналы уведомлений — фактическим состоянием.
 *
 * Часовой пояс здесь — пояс ПОКАЗА. Отметки, графики и рабочие дни
 * считаются по поясу офиса и от этой настройки не зависят: смена пояса
 * отображения не переписывает события и не пересчитывает историю.
 *
 * Черновик отделён от сохранённого намеренно. Панель «есть
 * несохранённые изменения» появляется от фактического отличия, а не от
 * того, что человек тронул поле; на сервер уходят только изменённые
 * поля, иначе два администратора отменяли бы правки друг друга даже
 * в разных полях одной группы.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { AppShell } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { AppSelectField } from '../components/AppSelect';
import { useSession } from '../features/auth/session';
import { useBlock } from '../features/dashboard/data';
import { moment } from '../features/time/zone';
import {
  PERSONAL,
  SECTION_OF,
  TABS,
  TAB_SUBTITLE,
  changedFields,
  changedOnly,
  demoMode,
  documentTitle,
  isTab,
  megabytes,
  sectionOf,
  stateTitle,
  timezones,
  zoneLabel,
  type Draft,
  type Tab,
} from '../features/settings/model';

export function SettingsPage() {
  const session = useSession();
  const mine = useMemo(
    () =>
      new Set(
        session.status === 'authenticated' ? session.user.permissions : [],
      ),
    [session],
  );
  const maySettings = mine.has('settings.manage');
  const mayAudit = mine.has('audit.read');
  const zone = session.status === 'authenticated' ? session.user.timezone : '';

  const [params, setParams] = useSearchParams();
  const raw = params.get('tab');
  const tab: Tab = isTab(raw) ? raw : 'org';

  const [attempt, setAttempt] = useState(0);
  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  const [all, reloadAll] = useBlock(
    (signal) => api.settings(signal),
    `settings|${attempt}`,
    maySettings,
  );

  // Подключения спрашиваются только там, где они показаны: открытая
  // страница настроек не обязана ходить за состоянием каждого сервиса.
  const [links, reloadLinks] = useBlock(
    (signal) => api.integrations(signal),
    `integrations|${attempt}|${tab}`,
    maySettings && (tab === 'integrations' || tab === 'notifications'),
  );

  const open = useCallback(
    (next: Tab) => setParams((was) => {
      const copy = new URLSearchParams(was);
      copy.set('tab', next);
      return copy;
    }, { replace: false }),
    [setParams],
  );

  const org = session.status === 'authenticated' ? session.user : null;
  const items = all.state === 'ready' ? all.data.items : [];
  const named = sectionOf(items, SECTION_OF['org']);
  const title = (named?.values['name'] as string | null) ?? org?.organization_code ?? '';

  return (
    <AppShell breadcrumb="Настройки" section="settings">
      <div className="head head--tight">
        <div>
          <h1 className="head__title">
            Настройки
            {demoMode() && <span className="pill pill--demo">Демо-данные</span>}
          </h1>
          <p className="head__sub">
            {title
              ? `Параметры рабочего пространства ${title}`
              : 'Параметры рабочего пространства'}
          </p>
        </div>
      </div>

      {!maySettings && tab !== 'me' && (
        <p className="empty empty--bad">
          Нет права на настройки организации. Здесь меняются правила,
          действующие на всех, поэтому это отдельное разрешение — попросите
          <span> </span>
          <span className="mono">settings.manage</span> у администратора.
          Личные предпочтения открыты и без него.
        </p>
      )}

      <div className="setup">
        <nav className="setup__menu" aria-label="Разделы настроек">
          <p className="setup__group">Разделы</p>
          <ul className="setup__list">
            {TABS.map((item) => (
              <li key={item.key}>
                <button type="button"
                        className={`setup__item${tab === item.key ? ' setup__item--on' : ''}`}
                        aria-current={tab === item.key ? 'page' : undefined}
                        onClick={() => open(item.key)}>
                  <AppIcon name={item.icon} size={16} />
                  <span>{item.title}</span>
                </button>
              </li>
            ))}
          </ul>

          <div className="setup__rule" />
          <p className="setup__group">Личные</p>
          <ul className="setup__list">
            <li>
              <button type="button"
                      className={`setup__item${tab === 'me' ? ' setup__item--on' : ''}`}
                      aria-current={tab === 'me' ? 'page' : undefined}
                      onClick={() => open('me')}>
                <AppIcon name={PERSONAL.icon} size={16} />
                <span>{PERSONAL.title}</span>
              </button>
            </li>
          </ul>

          <p className="setup__foot">Настройки доступны по вашей роли.</p>
        </nav>

        <div className="setup__body">
          <div className="setup__intro">
            <h2 className="setup__title">
              {tab === 'me' ? PERSONAL.title
                : TABS.find((item) => item.key === tab)?.title}
            </h2>
            <p className="setup__lead">{TAB_SUBTITLE[tab]}</p>
          </div>

          {tab === 'me' ? (
            <Personal session={session} />
          ) : !maySettings ? null : all.state === 'loading' ? (
            <p className="empty" role="status">Загружаем настройки…</p>
          ) : all.state === 'denied' ? (
            <p className="empty empty--bad">Настройки закрыты вашей ролью.</p>
          ) : all.state === 'error' ? (
            <p className="empty empty--bad">
              Не удалось загрузить настройки.{' '}
              <button type="button" className="link" onClick={reloadAll}>
                Повторить
              </button>
            </p>
          ) : (
            <Content tab={tab} all={all.data} links={links} zone={zone}
                     mayAudit={mayAudit} onSaved={reload}
                     onRetryLinks={reloadLinks} />
          )}
        </div>
      </div>
    </AppShell>
  );
}

// --- содержимое разделов -----------------------------------------------------

type LinksBlock = ReturnType<typeof useBlock<api.Items<api.Integration>>>[0];

function Content({ tab, all, links, zone, mayAudit, onSaved, onRetryLinks }: {
  tab: Tab;
  all: api.SettingsAll;
  links: LinksBlock;
  zone: string;
  mayAudit: boolean;
  onSaved: () => void;
  onRetryLinks: () => void;
}) {
  const key = SECTION_OF[tab];
  const section = sectionOf(all.items, key);

  if (tab === 'org' && section) {
    return (
      <Editable section={section} zone={zone} mayAudit={mayAudit}
                lastChange={all.last_change} onSaved={onSaved}
                render={(draft, set, errors) => (
                  <Organization section={section} draft={draft} set={set}
                                errors={errors} />
                )} />
    );
  }
  if (tab === 'requests' && section) {
    return (
      <Editable section={section} zone={zone} mayAudit={mayAudit}
                lastChange={all.last_change} onSaved={onSaved}
                render={(draft, set, errors) => (
                  <Requests section={section} draft={draft} set={set}
                            errors={errors} />
                )} />
    );
  }
  if (tab === 'attendance') return <Attendance elsewhere={all.elsewhere} />;
  if (tab === 'notifications') {
    return <Notifications links={links} zone={zone} onRetry={onRetryLinks} />;
  }
  return <Connections links={links} zone={zone} onRetry={onRetryLinks} />;
}

/**
 * Обёртка редактируемой группы: черновик, панель сохранения и конфликт.
 *
 * Она же держит защиту от ухода с несохранённым — и от закрытия вкладки,
 * и от перехода внутри приложения.
 */
function Editable({ section, zone, mayAudit, lastChange, onSaved, render }: {
  section: api.SettingSection;
  zone: string;
  mayAudit: boolean;
  lastChange: api.LastChange | null;
  onSaved: () => void;
  render: (
    draft: Draft,
    set: (field: string, value: unknown) => void,
    errors: Record<string, string[]>,
  ) => React.ReactNode;
}) {
  const [saved, setSaved] = useState<Draft>(section.values);
  const [draft, setDraft] = useState<Draft>(section.values);
  const [version, setVersion] = useState<string | null>(section.updated_at);
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [sending, setSending] = useState(false);
  const busy = useRef(false);

  // Пришла другая группа — черновик прежней к ней не относится.
  const shown = useRef(section.key);
  useEffect(() => {
    if (shown.current === section.key) return;
    shown.current = section.key;
    setSaved(section.values);
    setDraft(section.values);
    setVersion(section.updated_at);
    setErrors({});
    setFailure(null);
    setStale(false);
  }, [section]);

  const changed = changedFields(saved, draft);
  const dirty = changed.length > 0;

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const set = useCallback((field: string, value: unknown) => {
    setDraft((was) => ({ ...was, [field]: value }));
    setErrors((was) => {
      if (!(field in was)) return was;
      const next = { ...was };
      delete next[field];
      return next;
    });
  }, []);

  const undo = useCallback(() => {
    // Возврат к последнему сохранённому, а не к заводским значениям:
    // «Отменить» отменяет правку, а не настройку организации.
    setDraft(saved);
    setErrors({});
    setFailure(null);
  }, [saved]);

  const submit = useCallback(async () => {
    if (busy.current || !dirty) return;
    busy.current = true;
    setSending(true);
    setErrors({});
    setFailure(null);
    try {
      const fresh = await api.saveSettings(
        section.key, changedOnly(saved, draft), version,
      );
      // Новой сохранённой версией становится ОТВЕТ сервера, а не то,
      // что отправляли: сервер обрезает пробелы и нормализует значения,
      // и черновик обязан показывать сохранённое, а не задуманное.
      setSaved(fresh.values);
      setDraft(fresh.values);
      setVersion(fresh.updated_at);
      setStale(false);
      onSaved();
    } catch (error) {
      if (error instanceof ApiFailure) {
        setErrors(error.fields);
        setStale(error.kind === 'conflict');
        setFailure(messageFor(error));
      } else {
        setFailure('Не удалось сохранить настройки.');
      }
    } finally {
      busy.current = false;
      setSending(false);
    }
  }, [dirty, draft, onSaved, saved, section.key, version]);

  return (
    <>
      <div className="setup__cols">
        <div className="setup__forms">{render(draft, set, errors)}</div>
        <aside className="setup__aside" aria-label="Контекст">
          <ScopeCard section={section} />
          <LastChangeCard change={lastChange} zone={zone} mayAudit={mayAudit} />
          <RelatedCard />
        </aside>
      </div>

      {stale && (
        <p className="note note--dim" role="alert">
          Настройки изменил кто-то ещё, пока вы правили эту страницу. Ваш
          черновик цел — загрузите свежую версию и перенесите правки, чтобы
          не отменить чужую работу.{' '}
          <button type="button" className="link"
                  onClick={() => { setStale(false); onSaved(); }}>
            Загрузить свежую версию
          </button>
        </p>
      )}
      {failure && !stale && (
        <p className="note note--dim" role="alert">{failure}</p>
      )}

      <div className="setup__save">
        <span className="setup__state">
          <span className={`setup__dot${dirty ? ' setup__dot--on' : ''}`}
                aria-hidden="true" />
          <span>
            <strong>
              {dirty ? 'Есть несохранённые изменения' : 'Всё сохранено'}
            </strong>
            <span className="setup__hint">
              {dirty
                ? 'Изменения применятся после сохранения.'
                : 'Изменения применяются сразу после сохранения.'}
            </span>
          </span>
        </span>
        <span className="setup__buttons">
          <button type="button" className="btn" disabled={!dirty || sending}
                  onClick={undo}>
            Отменить
          </button>
          <button type="button" className="btn btn--dark"
                  disabled={!dirty || sending}
                  onClick={() => void submit()}>
            {sending ? 'Сохраняем…' : 'Сохранить изменения'}
          </button>
        </span>
      </div>
    </>
  );
}

// --- организация -------------------------------------------------------------

function Organization({ section, draft, set, errors }: {
  section: api.SettingSection;
  draft: Draft;
  set: (field: string, value: unknown) => void;
  errors: Record<string, string[]>;
}) {
  const effective = (section.effective?.['timezone'] as string | undefined) ?? '';
  const chosen = (draft['crm_timezone'] as string | null) ?? '';
  const zones = useMemo(() => timezones(chosen || effective), [chosen, effective]);

  return (
    <>
      <section className="panel">
        <h3 className="setup__block"><AppIcon name="building" size={16} /> Основные сведения</h3>

        <Field label="Название рабочего пространства" name="name" errors={errors}>
          <input className="form-grid__input" type="text" aria-label="Название рабочего пространства"
                 value={(draft['name'] as string | null) ?? ''}
                 maxLength={255}
                 onChange={(event) => set('name', event.target.value)} />
        </Field>

        <Field label="Описание" name="description" errors={errors}>
          <textarea className="form-grid__input area" aria-label="Описание" rows={3}
                    maxLength={500}
                    value={(draft['description'] as string | null) ?? ''}
                    onChange={(event) => set('description', event.target.value)} />
        </Field>
        <p className="field__hint">{section.help['description']}</p>
      </section>

      <section className="panel">
        <h3 className="setup__block"><AppIcon name="clock" size={16} /> Дата и время</h3>

        <Field label="Часовой пояс CRM" name="crm_timezone" errors={errors}>
          <AppSelectField label="Часовой пояс CRM" value={chosen} searchable
                  onChange={(value) => set('crm_timezone', value || null)}>
            <option value="">
              {effective
                ? `Как у первого офиса — сейчас ${zoneLabel(effective)}`
                : 'Как у первого офиса'}
            </option>
            {zones.map((name) => (
              <option key={name} value={name}>{zoneLabel(name)}</option>
            ))}
          </AppSelectField>
        </Field>

        <div className="setup__pair">
          <Field label="Формат даты" name="date_format" errors={errors}>
            <input className="form-grid__input" type="text" readOnly
                   aria-label="Формат даты" value="08 сент. 2026" />
          </Field>
          <Field label="Формат времени" name="time_format" errors={errors}>
            <input className="form-grid__input" type="text" readOnly
                   aria-label="Формат времени" value="24 часа · 14:30" />
          </Field>
        </div>
        <p className="field__hint">
          Форматы задаются русской локалью и пока не настраиваются.
          Переключатель без единого места применения показывал бы выбор,
          которого нет.
        </p>

        <p className="note note--dim">
          <AppIcon name="alert" size={16} /> Отметки и графики рассчитываются по
          часовому поясу офиса. Пояс CRM меняет только показ времени там, где
          у строки нет своего офиса, — журнал действий, список уведомлений,
          карточки учётных записей.
        </p>

        <Field label="Запасной пояс организации" name="default_timezone" errors={errors}>
          <AppSelectField label="Запасной пояс организации" value={(draft['default_timezone'] as string | null) ?? ''} searchable
                  onChange={(value) => set('default_timezone', value)}>
            {zones.map((name) => (
              <option key={name} value={name}>{zoneLabel(name)}</option>
            ))}
          </AppSelectField>
        </Field>
        <p className="field__hint">{section.help['default_timezone']}</p>
      </section>

      <section className="panel">
        <h3 className="setup__block"><AppIcon name="globe" size={16} /> Язык интерфейса</h3>
        <Field label="Язык по умолчанию" name="language" errors={errors}>
          <input className="form-grid__input" type="text" readOnly
                 aria-label="Язык по умолчанию" value="Русский" />
        </Field>
        <p className="field__hint">
          Другой локализации в CRM пока нет. Список из одного языка честнее
          выпадающего списка, который меняет подпись и ничего больше.
          Язык сотрудника в боте и Mini App хранится в его карточке.
        </p>
      </section>
    </>
  );
}

// --- заявки и документы ------------------------------------------------------

function Requests({ section, draft, set, errors }: {
  section: api.SettingSection;
  draft: Draft;
  set: (field: string, value: unknown) => void;
  errors: Record<string, string[]>;
}) {
  const storable =
    (section.effective?.['storable_document_types'] as string[] | undefined) ?? [];
  const allowed = (draft['allowed_document_types'] as string[] | undefined) ?? [];

  const toggle = (mime: string) => {
    const next = allowed.includes(mime)
      ? allowed.filter((item) => item !== mime)
      : [...allowed, mime];
    set('allowed_document_types', next);
  };

  return (
    <>
      <section className="panel">
        <h3 className="setup__block"><AppIcon name="doc" size={16} /> Согласование</h3>
        <Switch field="require_hr_approval" draft={draft} set={set}
                title="Согласование HR обязательно" help={section.help} />
        <Switch field="employee_may_cancel_pending" draft={draft} set={set}
                title="Снятие заявки самим сотрудником"
                help={section.help} />
        <Switch field="cancelling_approved_requires_hr" draft={draft} set={set}
                title="Отмена подтверждённого — только через отдел кадров"
                help={section.help} />
        <Switch field="extensions_allowed" draft={draft} set={set}
                title="Продление больничного" help={section.help} />
      </section>

      <section className="panel">
        <h3 className="setup__block"><AppIcon name="archive" size={16} /> Справки</h3>
        <Switch field="document_required" draft={draft} set={set}
                title="Справка обязательна при подаче" help={section.help} />
        <Switch field="document_can_be_added_later" draft={draft} set={set}
                title="Справку разрешено донести после выхода" help={section.help} />

        <Field label="Справка обязательна с дня" name="document_required_from_day"
               errors={errors}>
          <input className="form-grid__input" type="number" min={0} max={366}
                 aria-label="Справка обязательна с дня"
                 value={String(draft['document_required_from_day'] ?? 0)}
                 onChange={(event) =>
                   set('document_required_from_day', Number(event.target.value))} />
        </Field>
        <p className="field__hint">{section.help['document_required_from_day']}</p>

        <Field label="Предельный размер файла, байт" name="max_document_bytes"
               errors={errors}>
          <input className="form-grid__input" type="number" min={1}
                 aria-label="Предельный размер файла"
                 value={String(draft['max_document_bytes'] ?? 0)}
                 onChange={(event) =>
                   set('max_document_bytes', Number(event.target.value))} />
        </Field>
        <p className="field__hint">
          Сейчас {megabytes(draft['max_document_bytes'])}. Значение проверяет
          тот же серверный загрузчик, который принимает справку.
        </p>

        <fieldset className="setup__set">
          <legend className="form-grid__label">Разрешённые форматы</legend>
          {storable.map((mime) => (
            <label key={mime} className="setup__check">
              <input type="checkbox" checked={allowed.includes(mime)}
                     aria-label={documentTitle(mime)}
                     onChange={() => toggle(mime)} />
              <span>{documentTitle(mime)}</span>
              <span className="mono setup__mime">{mime}</span>
            </label>
          ))}
          {errors['allowed_document_types']?.map((text) => (
            <p key={text} className="form-grid__error">{text}</p>
          ))}
        </fieldset>
        <p className="field__hint">
          Показаны только форматы, содержимое которых хранилище умеет
          проверить по сигнатуре. Формат, которого здесь нет, был бы отвергнут
          при загрузке, сколько бы его ни разрешали.
        </p>
      </section>

      <section className="panel">
        <h3 className="setup__block"><AppIcon name="calendar" size={16} /> Сроки и остатки</h3>
        <Switch field="allow_negative_leave_balance" draft={draft} set={set}
                title="Отпуск в минус по остатку" help={section.help} />
        <Field label="Оформление задним числом, дней" name="backdating_days_allowed"
               errors={errors}>
          <input className="form-grid__input" type="number" min={0} max={366}
                 aria-label="Оформление задним числом"
                 value={String(draft['backdating_days_allowed'] ?? 0)}
                 onChange={(event) =>
                   set('backdating_days_allowed', Number(event.target.value))} />
        </Field>
        <p className="field__hint">{section.help['backdating_days_allowed']}</p>
        <Field label="Заявка на отпуск не позднее чем за, дней"
               name="vacation_min_days_ahead" errors={errors}>
          <input className="form-grid__input" type="number" min={0} max={366}
                 aria-label="Заявка на отпуск не позднее чем за"
                 value={String(draft['vacation_min_days_ahead'] ?? 0)}
                 onChange={(event) =>
                   set('vacation_min_days_ahead', Number(event.target.value))} />
        </Field>
        <p className="field__hint">{section.help['vacation_min_days_ahead']}</p>
      </section>

      <p className="note note--dim">
        Правила действуют на решения, принимаемые после сохранения. Уже
        подтверждённые отсутствия не пересматриваются, а загруженные справки
        не перепроверяются.
      </p>
    </>
  );
}

// --- учёт посещаемости -------------------------------------------------------

const OWNER_TITLE: Record<string, { where: string; to: string | null }> = {
  'work_schedules.late_grace_minutes': { where: 'График работы', to: '/employees' },
  'offices.geofence_radius_m': { where: 'Карточка офиса', to: '/offices' },
  deployment: { where: 'Настройки развёртывания', to: null },
};

function Attendance({ elsewhere }: { elsewhere: api.SettingElsewhere[] }) {
  return (
    <>
      <section className="panel">
        <h3 className="setup__block"><AppIcon name="clock" size={16} /> Где живёт какое правило</h3>
        <p className="muted">
          Общих переключателей посещаемости здесь нет намеренно. У каждого
          правила один владелец, и второй, «организационный», сделал бы
          неоднозначным вопрос «какое из двух значений сейчас работает».
        </p>
        <ul className="setup__owners">
          {elsewhere.map((item) => {
            const owner = OWNER_TITLE[item.owner];
            return (
              <li key={item.name} className="setup__owner">
                <span className="setup__owner-name mono">{item.name}</span>
                <span className="setup__owner-hint">{item.hint}</span>
                <span className="setup__owner-where">
                  {owner?.to ? (
                    <Link className="link" to={owner.to}>{owner.where} <AppIcon name="arrow" size={16} /></Link>
                  ) : (
                    owner?.where ?? item.owner
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="panel">
        <h3 className="setup__block"><AppIcon name="pin" size={16} /> Куда идти за настройкой</h3>
        <ul className="setup__links">
          <li>
            <Link className="setup__link" to="/offices">
              <span>
                <strong>Офисы и регионы</strong>
                <span className="setup__hint">
                  Координаты, радиус геозоны и QR-точки офиса
                </span>
              </span>
              <AppIcon name="chevron" size={16} />
            </Link>
          </li>
          <li>
            <Link className="setup__link" to="/attendance">
              <span>
                <strong>Посещаемость</strong>
                <span className="setup__hint">
                  Отметки, смены и заявки на исправление
                </span>
              </span>
              <AppIcon name="chevron" size={16} />
            </Link>
          </li>
        </ul>
        <p className="field__hint">
          Обязательность геолокации и направление отметки принадлежат
          конкретной QR-точке, начало смены и допуск опоздания — графику.
          Правила входа, выхода и защиты от повторных отметок отсюда
          не меняются.
        </p>
      </section>
    </>
  );
}

// --- уведомления и подключения -----------------------------------------------

function Notifications({ links, zone, onRetry }: {
  links: LinksBlock;
  zone: string;
  onRetry: () => void;
}) {
  return (
    <>
      <section className="panel">
        <h3 className="setup__block"><AppIcon name="bell" size={16} /> Канал доставки</h3>
        <p className="muted">
          Редактируемых параметров у канала нет: отправкой занимается
          отдельный обработчик очереди, и переключатель здесь ничего бы
          не изменил. Показано фактическое состояние.
        </p>
        <States links={links} zone={zone} onRetry={onRetry} only="telegram_bot" />
      </section>

      <section className="panel">
        <h3 className="setup__block"><AppIcon name="inbox" size={16} /> Журнал отправок</h3>
        <ul className="setup__links">
          <li>
            <Link className="setup__link" to="/notifications">
              <span>
                <strong>Уведомления</strong>
                <span className="setup__hint">
                  Очередь, попытки отправки и причины отказов
                </span>
              </span>
              <AppIcon name="chevron" size={16} />
            </Link>
          </li>
        </ul>
        <p className="field__hint">
          Просмотр и сохранение настроек ничего не отправляют и не
          возвращают в очередь старые сообщения.
        </p>
      </section>
    </>
  );
}

function Connections({ links, zone, onRetry }: {
  links: LinksBlock;
  zone: string;
  onRetry: () => void;
}) {
  return (
    <>
      <section className="panel">
        <h3 className="setup__block"><AppIcon name="globe" size={16} /> Подключения</h3>
        <States links={links} zone={zone} onRetry={onRetry} />
      </section>
      <p className="note note--dim">
        <AppIcon name="lock" size={16} /> Токенов, секретов и строк подключения
        здесь нет — ни целиком, ни префиксами: они не выходят за пределы
        сервера. Формы их правки не предусмотрено, эти значения задаются
        развёртыванием.
      </p>
    </>
  );
}

function States({ links, zone, onRetry, only }: {
  links: LinksBlock;
  zone: string;
  onRetry: () => void;
  only?: string;
}) {
  if (links.state === 'loading') {
    return <p className="empty" role="status">Проверяем подключения…</p>;
  }
  if (links.state === 'denied') {
    return <p className="empty empty--bad">Сведения о подключениях закрыты вашей ролью.</p>;
  }
  if (links.state === 'error') {
    return (
      <p className="empty empty--bad">
        Не удалось получить состояние подключений.{' '}
        <button type="button" className="link" onClick={onRetry}>Повторить</button>
      </p>
    );
  }
  const rows = links.data.items.filter((item) => !only || item.key === only);
  return (
    <ul className="setup__states">
      {rows.map((item) => (
        <li key={item.key} className="setup__state-row">
          <span className="setup__state-head">
            <strong>{item.title}</strong>
            <span className={`state state--${item.state}`}>
              <span className="state__dot" aria-hidden="true" />
              {stateTitle(item.state)}
            </span>
          </span>
          <span className="setup__hint">{item.note}</span>
          <span className="setup__state-meta">
            {item.configured ? 'Параметры заданы' : 'Параметры не заданы'}
            {item.confirmed_at
              ? ` · подтверждено ${moment(item.confirmed_at, zone, false)}`
              : ''}
            {typeof item.queued === 'number' && item.queued > 0
              ? ` · в очереди ${item.queued}`
              : ''}
          </span>
          {item.link && (
            <Link className="link" to={item.link}>Открыть раздел <AppIcon name="arrow" size={16} /></Link>
          )}
        </li>
      ))}
    </ul>
  );
}

// --- мои предпочтения --------------------------------------------------------

function Personal({ session }: { session: ReturnType<typeof useSession> }) {
  const user = session.status === 'authenticated' ? session.user : null;
  if (!user) return <p className="empty">Сведения о вас недоступны.</p>;
  return (
    <>
      <section className="panel">
        <h3 className="setup__block"><AppIcon name="users" size={16} /> Ваша учётная запись</h3>
        <dl className="facts">
          <div className="facts__row"><dt>Логин</dt><dd>{user.email}</dd></div>
          <div className="facts__row">
            <dt>Организация</dt><dd>{user.organization_code}</dd>
          </div>
          <div className="facts__row">
            <dt>Роли</dt>
            <dd>{user.roles.length ? user.roles.join(', ') : 'Без роли'}</dd>
          </div>
          <div className="facts__row">
            <dt>Время показывается в поясе</dt><dd>{zoneLabel(user.timezone)}</dd>
          </div>
        </dl>
      </section>

      <section className="panel">
        <h3 className="setup__block"><AppIcon name="alert" size={16} /> Личных переопределений пока нет</h3>
        <p className="muted">
          Личный язык и личные форматы отображения в CRM не хранятся: ни
          локализации, ни настраиваемых форматов в системе ещё нет, и поле
          без места применения обещало бы выбор, которого не существует.
          Заводить ради этого таблицу предпочтений преждевременно.
        </p>
        <p className="field__hint">
          Язык сотрудника для бота и Mini App хранится в его карточке
          (<span className="mono">employees.preferred_language</span>) и к
          учётной записи CRM не относится. Пароль и вход в систему меняются
          в разделе «Администрирование».
        </p>
      </section>
    </>
  );
}

// --- правая колонка ----------------------------------------------------------

function ScopeCard({ section }: { section: api.SettingSection }) {
  const code = section.effective?.['code'] as string | undefined;
  return (
    <section className="panel">
      <div className="setup__aside-head">
        <h3 className="setup__block"><AppIcon name="globe" size={16} /> Область действия</h3>
        <span className="pill">Вся организация</span>
      </div>
      <p className="muted">{section.description}</p>
      {code && (
        <p className="field__hint">
          Организация <span className="mono">{code}</span>. Настройки другой
          организации отсюда не видны и не меняются.
        </p>
      )}
      <p className="field__hint">
        Параметры отдельных офисов задаются в их карточках.
      </p>
      <Link className="link" to="/offices">Офисы и регионы <AppIcon name="arrow" size={16} /></Link>
    </section>
  );
}

function LastChangeCard({ change, zone, mayAudit }: {
  change: api.LastChange | null;
  zone: string;
  mayAudit: boolean;
}) {
  return (
    <section className="panel">
      <h3 className="setup__block"><AppIcon name="refresh" size={16} /> Последнее изменение</h3>
      {change ? (
        <>
          <p className="setup__when">{moment(change.at, zone, false)}</p>
          <p className="muted">{change.actor_email ?? 'Автор не записан'}</p>
        </>
      ) : (
        <p className="muted">
          {mayAudit
            ? 'Записей об изменении настроек пока нет.'
            : 'Кто и когда менял настройки, видно при праве на журнал действий.'}
        </p>
      )}
      {mayAudit && (
        <Link className="link" to="/admin?tab=audit">Открыть журнал действий <AppIcon name="arrow" size={16} /></Link>
      )}
    </section>
  );
}

function RelatedCard() {
  return (
    <section className="panel">
      <h3 className="setup__block"><AppIcon name="book" size={16} /> Связанные разделы</h3>
      <ul className="setup__links">
        <li>
          <Link className="setup__link" to="/offices">
            <span>
              <strong>Офисы и регионы</strong>
              <span className="setup__hint">Офисы и QR-точки</span>
            </span>
            <AppIcon name="chevron" size={16} />
          </Link>
        </li>
        <li>
          <Link className="setup__link" to="/admin">
            <span>
              <strong>Администрирование</strong>
              <span className="setup__hint">Пользователи, роли и доступ</span>
            </span>
            <AppIcon name="chevron" size={16} />
          </Link>
        </li>
      </ul>
    </section>
  );
}

// --- мелочи ------------------------------------------------------------------

function Field({ label, name, errors, children }: {
  label: string;
  name: string;
  errors: Record<string, string[]>;
  children: React.ReactNode;
}) {
  const found = errors[name];
  return (
    <label className="form-grid__field">
      <span className="form-grid__label">{label}</span>
      {children}
      {found?.map((text) => (
        <span key={text} className="form-grid__error">{text}</span>
      ))}
    </label>
  );
}

function Switch({ field, draft, set, title, help }: {
  field: string;
  draft: Draft;
  set: (field: string, value: unknown) => void;
  title: string;
  help: Record<string, string>;
}) {
  return (
    <label className="setup__check setup__check--row">
      <input type="checkbox" aria-label={title}
             checked={Boolean(draft[field])}
             onChange={(event) => set(field, event.target.checked)} />
      <span>
        <strong>{title}</strong>
        {help[field] && <span className="setup__hint">{help[field]}</span>}
      </span>
    </label>
  );
}
