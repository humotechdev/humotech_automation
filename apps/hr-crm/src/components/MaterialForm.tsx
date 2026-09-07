/**
 * Форма материала: документ или пара «вопрос — ответ».
 *
 * Редактор обычный `textarea` с подсказкой по разметке. Тянуть
 * визуальный редактор ради нескольких абзацев регламента незачем: он
 * весит больше всей страницы и приносит свою модель документа, из
 * которой в `TextField` backend всё равно уедет текст.
 *
 * Три правила, которые здесь соблюдаются:
 *
 * — при отправке форма заблокирована, а введённое остаётся на месте,
 *   если сервер отказал: набирать всё заново из-за одной ошибки —
 *   худшее, что можно сделать с человеком, написавшим три абзаца;
 * — закрытие с несохранёнными правками спрашивает подтверждение;
 * — правка ДЕЙСТВУЮЩЕГО документа делает новую версию, а не пишет
 *   поверх: иначе сотрудникам в середине правки отвечал бы
 *   полуотредактированный текст.
 */

import { useEffect, useMemo, useState } from 'react';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { Icon } from './nav-icons';
import { SOURCE_TYPE } from '../features/knowledge/model';

type Scope = { regions: api.Region[]; offices: api.Office[] };

type Props = {
  mode: 'document' | 'faq';
  /** Что правим. `null` — заводим новое. */
  editing: api.Source | api.FaqRow | null;
  /** Документ, поверх которого делается новая версия. */
  newVersionOf: api.Source | null;
  scope: Scope;
  documents: api.SourceRow[];
  onClose: () => void;
  onSaved: (kind: 'document' | 'faq', id: string) => void;
};

