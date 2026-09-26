/**
 * Боковые панели «Ознакомлений»: сотрудник, материал, раздел и
 * знакомство с компанией.
 *
 * Опубликованную редакцию материала здесь не правят нигде — только
 * «Новая версия». Сервер такую правку и так отклонит, но интерфейс не
 * должен её предлагать: под опубликованной стоят имена согласившихся.
 */

import { useCallback, useEffect, useState } from 'react';

import * as api from '../../api/crm';
import { AppIcon, type AppIconName } from '../../components/AppIcon';
import { AppSearchSelect, AppSelectField } from '../../components/AppSelect';
import { Confirm, Failed, Field, Loading, Refusal, SidePanel, useSaving } from '../../components/admin/Parts';
import { DatePicker } from '../../components/DatePicker';
import { today, useBlock } from '../dashboard/data';
import { moment } from '../time/zone';

// --- общие помощники ----------------------------------------------------------------

export function plural(n: number, one: string, few: string, many: string): string {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}

/** 2026-04-25 → 25.04.2026. */
export function dateRu(day: string): string {
  const [y, m, d] = day.split('-');
  return y && m && d ? `${d}.${m}.${y}` : '—';
}

export function daysUntil(day: string): number {
  return Math.round((Date.parse(`${day}T00:00:00Z`) - Date.parse(`${today()}T00:00:00Z`)) / 86400000);
}

/**
 * Сколько материалов назначено человеку: обязательные документы плюс
 * знакомство с компанией одним материалом — если карточки есть.
 */
export function materialsOf(row: api.OnboardingRow): number {
  return row.policies_total + (row.sections_total > 0 ? 1 : 0);
}

/** Прогресс по всему, что человек читает и подтверждает. Нечего проходить — нет и процента. */
export function progressOf(row: api.OnboardingRow): number | null {
  const total = row.sections_total + row.policies_total;
  if (!total) return null;
  return Math.round(((row.sections_done + row.policies_done) * 100) / total);
}

const TELEGRAM: Record<string, string> = {
  ACTIVE: 'Привязан',
  PENDING: 'Ждёт подтверждения',
  REVOKED: 'Отключён',
  BLOCKED: 'Заблокирован',
  NOT_LINKED: 'Не привязан',
};

const MATERIAL_STATE: Record<api.OnboardingMaterial['state'], { title: string; tone: string }> = {
  accepted: { title: 'Подтверждён', tone: 'green' },
  declined: { title: 'Отказ подтвердить', tone: 'red' },
  renewal: { title: 'Нужно подтвердить новую версию', tone: 'amber' },
  pending: { title: 'Ещё не подтверждён', tone: 'grey' },
};

const EVENT_ICON: Record<string, AppIconName> = {
  invited: 'send',
  linked: 'user',
  started: 'book',
  section: 'doc',
  info_completed: 'check',
  policy: 'lock',
  completed: 'check',
  reminded: 'bell',
};

// --- сотрудник --------------------------------------------------------------------------

