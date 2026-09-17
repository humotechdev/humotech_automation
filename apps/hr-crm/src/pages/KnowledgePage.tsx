/**
 * База знаний: документы и пары «вопрос — ответ».
 *
 * Три состояния материала здесь не смешиваются, и это главное правило
 * страницы:
 *
 *   СОХРАНЁН — черновик. Работает всегда, никакого AI не требует;
 *   ОПУБЛИКОВАН — действующая версия. Требует индексации;
 *   ОТВЕЧАЕТ СОТРУДНИКАМ — опубликован И проиндексирован.
 *
 * При выключенном ассистенте второе и третье недостижимы. Страница
 * говорит об этом словами и гасит соответствующие действия, но ведение
 * материалов работает полностью: черновик не называется опубликованным,
 * а недоступное действие не прячется, чтобы человек не искал его.
 *
 * Строка списка — это ДОКУМЕНТ, а не версия: сервер отдаёт самую новую
 * версию линейки, история версий приходит отдельным запросом. Иначе
 * «10 документов» означало бы «10 строк таблицы».
 */

import { useCallback, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { AppShell } from '../components/AppShell';
import { AppIcon } from '../components/AppIcon';
import { AppSelectField } from '../components/AppSelect';
import { MaterialForm } from '../components/MaterialForm';
import { Markup } from '../features/knowledge/markup';
import { usePaging } from '../features/knowledge/paging';
import {
  FAQ_STATUS, FAQ_TABS, SOURCE_STATUS, SOURCE_TABS, SOURCE_TYPE,
  canArchive, canEditInPlace, faqActivateBlockedBecause, faqReadinessNote, faqTabCount,
  indexBlockedBecause, readinessNote, scopeTitle, sourceTabCount,
  type FaqTab, type SourceTab,
} from '../features/knowledge/model';
import { longDate, useBlock, type Block } from '../features/dashboard/data';
import { useSession } from '../features/auth/session';

export function KnowledgePage() {
  const session = useSession();
  const can = (code: string) =>
    session.status === 'authenticated' && session.user.permissions.includes(code);
  const mayWrite = can('knowledge.write');
  const mayPublish = can('knowledge.publish');
  const mayIndex = can('knowledge.index');

  const [params, setParams] = useSearchParams();
  const area = params.get('area') === 'faq' ? 'faq' : 'docs';
  const search = params.get('q') ?? '';
  const region = params.get('region_id') ?? '';
  const office = params.get('office_id') ?? '';
  const picked = params.get('id') ?? '';
  const docTab = (SOURCE_TABS.find((t) => t.key === params.get('tab'))?.key ??
    'all') as SourceTab;
  const faqTab = (FAQ_TABS.find((t) => t.key === params.get('tab'))?.key ??
    'all') as FaqTab;

  const [attempt, setAttempt] = useState(0);
  const [form, setForm] = useState<
    | { mode: 'document' | 'faq'; editing: api.Source | api.FaqRow | null;
        newVersionOf: api.Source | null }
    | null
  >(null);
  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [innerTab, setInnerTab] = useState<'content' | 'versions'>('content');
  const [shown, setShown] = useState<string>('');

  const patch = useCallback(
    (changes: Record<string, string | null>) => {
      setParams((was) => {
        const next = new URLSearchParams(was);
        for (const [key, value] of Object.entries(changes)) {
          if (value) next.set(key, value);
          else next.delete(key);
        }
        return next;
      });
    },
    [setParams],
  );

  // --- справочники и признак доступности -----------------------------------

  const [directory] = useBlock(
    (signal) =>
      Promise.all([api.regions(signal), api.offices(signal)]).then(([r, o]) => ({
        regions: r.items.filter((item) => item.status === 'ACTIVE'),
        offices: o.items.filter((item) => item.status === 'ACTIVE'),
      })),
    'knowledge-directory',
  );
  const scope = directory.state === 'ready'
    ? directory.data
    : { regions: [] as api.Region[], offices: [] as api.Office[] };

  const [capabilityBlock] = useBlock(
    (signal) => api.knowledgeCapability(signal),
    'knowledge-capability',
  );
  const capability = capabilityBlock.state === 'ready' ? capabilityBlock.data : null;

  // --- списки ---------------------------------------------------------------

  const filters = useMemo(
    () => ({
      ...(search ? { search } : {}),
      ...(region && !office ? { region_id: region } : {}),
      ...(office ? { office_id: office } : {}),
    }),
    [search, region, office],
  );
  const key = `${area}|${docTab}|${faqTab}|${search}|${region}|${office}|${attempt}`;

  const [docs] = useBlock(
    (signal) => {
      const statuses = SOURCE_TABS.find((t) => t.key === docTab)?.statuses ?? '';
      return Promise.all([
        api.sources({ ...filters, ...(statuses ? { status: statuses } : {}), limit: '30' }, signal),
        api.sourceCounts(filters, signal),
      ]).then(([page, counts]) => ({ page, counts }));
    },
    `docs|${key}`,
    area === 'docs',
  );

  const [faqs] = useBlock(
    (signal) => {
      const statuses = FAQ_TABS.find((t) => t.key === faqTab)?.statuses ?? '';
      return Promise.all([
        api.faqList({ ...filters, ...(statuses ? { status: statuses } : {}), limit: '30' }, signal),
        api.faqCounts(filters, signal),
      ]).then(([page, counts]) => ({ page, counts }));
    },
    `faq|${key}`,
    area === 'faq',
  );

  // Дочитывание идёт по курсору сервера. Номеров страниц здесь нет:
  // выдача курсорная, и «страница 3» была бы выдумкой.
  const docStatuses = SOURCE_TABS.find((t) => t.key === docTab)?.statuses ?? '';
  const faqStatuses = FAQ_TABS.find((t) => t.key === faqTab)?.statuses ?? '';
  const docPages = usePaging(
    `docs|${key}`,
    docs.state === 'ready' ? docs.data.page : null,
    useCallback(
      (cursor: string) =>
        api.sources({
          ...filters, ...(docStatuses ? { status: docStatuses } : {}),
          limit: '30', cursor,
        }),
      [filters, docStatuses],
    ),
  );
  const faqPages = usePaging(
    `faq|${key}`,
    faqs.state === 'ready' ? faqs.data.page : null,
    useCallback(
      (cursor: string) =>
        api.faqList({
          ...filters, ...(faqStatuses ? { status: faqStatuses } : {}),
          limit: '30', cursor,
        }),
      [filters, faqStatuses],
    ),
  );

  // Ключ включает выбранный идентификатор: при быстром переключении
  // строк устаревший ответ отбрасывается, и текст одного документа
  // не окажется под заголовком другого.
  const [openDoc] = useBlock(
    (signal) => api.source(picked, signal),
    `doc|${picked}|${attempt}`,
    area === 'docs' && Boolean(picked),
  );
  const [openFaq] = useBlock(
    (signal) => api.faqItem(picked, signal),
    `faqitem|${picked}|${attempt}`,
    area === 'faq' && Boolean(picked),
  );
  const [versions] = useBlock(
    (signal) => api.sourceVersions(picked, signal),
    `versions|${picked}|${attempt}`,
    area === 'docs' && Boolean(picked),
  );

  // Текст старой версии приходит отдельным запросом: в списке версий
  // содержимого нет — и не должно быть, иначе история тянула бы за
  // собой все редакции целиком.
  const [oldVersion] = useBlock(
    (signal) => api.source(shown, signal),
    `old|${shown}`,
    Boolean(shown),
  );

  // --- действия -------------------------------------------------------------

  async function act(run: () => Promise<unknown>) {
    if (acting) return;
    setActing(true);
    setActionError(null);
    try {
      await run();
      setAttempt((n) => n + 1);
    } catch (error) {
      setActionError(
        error instanceof ApiFailure && error.kind === 'conflict'
          ? 'Состояние материала изменилось — обновите карточку'
          : messageFor(error),
      );
    } finally {
      setActing(false);
    }
  }

  const docCounts = docs.state === 'ready' ? docs.data.counts : null;
  const faqCounts = faqs.state === 'ready' ? faqs.data.counts : null;

  // Итог для подписи — счётчик ОТКРЫТОЙ вкладки, а не общее число:
  // «показано 30 из 48» при выбранном состоянии обязано считать
  // документы этого состояния.
  const pages = area === 'docs' ? docPages : faqPages;
  const total = area === 'docs'
    ? (docCounts ? sourceTabCount(docTab, docCounts) : null)
    : (faqCounts ? faqTabCount(faqTab, faqCounts) : null);

  return (
    <AppShell breadcrumb="База знаний" section="knowledge">
      <header className="head head--tight">
        <div>
          <h1 className="head__title">База знаний</h1>
          <p className="head__sub">
            Правила, инструкции и ответы на вопросы сотрудников
          </p>
        </div>
        <div className="head__actions head__actions--column">
          <button type="button" className="btn btn--dark" disabled={!mayWrite}
                  title={mayWrite ? undefined : 'Нужно право knowledge.write'}
                  onClick={() =>
                    setForm({
                      mode: area === 'faq' ? 'faq' : 'document',
                      editing: null,
                      newVersionOf: null,
                    })}>
            <AppIcon name="plus" size={16} />
            Добавить материал
          </button>
          <AiBadge capability={capability} />
        </div>
      </header>

      <div className="tabs tabs--top" role="tablist" aria-label="Разделы базы знаний">
        <button type="button" role="tab" aria-selected={area === 'docs'}
                className={area === 'docs' ? 'tab tab--on' : 'tab'}
                onClick={() => patch({ area: null, tab: null, id: null })}>
          Документы
          {docCounts && <span className="tab__count">{docCounts.total}</span>}
        </button>
        <button type="button" role="tab" aria-selected={area === 'faq'}
                className={area === 'faq' ? 'tab tab--on' : 'tab'}
                onClick={() => patch({ area: 'faq', tab: null, id: null })}>
          Вопросы и ответы
          {faqCounts && <span className="tab__count">{faqCounts.total}</span>}
        </button>
      </div>

      <div className={picked ? 'split split--open' : 'split'}>
        <section className="panel panel--list">
          <div className="toolbar">
            <label className="find find--wide">
              <AppIcon name="search" size={16} />
              <input type="search" value={search}
                     placeholder={area === 'faq' ? 'Поиск вопроса' : 'Поиск материала'}
                     aria-label={area === 'faq' ? 'Поиск вопроса' : 'Поиск материала'}
                     onChange={(event) => patch({ q: event.target.value || null })} />
            </label>
            <AppSelectField className="toolbar-select" label="Регион" value={region} onChange={(value) => patch({ region_id: value || null })}>
                <option value="">Все регионы</option>
                {scope.regions.map((item) => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
            </AppSelectField>
            <AppSelectField className="toolbar-select" label="Офис" value={office} onChange={(value) => patch({ office_id: value || null })}>
                <option value="">Все офисы</option>
                {(region
                  ? scope.offices.filter((item) => item.region_id === region)
                  : scope.offices
                ).map((item) => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
            </AppSelectField>
          </div>

          <div className="chips" role="tablist" aria-label="Состояние материалов">
            {(area === 'docs' ? SOURCE_TABS : FAQ_TABS).map((item) => {
              const on = item.key === (area === 'docs' ? docTab : faqTab);
              const counts = area === 'docs' ? docCounts : faqCounts;
              const number = counts
                ? area === 'docs'
                  ? sourceTabCount(item.key as SourceTab, counts)
                  : faqTabCount(item.key as FaqTab, counts)
                : null;
              return (
                <button key={item.key} type="button" role="tab" aria-selected={on}
                        className={on ? 'chip-btn chip-btn--on' : 'chip-btn'}
                        onClick={() => patch({ tab: item.key === 'all' ? null : item.key })}>
                  {item.title}
                  {number !== null && <span className="chip-btn__count">{number}</span>}
                </button>
              );
            })}
          </div>

          {area === 'docs' ? (
            <Listing block={docs} shown={docPages.items.length}
                     empty={emptyText(search, docTab !== 'all')}>
              {() => (
                <table className="people">
                  <thead>
                    <tr>
                      <th>Материал</th>
                      <th>Область</th>
                      <th>Статус</th>
                      <th>Обновлён</th>
                    </tr>
                  </thead>
                  <tbody>
                    {docPages.items.map((row) => (
                      <tr key={row.id} tabIndex={0}
                          className={row.id === picked ? 'row--on' : ''}
                          onClick={() => { patch({ id: row.id }); setInnerTab('content'); }}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault();
                              patch({ id: row.id });
                            }
                          }}>
                        <td className="grid-table__name">
                          <span className="who">
                            <AppIcon name="sheet" size={16} />
                            <span className="two">
                              <b>{row.title}</b>
                              <span className="two__second">{row.created_by ?? '—'}</span>
                            </span>
                          </span>
                        </td>
                        <td>{scopeTitle(row)}</td>
                        <td>
                          <StatusPill status={row.status}
                                      title={SOURCE_STATUS[row.status] ?? row.status} />
                        </td>
                        <td>{longDate(row.updated_at.slice(0, 10))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Listing>
          ) : (
            <Listing block={faqs} shown={faqPages.items.length}
                     empty={emptyText(search, faqTab !== 'all')}>
              {() => (
                <table className="people">
                  <thead>
                    <tr>
                      <th>Вопрос</th>
                      <th>Область</th>
                      <th>Статус</th>
                      <th>Изменён</th>
                    </tr>
                  </thead>
                  <tbody>
                    {faqPages.items.map((row) => (
                      <tr key={row.id} tabIndex={0}
                          className={row.id === picked ? 'row--on' : ''}
                          onClick={() => patch({ id: row.id })}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault();
                              patch({ id: row.id });
                            }
                          }}>
                        <td className="grid-table__name">
                          <span className="who">
                            <AppIcon name="chat" size={16} />
                            <span className="two">
                              <b>{row.canonical_question}</b>
                              <span className="two__second">
                                {row.source_title ?? 'Без связи с документом'}
                              </span>
                            </span>
                          </span>
                        </td>
                        <td>{scopeTitle(row)}</td>
                        <td>
                          <StatusPill status={row.status}
                                      title={FAQ_STATUS[row.status] ?? row.status} />
                        </td>
                        <td>{longDate(row.updated_at.slice(0, 10))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Listing>
          )}

          <div className="sheet__foot">
            <span className="muted">{shownLine(pages, total)}</span>
            <span className="sheet__right">
              {pages.error && (
                <span className="field__bad">Не удалось дочитать список</span>
              )}
              {pages.hasMore && (
                <button type="button" className="btn btn--small" disabled={pages.busy}
                        onClick={() => pages.loadMore()}>
                  {pages.busy ? 'Читаем…' : 'Показать ещё'}
                </button>
              )}
            </span>
          </div>
        </section>

        {picked && (
          <section className="panel panel--view" aria-label="Выбранный материал">
            {area === 'docs' ? (
              <DocView
                block={openDoc}
                versions={versions}
                capability={capability}
                tab={innerTab}
                onTab={setInnerTab}
                shownVersion={shown}
                oldVersion={oldVersion}
                onShowVersion={setShown}
                mayWrite={mayWrite}
                mayPublish={mayPublish}
                mayIndex={mayIndex}
                acting={acting}
                error={actionError}
                onClose={() => patch({ id: null })}
                onEdit={(doc) => setForm({ mode: 'document', editing: doc, newVersionOf: null })}
                onNewVersion={(doc) =>
                  setForm({ mode: 'document', editing: null, newVersionOf: doc })}
                onIndex={(doc) => void act(() => api.indexSource(doc.id))}
                onPublish={(doc) => void act(() => api.publishSource(doc.id))}
                onArchive={(doc) => void act(() => api.archiveSource(doc.id))}
              />
            ) : (
              <FaqView
                block={openFaq}
                capability={capability}
                mayWrite={mayWrite}
                mayPublish={mayPublish}
                acting={acting}
                error={actionError}
                onClose={() => patch({ id: null })}
                onEdit={(row) => setForm({ mode: 'faq', editing: row, newVersionOf: null })}
                onActivate={(row) => void act(() => api.activateFaq(row.id))}
                onArchive={(row) => void act(() => api.archiveFaq(row.id))}
              />
            )}
          </section>
        )}
      </div>

      {form && (
        <MaterialForm
          mode={form.mode}
          editing={form.editing}
          newVersionOf={form.newVersionOf}
          scope={scope}
          documents={docs.state === 'ready' ? docs.data.page.items : []}
          onClose={() => setForm(null)}
          onSaved={(kind, id) => {
            setForm(null);
            // Список и карточка перечитываются: материал мог перестать
            // подходить под текущий фильтр, и молчать об этом нельзя.
            patch({ area: kind === 'faq' ? 'faq' : null, id });
            setAttempt((n) => n + 1);
          }}
        />
      )}
    </AppShell>
  );
}

// --- карточка документа -----------------------------------------------------

type DocProps = {
  block: Block<api.Source>;
  versions: Block<api.SourceRow[]>;
  capability: api.Capability | null;
  tab: 'content' | 'versions';
  onTab: (tab: 'content' | 'versions') => void;
  shownVersion: string;
  oldVersion: Block<api.Source>;
  onShowVersion: (id: string) => void;
  mayWrite: boolean;
  mayPublish: boolean;
  mayIndex: boolean;
  acting: boolean;
  error: string | null;
  onClose: () => void;
  onEdit: (doc: api.Source) => void;
  onNewVersion: (doc: api.Source) => void;
  onIndex: (doc: api.Source) => void;
  onPublish: (doc: api.Source) => void;
  onArchive: (doc: api.Source) => void;
};

function DocView({
  block, versions, capability, tab, onTab, shownVersion, oldVersion, onShowVersion,
  mayWrite, mayPublish, mayIndex, acting, error,
  onClose, onEdit, onNewVersion, onIndex, onPublish, onArchive,
}: DocProps) {
  if (block.state === 'loading') return <p className="empty">Открываем документ…</p>;
  if (block.state === 'denied') return <p className="empty">Сессия истекла. Войдите заново.</p>;
  if (block.state === 'error') {
    return (
      <p className="empty empty--bad">
        Не удалось открыть документ. Выберите его ещё раз.
      </p>
    );
  }

  const doc = block.data;
  const rows = versions.state === 'ready' ? versions.data : [];
  const older = rows.find((row) => row.id === shownVersion);
  const notYet = indexBlockedBecause(doc, capability);
  const draft = doc.status === 'DRAFT' || doc.status === 'ERROR';

  return (
    <>
      <header className="view__head">
        <span className="view__icon" aria-hidden="true">
          <AppIcon name="sheet" size={20} />
        </span>
        <div className="view__who">
          <h2 className="view__title">{doc.title}</h2>
          <p className="view__sub">
            {SOURCE_TYPE[doc.source_type] ?? doc.source_type} · {doc.id.slice(0, 8)}
          </p>
          <p className="view__badges">
            <StatusPill status={doc.status} title={SOURCE_STATUS[doc.status] ?? doc.status} />
            <span className="view__version">Версия {doc.version}</span>
          </p>
        </div>
        <button type="button" className="tool" aria-label="Закрыть карточку" onClick={onClose}>
          <AppIcon name="cross" size={18} />
        </button>
      </header>

      <dl className="facts">
        <div><dt>Область</dt><dd>{scopeTitle(doc)}</dd></div>
        <div><dt>Автор</dt><dd>{doc.created_by ?? '—'}</dd></div>
        <div><dt>Обновлён</dt><dd>{longDate(doc.updated_at.slice(0, 10))}</dd></div>
      </dl>

      <div className="tabs tabs--inner" role="tablist" aria-label="Что показать">
        <button type="button" role="tab" aria-selected={tab === 'content'}
                className={tab === 'content' ? 'tab tab--on' : 'tab'}
                onClick={() => onTab('content')}>
          Содержание
        </button>
        <button type="button" role="tab" aria-selected={tab === 'versions'}
                className={tab === 'versions' ? 'tab tab--on' : 'tab'}
                onClick={() => onTab('versions')}>
          Версии
          {versions.state === 'ready' && (
            <span className="tab__count">{rows.length}</span>
          )}
        </button>
      </div>

      <div className="view__body">
        {tab === 'content' ? (
          <>
            {older && (
              <p className="view__old" role="status">
                Показана предыдущая версия {older.version} от{' '}
                {longDate(older.updated_at.slice(0, 10))} — это не текущий текст.{' '}
                <button type="button" className="link" onClick={() => onShowVersion('')}>
                  Вернуться к текущей
                </button>
              </p>
            )}
            {older ? (
              oldVersion.state === 'ready' ? (
                <Markup content={oldVersion.data.content} />
              ) : oldVersion.state === 'loading' ? (
                <p className="empty">Читаем версию…</p>
              ) : (
                <p className="empty empty--bad">Не удалось открыть эту версию.</p>
              )
            ) : (
              <Markup content={doc.content} />
            )}
          </>
        ) : versions.state === 'error' ? (
          <p className="empty empty--bad">Не удалось загрузить историю версий.</p>
        ) : versions.state === 'loading' ? (
          <p className="empty">Читаем историю…</p>
        ) : (
          <ol className="versions">
            {rows.map((row) => (
              <li key={row.id} className={row.id === doc.id ? 'versions__now' : ''}>
                <span className="versions__no">v{row.version}</span>
                <span className="two">
                  <b>{SOURCE_STATUS[row.status] ?? row.status}</b>
                  <span className="two__second">
                    {row.created_by ?? '—'} · {longDate(row.updated_at.slice(0, 10))}
                  </span>
                </span>
                {row.id === doc.id ? (
                  <span className="muted">Открыта</span>
                ) : (
                  <button type="button" className="link"
                          onClick={() => { onShowVersion(row.id); onTab('content'); }}>
                    Посмотреть
                  </button>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>

      {error && <p className="empty empty--bad" role="alert">{error}</p>}

      <footer className="view__foot">
        <div className="view__actions">
          {canEditInPlace(doc) ? (
            <button type="button" className="btn btn--dark" disabled={!mayWrite}
                    title={mayWrite ? undefined : 'Нужно право knowledge.write'}
                    onClick={() => onEdit(doc)}>
              <AppIcon name="pencil" size={16} />
              Редактировать
            </button>
          ) : (
            // Действующую версию правят НОВОЙ версией: пока она
            // черновик, сотрудникам продолжает отвечать текущая.
            <button type="button" className="btn btn--dark" disabled={!mayWrite}
                    onClick={() => onNewVersion(doc)}>
              <AppIcon name="pencil" size={16} />
              Новая версия
            </button>
          )}
          {draft ? (
            // Путь к публикации начинается с индексации, и когда она
            // недоступна — кнопка гаснет с причиной, а не отправляет
            // задание, которое некому выполнить.
            <button type="button" className="btn"
                    disabled={!mayIndex || Boolean(notYet) || acting}
                    title={notYet ? `Сейчас нельзя: ${notYet}` : undefined}
                    onClick={() => onIndex(doc)}>
              <AppIcon name="refresh" size={16} />
              Отправить на индексацию
            </button>
          ) : doc.status === 'INDEXING' ? (
            <button type="button" className="btn" disabled={!mayPublish || acting}
                    onClick={() => onPublish(doc)}>
              <AppIcon name="check" size={16} />
              Опубликовать
            </button>
          ) : null}
          <button type="button" className="btn"
                  disabled={!mayPublish || !canArchive(doc) || acting}
                  title={
                    canArchive(doc)
                      ? undefined
                      : 'Архивируют опубликованный документ: у черновика нет публикации, которую снимают'
                  }
                  onClick={() => onArchive(doc)}>
            <AppIcon name="archive" size={16} />
            В архив
          </button>
        </div>
        <p className="view__note">{readinessNote(doc, capability)}</p>
        {draft && notYet && (
          <p className="view__note view__note--dim">Индексация недоступна: {notYet}</p>
        )}
      </footer>
    </>
  );
}

// --- карточка FAQ -----------------------------------------------------------

function FaqView({
  block, capability, mayWrite, mayPublish, acting, error,
  onClose, onEdit, onActivate, onArchive,
}: {
  block: Block<api.FaqRow>;
  capability: api.Capability | null;
  mayWrite: boolean;
  mayPublish: boolean;
  acting: boolean;
  error: string | null;
  onClose: () => void;
  onEdit: (row: api.FaqRow) => void;
  onActivate: (row: api.FaqRow) => void;
  onArchive: (row: api.FaqRow) => void;
}) {
  if (block.state === 'loading') return <p className="empty">Открываем вопрос…</p>;
  if (block.state === 'denied') return <p className="empty">Сессия истекла. Войдите заново.</p>;
  if (block.state === 'error') {
    return <p className="empty empty--bad">Не удалось открыть вопрос.</p>;
  }

  const row = block.data;
  const notYet = faqActivateBlockedBecause(row, capability);
  return (
    <>
      <header className="view__head">
        <span className="view__icon" aria-hidden="true">
          <AppIcon name="chat" size={20} />
        </span>
        <div className="view__who">
          <h2 className="view__title">{row.canonical_question}</h2>
          <p className="view__sub">Вопрос и ответ · {row.id.slice(0, 8)}</p>
          <p className="view__badges">
            <StatusPill status={row.status} title={FAQ_STATUS[row.status] ?? row.status} />
          </p>
        </div>
        <button type="button" className="tool" aria-label="Закрыть карточку" onClick={onClose}>
          <AppIcon name="cross" size={18} />
        </button>
      </header>

      <dl className="facts">
        <div><dt>Область</dt><dd>{scopeTitle(row)}</dd></div>
        <div><dt>Документ</dt><dd>{row.source_title ?? 'Не связан'}</dd></div>
        <div><dt>Изменён</dt><dd>{longDate(row.updated_at.slice(0, 10))}</dd></div>
      </dl>

      <div className="view__body">
        <h3 className="doc__h1">Ответ HR</h3>
        <Markup content={row.approved_answer} />
      </div>

      {error && <p className="empty empty--bad" role="alert">{error}</p>}

      <footer className="view__foot">
        <div className="view__actions">
          <button type="button" className="btn btn--dark" disabled={!mayWrite}
                  onClick={() => onEdit(row)}>
            <AppIcon name="pencil" size={16} />
            Редактировать
          </button>
          {row.status === 'DRAFT' && (
            // Включение в ответы — отдельное решение человека, и когда
            // оно невозможно, кнопка гаснет с причиной, а не отправляет
            // запрос, который сервер обязан отклонить.
            <button type="button" className="btn"
                    disabled={!mayPublish || Boolean(notYet) || acting}
                    title={notYet ? `Сейчас нельзя: ${notYet}` : undefined}
                    onClick={() => onActivate(row)}>
              <AppIcon name="check" size={16} />
              Включить в ответы
            </button>
          )}
          <button type="button" className="btn"
                  disabled={!mayPublish || row.status === 'ARCHIVED' || acting}
                  onClick={() => onArchive(row)}>
            <AppIcon name="archive" size={16} />
            В архив
          </button>
        </div>
        {/* Второй строки с причиной здесь нет намеренно: подпись
            о готовности уже называет её словами, а повтор в двух
            предложениях подряд читается как две разные помехи. */}
        <p className="view__note">{faqReadinessNote(row, capability)}</p>
      </footer>
    </>
  );
}

// --- мелочи -----------------------------------------------------------------

function AiBadge({ capability }: { capability: api.Capability | null }) {
  if (capability === null) return null;
  if (capability.embeddings_available) {
    return (
      <span className="ai-badge">
        <AppIcon name="half" size={16} />
        AI-ответы включены
      </span>
    );
  }
  return (
    <span className="ai-badge" title={
      capability.reason === 'ai_disabled'
        ? 'Рубильник AI_ASSISTANT_ENABLED выключен'
        : 'Провайдер эмбеддингов не настроен'
    }>
      <AppIcon name="half" size={16} />
      {capability.reason === 'ai_disabled'
        ? 'AI-ответы выключены'
        : 'AI-провайдер не настроен'}
    </span>
  );
}

function StatusPill({ status, title }: { status: string; title: string }) {
  const icon =
    status === 'ACTIVE' ? 'check'
      : status === 'ARCHIVED' ? 'archive'
        : status === 'ERROR' ? 'alert'
          : status === 'INDEXING' ? 'refresh'
            : 'pencil';
  return (
    <span className={`state state--${status.toLowerCase()}`}>
      <AppIcon name={icon} size={16} />
      {title}
    </span>
  );
}

function Listing<T>({ block, shown, empty, children }: {
  block: Block<T>;
  /** Сколько строк уже накоплено: пустоту считаем по ним, а не по первой странице. */
  shown: number;
  empty: string;
  children: (data: T) => React.ReactNode;
}) {
  if (block.state === 'loading') return <p className="empty">Загружаем материалы…</p>;
  if (block.state === 'denied') return <p className="empty">Сессия истекла. Войдите заново.</p>;
  if (block.state === 'error') {
    return (
      <p className="empty empty--bad">
        Не удалось загрузить список. Это ошибка запроса, а не пустая база.
      </p>
    );
  }
  if (shown === 0) return <p className="empty">{empty}</p>;
  return <div className="scroller">{children(block.data)}</div>;
}

function emptyText(search: string, filtered: boolean): string {
  if (search) return 'По этому запросу ничего не нашлось.';
  if (filtered) return 'В этом состоянии материалов нет. Проверьте другие вкладки.';
  return 'Материалов пока нет. Нажмите «Добавить материал», чтобы завести первый.';
}

/**
 * Честная подпись под списком.
 *
 * «Показаны все» пишется, только когда следующей страницы нет И
 * накопленное совпало со счётчиком открытой вкладки. Счётчик приходит
 * с сервера по всему набору — длина массива за итог не выдаётся ни при
 * каких обстоятельствах.
 */
export function shownLine(
  page: { items: unknown[]; hasMore: boolean },
  total: number | null,
): string {
  const shown = page.items.length;
  // Пустому списку подпись не нужна: «показаны все 0» — это шум под
  // объяснением, которое уже стоит на месте таблицы.
  if (shown === 0) return '';
  if (total === null) return `Показано ${shown}`;
  if (!page.hasMore && shown >= total) return `Показаны все ${shown}`;
  return `Показано ${shown} из ${total}`;
}