export function MaterialForm({
  mode, editing, newVersionOf, scope, documents, onClose, onSaved,
}: Props) {
  const doc = editing && 'content' in editing ? editing : null;
  const faq = editing && 'canonical_question' in editing ? editing : null;
  const base = newVersionOf ?? doc;

  const [title, setTitle] = useState(base?.title ?? '');
  const [type, setType] = useState(base?.source_type ?? 'POLICY');
  const [content, setContent] = useState(base?.content ?? '');
  const [question, setQuestion] = useState(faq?.canonical_question ?? '');
  const [answer, setAnswer] = useState(faq?.approved_answer ?? '');
  const [linked, setLinked] = useState(faq?.source_id ?? '');
  const [region, setRegion] = useState(base?.region_id ?? faq?.region_id ?? '');
  const [office, setOffice] = useState(base?.office_id ?? faq?.office_id ?? '');

  // Название — это ИМЯ ДОКУМЕНТА: история версий собирается по паре
  // «заголовок + язык», а не по ссылке на родителя. Переименовать
  // версию значит увести её в отдельный документ: у родителя её
  // в истории не будет, у неё в истории не будет родителя, а номер
  // версии останется прежним и будет врать. Поэтому у документа
  // с историей название заперто.
  const titleLocked = Boolean(newVersionOf) || (doc?.version ?? 1) > 1;

  const [busy, setBusy] = useState(false);
  const [fields, setFields] = useState<Record<string, string[]>>({});
  const [failure, setFailure] = useState<string | null>(null);

  const start = useMemo(
    () => JSON.stringify({ title, type, content, question, answer, linked, region, office }),
    // Снимок берётся один раз при открытии: он и есть «как было».
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  const now = JSON.stringify({ title, type, content, question, answer, linked, region, office });
  const dirty = now !== start;

  // Офис, выпавший из нового региона, сбрасывается: модель запрещает
  // офис и регион одновременно, и показанное должно совпадать
  // с отправляемым.
  const offices = useMemo(
    () => (region ? scope.offices.filter((item) => item.region_id === region) : scope.offices),
    [scope.offices, region],
  );
  useEffect(() => {
    // Пока справочник не пришёл, список пуст — и «офиса нет в регионе»
    // означало бы «сотри унаследованный офис», чего делать нельзя.
    if (!office || !region || scope.offices.length === 0) return;
    if (!offices.some((item) => item.id === office)) setOffice('');
  }, [offices, office, region, scope.offices.length]);

  useEffect(() => {
    const stop = new AbortController();
    document.addEventListener(
      'keydown',
      (event) => {
        if (event.key === 'Escape') close();
      },
      { signal: stop.signal },
    );
    return () => stop.abort();
  });

  function close() {
    if (dirty && !window.confirm('Правки не сохранены. Закрыть форму?')) return;
    onClose();
  }

  async function save() {
    if (busy) return;
    setBusy(true);
    setFields({});
    setFailure(null);
    try {
      if (mode === 'faq') {
        const saved = faq
          ? await api.updateFaq(faq.id, {
              canonical_question: question,
              approved_answer: answer,
            })
          : await api.createFaq({
              canonical_question: question,
              approved_answer: answer,
              language: 'ru',
              ...(linked ? { source_id: linked } : {}),
              ...(office ? { office_id: office } : {}),
              ...(region && !office ? { region_id: region } : {}),
            });
        onSaved('faq', saved.id);
        return;
      }

      // Правка черновика идёт PATCH-ем, а новая версия — созданием
      // с `parent_source_id`: разрушительной перезаписи опубликованного
      // текста в этом сценарии нет.
      const saved =
        doc && !newVersionOf
          ? await api.updateSource(doc.id, { title, content })
          : await api.createSource({
              title,
              source_type: type,
              language: 'ru',
              content,
              ...(newVersionOf ? { parent_source_id: newVersionOf.id } : {}),
              ...(office ? { office_id: office } : {}),
              ...(region && !office ? { region_id: region } : {}),
            });
      onSaved('document', saved.id);
    } catch (error) {
      if (error instanceof ApiFailure) {
        setFields(error.fields);
        setFailure(messageFor(error));
      } else {
        setFailure(messageFor(error));
      }
    } finally {
      // Введённое остаётся в полях: сервер отказал, а текст никуда
      // не делся.
      setBusy(false);
    }
  }

  const heading = faq
    ? 'Изменить вопрос и ответ'
    : newVersionOf
      ? 'Новая версия документа'
      : doc
        ? 'Изменить документ'
        : mode === 'faq'
          ? 'Новый вопрос и ответ'
          : 'Новый документ';

  const ready =
    mode === 'faq'
      ? question.trim().length > 0 && answer.trim().length > 0
      : title.trim().length > 0 && content.trim().length > 0;

  return (
    <>
      <div className="veil" onClick={close} aria-hidden="true" />
      <aside className="drawer" role="dialog" aria-label={heading} aria-modal="true">
        <header className="drawer__head">
          <span className="drawer__who">
            <span className="drawer__name">{heading}</span>
            {newVersionOf && (
              <span className="drawer__id">
                На основе версии {newVersionOf.version} — действующая останется
                в силе, пока новая не опубликована
              </span>
            )}
          </span>
          <button type="button" className="tool" aria-label="Закрыть форму" onClick={close}>
            <Icon name="cross" size={18} />
          </button>
        </header>

        <div className="drawer__body">
          {mode === 'document' ? (
            <>
              <label className="field">
                <span className="field__label">Название</span>
                <span className="field__box">
                  <input value={title} aria-label="Название документа"
                         disabled={titleLocked}
                         onChange={(event) => setTitle(event.target.value)} />
                </span>
                {titleLocked && (
                  <span className="field__hint">
                    По названию собирается история версий — у документа
                    с историей оно не меняется
                  </span>
                )}
                {fields['title']?.[0] && (
                  <span className="field__bad" role="alert">{fields['title'][0]}</span>
                )}
              </label>

              <label className="field">
                <span className="field__label">Вид</span>
                <span className="field__box">
                  <select value={type} aria-label="Вид документа"
                          disabled={Boolean(doc && !newVersionOf)}
                          onChange={(event) => setType(event.target.value as typeof type)}>
                    {Object.entries(SOURCE_TYPE).map(([key, name]) => (
                      <option key={key} value={key}>{name}</option>
                    ))}
                  </select>
                </span>
              </label>

              <label className="field">
                <span className="field__label">Содержание</span>
                <textarea
                  className="editor"
                  value={content}
                  rows={14}
                  aria-label="Содержание документа"
                  onChange={(event) => setContent(event.target.value)}
                />
                <span className="field__hint">
                  {'# заголовок · - список · 1. нумерация · **важное** · > примечание'}
                </span>
                {fields['content']?.[0] && (
                  <span className="field__bad" role="alert">{fields['content'][0]}</span>
                )}
              </label>
            </>
          ) : (
            <>
              <label className="field">
                <span className="field__label">Вопрос сотрудника</span>
                <textarea className="editor editor--short" value={question} rows={3}
                          aria-label="Вопрос сотрудника"
                          onChange={(event) => setQuestion(event.target.value)} />
                {fields['canonical_question']?.[0] && (
                  <span className="field__bad" role="alert">
                    {fields['canonical_question'][0]}
                  </span>
                )}
              </label>

              <label className="field">
                <span className="field__label">Ответ HR</span>
                {/* Ответ пишет человек. Кнопки «сгенерировать» здесь
                    нет и не будет: ответ сотруднику от имени компании
                    не должен появляться сам. */}
                <textarea className="editor" value={answer} rows={8}
                          aria-label="Ответ HR"
                          onChange={(event) => setAnswer(event.target.value)} />
                {fields['approved_answer']?.[0] && (
                  <span className="field__bad" role="alert">
                    {fields['approved_answer'][0]}
                  </span>
                )}
              </label>

              {!faq && (
                <label className="field">
                  <span className="field__label">Связанный документ</span>
                  <span className="field__box">
                    <select value={linked} aria-label="Связанный документ"
                            onChange={(event) => setLinked(event.target.value)}>
                      <option value="">Без связи</option>
                      {documents.map((item) => (
                        <option key={item.id} value={item.id}>{item.title}</option>
                      ))}
                    </select>
                  </span>
                </label>
              )}
            </>
          )}

          {/* Область задаётся только при заведении материала. У новой
              версии она наследуется от предыдущей и отправляется как
              есть: расходиться от версии к версии область одного
              документа не должна. */}
          {!editing && !newVersionOf && (
            <div className="two-fields">
              <label className="field">
                <span className="field__label">Регион</span>
                <span className="field__box">
                  <select value={region} aria-label="Регион материала"
                          onChange={(event) => setRegion(event.target.value)}>
                    <option value="">Вся организация</option>
                    {scope.regions.map((item) => (
                      <option key={item.id} value={item.id}>{item.name}</option>
                    ))}
                  </select>
                </span>
              </label>
              <label className="field">
                <span className="field__label">Офис</span>
                <span className="field__box">
                  <select value={office} aria-label="Офис материала"
                          onChange={(event) => setOffice(event.target.value)}>
                    <option value="">
                      {region ? 'Весь регион' : 'Вся организация'}
                    </option>
                    {offices.map((item) => (
                      <option key={item.id} value={item.id}>{item.name}</option>
                    ))}
                  </select>
                </span>
                <span className="field__hint">
                  Офис и регион одновременно указать нельзя
                </span>
              </label>
            </div>
          )}

          {failure && (
            <p className="empty empty--bad" role="alert">{failure}</p>
          )}
        </div>

        <footer className="drawer__foot">
          <button type="button" className="btn" onClick={close}>Отмена</button>
          <button type="button" className="btn btn--dark" disabled={!ready || busy}
                  onClick={() => void save()}>
            {busy ? 'Сохраняем…' : 'Сохранить'}
          </button>
        </footer>
      </aside>
    </>
  );
}