export function PersonPanel({ id, zone, onClose, onChanged }: {
  id: string; zone: string; onClose: () => void; onChanged: () => void;
}) {
  const [attempt, setAttempt] = useState(0);
  const [card, reload] = useBlock((signal) => api.employeeOnboarding(id, signal), `onb-card|${id}|${attempt}`);
  const again = useCallback(() => { setAttempt((n) => n + 1); reload(); onChanged(); }, [reload, onChanged]);

  return (
    <SidePanel title="Ознакомление сотрудника" onClose={onClose}>
      {card.state === 'loading' && <Loading />}
      {(card.state === 'error' || card.state === 'denied') && <Failed onRetry={reload} />}
      {card.state === 'ready' && (
        <div className="on-panel">
          <h3 className="on-panel__name">{card.data.full_name}</h3>
          <p className="on-panel__sub">
            {[card.data.office_name, card.data.department_name, card.data.position_name].filter(Boolean).join(' · ') || 'Место работы не указано'}
          </p>

          <dl className="on-panel__facts">
            <div><dt>Знакомство с компанией</dt><dd>{card.data.sections_done} из {card.data.sections_total} карточек</dd></div>
            <div><dt>Материалы</dt><dd>{card.data.policies_done} из {card.data.policies_total}</dd></div>
            <div><dt>Telegram</dt><dd>{TELEGRAM[card.data.telegram_state] ?? '—'}</dd></div>
            <div><dt>Последнее напоминание</dt><dd>{card.data.last_reminder_at ? moment(card.data.last_reminder_at, zone, true) : 'не было'}</dd></div>
          </dl>

          <Due row={card.data} onChanged={again} />

          {(card.data.materials ?? []).length > 0 && (
            <>
              <h4 className="on-panel__head">Материалы</h4>
              <ul className="on-panel__list">
                {(card.data.materials ?? []).map((one) => {
                  const state = MATERIAL_STATE[one.state];
                  return (
                    <li key={one.document_id}>
                      <span><b>{one.title}</b>{one.version && <small> · v{one.version}</small>}</span>
                      <span className={`on-state on-state--${state.tone}`}><i aria-hidden="true" />{state.title}</span>
                    </li>
                  );
                })}
              </ul>
            </>
          )}

          <Invitation row={card.data} zone={zone} onChanged={again} />

          <h4 className="on-panel__head">История</h4>
          {card.data.timeline.length === 0 ? (
            <p className="on-muted">Пока ничего: приглашение ещё не выдавали.</p>
          ) : (
            <ol className="on-panel__line">
              {card.data.timeline.map((event, index) => (
                <li key={`${event.kind}-${index}`}>
                  <span className="on-panel__mark" aria-hidden="true"><AppIcon name={EVENT_ICON[event.kind] ?? 'doc'} size={16} /></span>
                  <span><b>{event.title}</b><small>{moment(event.at, zone, true)}{event.detail ? ` · ${event.detail}` : ''}</small></span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </SidePanel>
  );
}

function Due({ row, onChanged }: { row: api.OnboardingCard; onChanged: () => void }) {
  const [value, setValue] = useState(row.due_date ?? '');
  useEffect(() => setValue(row.due_date ?? ''), [row.due_date]);
  const saving = useSaving();
  const changed = (value || null) !== (row.due_date ?? null);
  return (
    <div className="on-panel__due">
      <h4 className="on-panel__head">Срок ознакомления</h4>
      <div className="on-panel__due-row">
        <DatePicker label="Срок ознакомления" value={value} now={today()} min={today()} allowEmpty onChange={setValue} />
        <button type="button" className="btn btn--primary" disabled={!changed || saving.busy}
                onClick={() => saving.run(() => api.setOnboardingDue(row.employee_id, value || null), onChanged)}>
          {saving.busy ? 'Сохраняем…' : 'Сохранить срок'}
        </button>
      </div>
      <p className="on-muted">{row.due_date ? (row.overdue ? 'Срок прошёл, ознакомление не завершено.' : '') : 'Срок не назначен — просрочки нет.'}</p>
      <Refusal text={saving.refusal} />
    </div>
  );
}

/**
 * Ссылка и то, что с ней можно сделать. Ссылка показывается ровно один
 * раз — в ответе на её выдачу: в базе лежит только хеш.
 */
function Invitation({ row, zone, onChanged }: { row: api.OnboardingCard; zone: string; onChanged: () => void }) {
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const saving = useSaving();
  const live = row.invitation_status === 'ACTIVE';
  const linked = row.telegram_state === 'ACTIVE';

  const issue = (again: boolean) => saving.run(async () => {
    const made = await api.inviteToOnboarding(row.employee_id, again);
    setLink(made.link);
    setCopied(false);
    setNote(made.linked ? 'Ссылка не нужна: Telegram уже привязан. Ознакомление назначено.' : null);
  }, onChanged);

  return (
    <div className="on-panel__invite">
      <h4 className="on-panel__head">Telegram</h4>
      <p className="on-muted">
        {live ? `Ссылка выдана, действует до ${moment(row.invitation_expires_at ?? '', zone, false)}`
          : linked ? 'Telegram привязан: ссылка больше не нужна' : 'Ссылка не выдавалась'}
      </p>
      <div className="on-panel__tools">
        {!live && !linked && (
          <button type="button" className="btn btn--primary" disabled={saving.busy} onClick={() => issue(false)}>
            <AppIcon name="send" size={16} /> Создать приглашение
          </button>
        )}
        {live && (
          <>
            <button type="button" className="btn" disabled={saving.busy} onClick={() => issue(true)}>Создать новую ссылку</button>
            <button type="button" className="btn" disabled={saving.busy}
                    onClick={() => saving.run(() => api.revokeOnboardingInvite(row.employee_id), () => { setLink(null); onChanged(); })}>
              Отозвать
            </button>
          </>
        )}
        {/* Бот не может написать первым — это правило Telegram. */}
        <button type="button" className="btn" disabled={saving.busy || !linked || row.completed}
                title={linked ? '' : 'Бот не может написать первым: сначала ссылка'}
                onClick={() => saving.run(() => api.remindOnboarding(row.employee_id), () => { setNote('Напоминание отправлено'); onChanged(); })}>
          <AppIcon name="bell" size={16} /> Напомнить
        </button>
      </div>
      {link && (
        <div className="on-panel__link">
          <p className="on-muted">Ссылка персональная и одноразовая. Она показывается один раз — скопируйте её сейчас.</p>
          <div className="on-panel__tools">
            <input readOnly value={link} aria-label="Ссылка на ознакомление" onFocus={(event) => event.currentTarget.select()} />
            <button type="button" className="btn" onClick={async () => {
              try { await navigator.clipboard.writeText(link); setCopied(true); } catch { setCopied(false); }
            }}>{copied ? 'Скопировано' : 'Скопировать'}</button>
          </div>
        </div>
      )}
      {note && <p className="on-note" role="status">{note}</p>}
      <Refusal text={saving.refusal} />
    </div>
  );
}

// --- материал ----------------------------------------------------------------------------

const VERSION_STATE: Record<string, string> = { DRAFT: 'черновик', PUBLISHED: 'действует', ARCHIVED: 'в архиве' };

/** Следующий номер по умолчанию: 1.0 → 2.0. Правится руками. */
function nextVersion(document: api.PolicyDocument): string {
  const live = document.current_version?.version ?? '0.0';
  const major = Number.parseInt(live.split('.')[0] ?? '0', 10);
  return `${Number.isNaN(major) ? 1 : major + 1}.0`;
}

type VersionDraft = { version: string; summary: string; body: string; agree_label: string };

export function DocumentPanel({ id, rows, categories, zone, onClose, onChanged }: {
  id: string; rows: api.PolicyDocument[]; categories: api.PolicyCategory[]; zone: string;
  onClose: () => void; onChanged: () => void;
}) {
  const document = rows.find((one) => one.id === id);
  const [draft, setDraft] = useState<VersionDraft | null>(null);
  const [publishing, setPublishing] = useState<api.PolicyVersion | null>(null);
  const [showPending, setShowPending] = useState(false);
  const [about, setAbout] = useState({
    title: document?.title ?? '', description: document?.description ?? '', category: document?.category?.id ?? '',
    position: String(document?.position ?? 1),
  });
  useEffect(() => {
    if (document) {
      setAbout({
        title: document.title, description: document.description ?? '', category: document.category?.id ?? '',
        position: String(document.position),
      });
    }
  }, [document?.id, document?.title, document?.description, document?.category?.id, document?.position]); // eslint-disable-line react-hooks/exhaustive-deps
  const saving = useSaving();
  const publish = useSaving();
  const facts = useSaving();

  const [pending, reloadPending] = useBlock((signal) => api.policyPending(id, signal), `policy-pending|${id}|${showPending}`, showPending);

  if (!document) return null;
  const drafts = document.versions.filter((one) => one.status === 'DRAFT');
  const aboutChanged = about.title.trim() !== document.title
    || (about.description.trim() || null) !== (document.description ?? null)
    || (about.category || null) !== (document.category?.id ?? null)
    || Number(about.position) !== document.position;
  const position = Number(about.position);
  const positionOk = Number.isInteger(position) && position >= 1;

  return (
    <>
      <SidePanel title={document.title} onClose={onClose}>
        <div className="on-panel">
          <h4 className="on-panel__head">О материале</h4>
          <Field label="Название">
            <input value={about.title} maxLength={255} onChange={(event) => setAbout((was) => ({ ...was, title: event.target.value }))} />
          </Field>
          <Field label="Описание" hint="Коротко, о чём материал: так его видит кадровик в списке.">
            <textarea rows={3} value={about.description} onChange={(event) => setAbout((was) => ({ ...was, description: event.target.value }))} />
          </Field>
          <AppSelectField label="Раздел" value={about.category} onChange={(value) => setAbout((was) => ({ ...was, category: value }))}>
            <option value="">Без раздела</option>
            {categories.map((one) => <option key={one.id} value={one.id}>{one.title}</option>)}
          </AppSelectField>
          <Field label="Порядок в разделе" hint="Материалы раздела идут по возрастанию: 1 — первым. В этом же порядке их показывает бот.">
            <input type="number" min={1} step={1} value={about.position}
                   onChange={(event) => setAbout((was) => ({ ...was, position: event.target.value }))} />
          </Field>
          <div className="on-panel__tools">
            <button type="button" className="btn" disabled={!aboutChanged || !about.title.trim() || !positionOk || facts.busy}
                    onClick={() => facts.run(() => api.updatePolicyDocument(document.id, {
                      title: about.title.trim(), description: about.description.trim(), category_id: about.category || null,
                      position,
                    }), onChanged)}>
              {facts.busy ? 'Сохраняем…' : 'Сохранить'}
            </button>
          </div>
          <Refusal text={facts.refusal} />

          <h4 className="on-panel__head">Действующая версия</h4>
          {document.current_version ? (
            <div className="on-panel__version">
              <p><b>v{document.current_version.version}</b>{document.current_version.published_at && ` · опубликована ${moment(document.current_version.published_at, zone, false)}`}</p>
              <p className="on-muted">{document.current_version.summary}</p>
              {document.current_version.has_file && (
                <a className="on-link" href={api.policyFileUrl(document.current_version.id)} target="_blank" rel="noreferrer">
                  <AppIcon name="doc" size={16} /> {document.current_version.file_name}
                </a>
              )}
              <p className="on-muted">Назначено: {document.assigned ?? 0} · подтвердили: {document.confirmed ?? 0}
                {(document.renewal_pending ?? 0) > 0 ? ` · ждут подтверждения новой версии: ${document.renewal_pending}` : ''}
                {(document.declined ?? 0) > 0 ? ` · отказались: ${document.declined}` : ''}</p>
            </div>
          ) : (
            <p className="on-muted">Опубликованной версии нет. Пока её не выпустят, материал никого ни к чему не обязывает.</p>
          )}

          <div className="on-panel__tools">
            {document.current_version && (
              <button type="button" className="btn" onClick={() => setShowPending((was) => !was)}>
                {showPending ? 'Скрыть' : 'Кто не подтвердил'}
              </button>
            )}
            <button type="button" className="btn btn--primary"
                    onClick={() => setDraft({
                      version: nextVersion(document),
                      summary: document.current_version?.summary ?? '',
                      body: document.current_version?.body ?? '',
                      agree_label: document.current_version?.agree_label ?? 'Ознакомлен(а) и согласен(на)',
                    })}>
              <AppIcon name="plus" size={16} /> {document.current_version ? 'Новая версия' : 'Создать версию'}
            </button>
          </div>

          {showPending && (
            <div className="on-panel__pending">
              {pending.state === 'loading' && <Loading />}
              {pending.state === 'error' && <Failed onRetry={reloadPending} />}
              {pending.state === 'ready' && pending.data.total === 0 && <p className="on-muted">Действующую версию подтвердили все.</p>}
              {pending.state === 'ready' && pending.data.total > 0 && (
                <ul className="on-panel__list">
                  {pending.data.items.map((one) => (
                    <li key={one.employee_id}>
                      <span>{one.full_name}</span>
                      {one.declined_at && <span className="on-state on-state--red"><i aria-hidden="true" />отказ {moment(one.declined_at, zone, false)}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {drafts.length > 0 && (
            <>
              <h4 className="on-panel__head">Черновики</h4>
              <ul className="on-panel__list">
                {drafts.map((one) => (
                  <li key={one.id}>
                    <span><b>v{one.version}</b><small>{one.has_file ? ` · ${one.file_name}` : ' · без файла'}</small></span>
                    <span className="on-panel__tools">
                      <FileUpload versionId={one.id} onDone={onChanged} />
                      <button type="button" className="btn btn--primary" onClick={() => setPublishing(one)}>Опубликовать</button>
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}

          {document.versions.length > 0 && (
            <>
              <h4 className="on-panel__head">История версий</h4>
              <ul className="on-panel__history">
                {document.versions.map((one) => (
                  <li key={one.id}>v{one.version} — {VERSION_STATE[one.status] ?? ''}{one.published_at ? ` · ${moment(one.published_at, zone, false)}` : ''}</li>
                ))}
              </ul>
              <p className="on-muted">Создал(а): {document.created_by ?? '—'} · последнее изменение: {document.changed_by ?? '—'}{document.changed_at ? `, ${moment(document.changed_at, zone, true)}` : ''}</p>
            </>
          )}
          <Refusal text={saving.refusal} />
        </div>
      </SidePanel>

      {draft && (
        <VersionForm draft={draft} onChange={setDraft} busy={saving.busy} refusal={saving.refusal}
                     onCancel={() => setDraft(null)}
                     onSave={() => saving.run(() => api.createPolicyVersion(id, {
                       version: draft.version.trim(), summary: draft.summary, body: draft.body, agree_label: draft.agree_label.trim(),
                     }), () => { setDraft(null); onChanged(); })} />
      )}

      {publishing && (
        <Confirm
          title="Опубликовать версию"
          what={`Версия ${publishing.version} материала «${document.title}» станет действующей.`}
          consequence={'После публикации её нельзя менять. Все, кто её не подтвердил, получат материал повторно в Telegram, '
            + 'а прежние подтверждения останутся в истории. Перечитывать знакомство с компанией не придётся.'}
          confirmLabel="Опубликовать"
          busy={publish.busy}
          refusal={publish.refusal}
          onCancel={() => setPublishing(null)}
          onConfirm={() => publish.run(() => api.publishPolicyVersion(publishing.id), () => { setPublishing(null); onChanged(); })}
        />
      )}
    </>
  );
}

function VersionForm({ draft, onChange, busy, refusal, onCancel, onSave }: {
  draft: VersionDraft; onChange: (next: VersionDraft) => void; busy: boolean; refusal: string | null;
  onCancel: () => void; onSave: () => void;
}) {
  const set = (key: keyof VersionDraft, value: string) => onChange({ ...draft, [key]: value });
  return (
    <div className="adm-ask" role="dialog" aria-modal="true" aria-label="Новая версия">
      <div className="adm-ask__box adm-ask__box--wide">
        <h2 className="adm-ask__title">Новая версия</h2>
        <p className="adm-ask__what">Создаётся черновиком. Пока её не опубликуют, она никого не обязывает — текст можно править, файл заменять.</p>
        <Field label="Номер версии"><input value={draft.version} maxLength={20} onChange={(event) => set('version', event.target.value)} /></Field>
        <Field label="Краткий текст в боте" hint="То, что человек увидит перед кнопками согласия.">
          <textarea rows={5} value={draft.summary} onChange={(event) => set('summary', event.target.value)} />
        </Field>
        <Field label="Полный текст" hint="Открывается кнопкой «Открыть полный документ». Можно оставить пустым, если приложите PDF.">
          <textarea rows={10} value={draft.body} onChange={(event) => set('body', event.target.value)} />
        </Field>
        <Field label="Подпись кнопки согласия">
          <input value={draft.agree_label} maxLength={100} onChange={(event) => set('agree_label', event.target.value)} />
        </Field>
        <Refusal text={refusal} />
        <div className="adm-ask__tools">
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>Отмена</button>
          <button type="button" className="btn btn--primary" onClick={onSave}
                  disabled={busy || draft.version.trim() === '' || draft.summary.trim() === ''}>
            {busy ? 'Сохраняем…' : 'Сохранить черновик'}
          </button>
        </div>
      </div>
    </div>
  );
}

function FileUpload({ versionId, onDone }: { versionId: string; onDone: () => void }) {
  const saving = useSaving();
  return (
    <label className="btn on-file">
      {saving.busy ? 'Загружаем…' : 'Приложить PDF'}
      <input type="file" accept="application/pdf" hidden onChange={(event) => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (file) void saving.run(() => api.uploadPolicyFile(versionId, file), onDone);
      }} />
      {saving.refusal && <span className="on-red"> {saving.refusal}</span>}
    </label>
  );
}

/** Код материала — внутренний: человеку его не показывают, поэтому он придумывается сам. */
function materialCode(): string {
  return `M-${Date.now().toString(36).toUpperCase()}`;
}

export function NewMaterialPanel({ categories, onClose, onCreated }: {
  categories: api.PolicyCategory[]; onClose: () => void; onCreated: (id: string) => void;
}) {
  const [form, setForm] = useState({ title: '', description: '', category: '', mandatory: true });
  const saving = useSaving();
  return (
    <SidePanel title="Новый материал" onClose={onClose}>
      <div className="on-panel">
        <p className="on-muted">Сначала материал создаётся без текста. Затем добавьте первую версию и опубликуйте её — после этого сотрудники получат его в Telegram.</p>
        <Field label="Название">
          <input value={form.title} maxLength={255} onChange={(event) => setForm((was) => ({ ...was, title: event.target.value }))} />
        </Field>
        <Field label="Описание">
          <textarea rows={3} value={form.description} onChange={(event) => setForm((was) => ({ ...was, description: event.target.value }))} />
        </Field>
        <AppSelectField label="Раздел" value={form.category} onChange={(value) => setForm((was) => ({ ...was, category: value }))}>
          <option value="">Без раздела</option>
          {categories.map((one) => <option key={one.id} value={one.id}>{one.title}</option>)}
        </AppSelectField>
        <label className="on-check">
          <input type="checkbox" checked={form.mandatory} onChange={(event) => setForm((was) => ({ ...was, mandatory: event.target.checked }))} />
          Обязательный: сотрудник должен подтвердить ознакомление
        </label>
        <Refusal text={saving.refusal} />
        <div className="adm-side__tools">
          <button type="button" className="btn" onClick={onClose}>Отмена</button>
          <button type="button" className="btn btn--primary" disabled={!form.title.trim() || saving.busy}
                  onClick={() => saving.run(async () => {
                    const made = await api.createPolicyDocument({
                      code: materialCode(), title: form.title.trim(), description: form.description.trim(),
                      is_mandatory: form.mandatory, category_id: form.category || null,
                    });
                    onCreated(made.id);
                  })}>
            {saving.busy ? 'Создаём…' : 'Создать материал'}
          </button>
        </div>
      </div>
    </SidePanel>
  );
}

// --- раздел --------------------------------------------------------------------------------

export function CategoryPanel({ category, onClose, onSaved }: {
  category: api.PolicyCategory | null; onClose: () => void; onSaved: () => void;
}) {
  const [form, setForm] = useState({
    title: category?.title ?? '', description: category?.description ?? '', owner: category?.owner?.id ?? '',
  });
  const [archiving, setArchiving] = useState(false);
  const saving = useSaving();
  const archive = useSaving();
  const [people] = useBlock((signal) => api.employees({ status: 'ACTIVE', limit: '200' }, signal), 'onb-owners');
  const options = [
    ...(category?.owner && !(people.state === 'ready' && people.data.items.some((one) => one.id === category.owner?.id))
      ? [{ value: category.owner.id, label: category.owner.full_name }] : []),
    ...(people.state === 'ready' ? people.data.items.map((one) => ({ value: one.id, label: one.full_name })) : []),
  ];

  return (
    <>
      <SidePanel title={category ? 'Раздел материалов' : 'Новый раздел'} onClose={onClose}>
        <div className="on-panel">
          <Field label="Название">
            <input value={form.title} maxLength={255} onChange={(event) => setForm((was) => ({ ...was, title: event.target.value }))} />
          </Field>
          <Field label="Описание" hint="Что собрано в разделе — одним предложением.">
            <textarea rows={3} value={form.description} onChange={(event) => setForm((was) => ({ ...was, description: event.target.value }))} />
          </Field>
          <AppSearchSelect label="Ответственный" value={form.owner} empty="Не назначен" options={options}
                           onChange={(value) => setForm((was) => ({ ...was, owner: value }))} />
          {category && category.documents.length > 0 && (
            <>
              <h4 className="on-panel__head">Материалы раздела</h4>
              <ul className="on-panel__history">{category.documents.map((one) => <li key={one.id}>{one.title}</li>)}</ul>
            </>
          )}
          <Refusal text={saving.refusal} />
          <div className="adm-side__tools">
            {category && <button type="button" className="btn btn--danger" onClick={() => setArchiving(true)}>Убрать раздел</button>}
            <button type="button" className="btn" onClick={onClose}>Отмена</button>
            <button type="button" className="btn btn--primary" disabled={!form.title.trim() || saving.busy}
                    onClick={() => saving.run(() => {
                      const body = { title: form.title.trim(), description: form.description.trim() || null, owner_employee_id: form.owner || null };
                      return category ? api.updatePolicyCategory(category.id, body) : api.createPolicyCategory(body);
                    }, onSaved)}>
              {saving.busy ? 'Сохраняем…' : 'Сохранить'}
            </button>
          </div>
        </div>
      </SidePanel>
      {archiving && category && (
        <Confirm
          title="Убрать раздел"
          what={`Раздел «${category.title}» пропадёт из списка.`}
          consequence="Убрать можно только пустой раздел: сначала перенесите его материалы в другой. История подтверждений не меняется."
          confirmLabel="Убрать"
          busy={archive.busy}
          refusal={archive.refusal}
          onCancel={() => setArchiving(false)}
          onConfirm={() => archive.run(() => api.archivePolicyCategory(category.id), onSaved)}
        />
      )}
    </>
  );
}

// --- знакомство с компанией ------------------------------------------------------------------

type CardDraft = { id: string | null; title: string; body: string; button_label: string };

export function IntroPanel({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [attempt, setAttempt] = useState(0);
  const [cards, reload] = useBlock((signal) => api.onboardingSections(signal), `onb-intro|${attempt}`);
  const [draft, setDraft] = useState<CardDraft | null>(null);
  const done = () => { setDraft(null); setAttempt((n) => n + 1); onChanged(); };

  if (draft) return <CardPanel draft={draft} onClose={() => setDraft(null)} onSaved={done} />;
  return (
    <SidePanel title="Знакомство с компанией" onClose={onClose}>
      <div className="on-panel">
        <p className="on-muted">Эти карточки сотрудник читает в Telegram первыми, по одной. Правка текста не заставляет перечитывать: карточка рассказывает, а не обязывает.</p>
        {cards.state === 'loading' && <Loading />}
        {(cards.state === 'error' || cards.state === 'denied') && <Failed onRetry={reload} />}
        {cards.state === 'ready' && (
          <ol className="on-cards">
            {cards.data.items.map((one) => (
              <li key={one.id}>
                <button type="button" onClick={() => setDraft({ id: one.id, title: one.title, body: one.body, button_label: one.button_label })}>
                  <span className="on-cards__no">{one.position}</span>
                  <span><b>{one.title}</b><small>{one.body.slice(0, 110)}{one.body.length > 110 ? '…' : ''}</small></span>
                </button>
              </li>
            ))}
          </ol>
        )}
        <div className="adm-side__tools">
          <button type="button" className="btn btn--primary" onClick={() => setDraft({ id: null, title: '', body: '', button_label: 'Я ознакомился(ась)' })}>
            <AppIcon name="plus" size={16} /> Добавить карточку
          </button>
        </div>
      </div>
    </SidePanel>
  );
}

function CardPanel({ draft, onClose, onSaved }: { draft: CardDraft; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState(draft);
  const [archiving, setArchiving] = useState(false);
  const saving = useSaving();
  const archive = useSaving();
  const set = (key: keyof CardDraft, value: string) => setForm((was) => ({ ...was, [key]: value }));
  const valid = form.title.trim() !== '' && form.body.trim() !== '';
  return (
    <>
      <SidePanel title={draft.id ? 'Карточка о компании' : 'Новая карточка'} onClose={onClose}>
        <div className="on-panel">
          <Field label="Название"><input value={form.title} maxLength={255} onChange={(event) => set('title', event.target.value)} /></Field>
          <Field label="Текст" hint="То, что человек увидит в чате. Обычный текст и списки.">
            <textarea rows={14} value={form.body} onChange={(event) => set('body', event.target.value)} />
          </Field>
          <Field label="Подпись кнопки"><input value={form.button_label} maxLength={100} onChange={(event) => set('button_label', event.target.value)} /></Field>
          <Refusal text={saving.refusal} />
          <div className="adm-side__tools">
            {draft.id && <button type="button" className="btn btn--danger" onClick={() => setArchiving(true)}>Убрать карточку</button>}
            <button type="button" className="btn" onClick={onClose}>Назад</button>
            <button type="button" className="btn btn--primary" disabled={!valid || saving.busy}
                    onClick={() => saving.run(() => {
                      const body = { title: form.title.trim(), body: form.body, button_label: form.button_label.trim() };
                      return draft.id ? api.updateOnboardingSection(draft.id, body) : api.createOnboardingSection(body);
                    }, onSaved)}>
              {saving.busy ? 'Сохраняем…' : 'Сохранить'}
            </button>
          </div>
        </div>
      </SidePanel>
      {archiving && draft.id && (
        <Confirm
          title="Убрать карточку"
          what={`Карточка «${draft.title}» перестанет показываться сотрудникам.`}
          consequence="Отметки тех, кто её уже прочитал, сохранятся."
          confirmLabel="Убрать"
          busy={archive.busy}
          refusal={archive.refusal}
          onCancel={() => setArchiving(false)}
          onConfirm={() => archive.run(() => api.archiveOnboardingSection(draft.id as string), onSaved)}
        />
      )}
    </>
  );
}
