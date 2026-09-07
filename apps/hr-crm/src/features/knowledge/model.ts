/**
 * Правила базы знаний, вынесенные из разметки.
 *
 * Три состояния, которые нельзя смешивать, и на этой странице их
 * различают явно:
 *
 *   сохранён   — DRAFT. Работает всегда, никакого AI не требует;
 *   опубликован — ACTIVE. Требует проиндексированных кусков;
 *   отвечает сотрудникам — ACTIVE плюс посчитанные эмбеддинги.
 *
 * При выключенном ассистенте второе и третье недостижимы, и интерфейс
 * обязан сказать это словами, а не назвать черновик опубликованным.
 */

import type { Capability, FaqRow, SourceRow } from '../../api/crm';

/** Подпись состояния документа. Ровно пять, все из backend. */
export const SOURCE_STATUS: Record<string, string> = {
  DRAFT: 'Черновик',
  INDEXING: 'Индексируется',
  ACTIVE: 'Опубликован',
  ARCHIVED: 'Архив',
  ERROR: 'Ошибка индексации',
};

export const FAQ_STATUS: Record<string, string> = {
  DRAFT: 'Черновик',
  ACTIVE: 'В ответах',
  ARCHIVED: 'Архив',
};

/** Вид документа. Используется в форме и в карточке. */
export const SOURCE_TYPE: Record<string, string> = {
  POLICY: 'Правило',
  INSTRUCTION: 'Инструкция',
  DOCUMENT: 'Документ',
  FAQ: 'Вопрос-ответ',
};

/**
 * Вкладки списка документов.
 *
 * Правило, которое здесь соблюдается: число на вкладке равно числу
 * строк, которые по ней покажутся. Поэтому набор состояний вкладки и
 * слагаемые её счётчика — это одно и то же, и врозь их менять нельзя.
 *
 * Индексируемая версия лежит с черновиками: она ещё НЕ опубликована и
 * сотрудникам не отвечает. Во вкладке «Опубликованы» ей делать нечего —
 * там только то, что действительно действует.
 */
export const SOURCE_TABS = [
  { key: 'all', title: 'Все', statuses: '' },
  { key: 'draft', title: 'Черновики', statuses: 'DRAFT,ERROR,INDEXING' },
  { key: 'published', title: 'Опубликованы', statuses: 'ACTIVE' },
  { key: 'archived', title: 'Архив', statuses: 'ARCHIVED' },
] as const;

export type SourceTab = (typeof SOURCE_TABS)[number]['key'];

export const FAQ_TABS = [
  { key: 'all', title: 'Все', statuses: '' },
  { key: 'draft', title: 'Черновики', statuses: 'DRAFT' },
  { key: 'active', title: 'В ответах', statuses: 'ACTIVE' },
  { key: 'archived', title: 'Архив', statuses: 'ARCHIVED' },
] as const;

export type FaqTab = (typeof FAQ_TABS)[number]['key'];

type Counts = Record<string, number> & { total: number };

export function sourceTabCount(tab: SourceTab, counts: Counts): number {
  if (tab === 'all') return counts['total'] ?? 0;
  if (tab === 'draft') {
    return (counts['DRAFT'] ?? 0) + (counts['ERROR'] ?? 0) + (counts['INDEXING'] ?? 0);
  }
  if (tab === 'published') return counts['ACTIVE'] ?? 0;
  return counts['ARCHIVED'] ?? 0;
}

export function faqTabCount(tab: FaqTab, counts: Counts): number {
  if (tab === 'all') return counts['total'] ?? 0;
  if (tab === 'draft') return counts['DRAFT'] ?? 0;
  if (tab === 'active') return counts['ACTIVE'] ?? 0;
  return counts['ARCHIVED'] ?? 0;
}

/**
 * Область действия словами.
 *
 * Пустая область — это ВСЯ организация, и так её и называем. Но офис
 * с регионом одновременно модель запрещает, поэтому «Ташкент · Главный»
 * из макета невозможно: у документа либо офис, либо регион.
 */
export function scopeTitle(row: {
  office_name: string | null;
  region_name: string | null;
}): string {
  if (row.office_name) return row.office_name;
  if (row.region_name) return row.region_name;
  return 'Вся организация';
}

/**
 * Можно ли архивировать документ.
 *
 * Архивируют ДЕЙСТВУЮЩУЮ версию: у черновика нет публикации, которую
 * снимают, и сервер такой запрос отклоняет. Кнопка на черновике —
 * это обещание удаления, которого здесь нет.
 */
export const canArchive = (row: { status: string }): boolean =>
  row.status === 'ACTIVE';

/**
 * Можно ли править документ прямо.
 *
 * Действующую версию правят НОВОЙ версией, а не поверх: иначе
 * сотрудникам в середине правки отвечал бы полуотредактированный текст.
 */
export const canEditInPlace = (row: { status: string }): boolean =>
  row.status === 'DRAFT' || row.status === 'ERROR';

/**
 * Почему индексацию сейчас нельзя запустить. `null` — можно.
 *
 * Путь к публикации ровно один: черновик → индексация → публикация.
 * Непроиндексированный документ сервер публиковать отказывается, и
 * обходить это нечем — в ответах сотрудникам он всё равно не появится.
 *
 * Когда индексация недоступна, кнопка гасится ЗАРАНЕЕ и с причиной:
 * отправлять заведомо обречённый запрос значит поставить задание,
 * которое некому выполнить.
 */
export function indexBlockedBecause(
  row: { status: string },
  capability: Capability | null,
): string | null {
  if (row.status === 'ACTIVE') {
    return 'действующую версию не переиндексируют, для правки заводят новую';
  }
  if (row.status === 'ARCHIVED') return 'архивный документ не индексируется';
  if (row.status === 'INDEXING') return 'индексация уже идёт';
  if (capability && !capability.embeddings_available) {
    return capability.reason === 'ai_disabled'
      ? 'AI-ассистент выключен, поэтому документ нельзя проиндексировать, '
        + 'а без индексации он не попадёт в автоматические ответы'
      : 'AI-провайдер не настроен до конца';
  }
  return null;
}

/**
 * Что сказать про готовность материала отвечать сотрудникам.
 *
 * «Сохранён» и «участвует в ответах» — разные вещи, и подпись обязана
 * их разделять.
 */
export function readinessNote(
  row: Pick<SourceRow, 'status'>,
  capability: Capability | null,
): string {
  if (row.status === 'ACTIVE') return 'Документ участвует в ответах сотрудникам.';
  if (row.status === 'ARCHIVED') return 'Архивный документ в ответах не участвует.';
  if (row.status === 'ERROR') return 'Индексация не удалась — в ответах документ не участвует.';
  if (row.status === 'INDEXING') {
    return 'Идёт индексация. Версия ещё не опубликована и в ответах не участвует.';
  }
  if (capability && !capability.embeddings_available) {
    return 'Черновик сохранён. В автоматические ответы он не попадает: AI-ассистент выключен.';
  }
  return 'Черновик не используется для ответов сотрудникам.';
}

/**
 * Почему запись нельзя включить в автоматические ответы. `null` — можно.
 *
 * Поиск отбирает записи по посчитанному эмбеддингу, и сервер отказывает
 * включить запись без него: «включено, но не работает» — худшее из
 * состояний, потому что выглядит рабочим.
 */
export function faqActivateBlockedBecause(
  row: Pick<FaqRow, 'indexed'>,
  capability: Capability | null,
): string | null {
  if (row.indexed) return null;
  if (capability && !capability.embeddings_available) {
    return capability.reason === 'ai_disabled'
      ? 'AI-ассистент выключен, и эмбеддинг записи посчитать нечем'
      : 'AI-провайдер не настроен до конца';
  }
  return 'у записи ещё нет посчитанного эмбеддинга';
}

export function faqReadinessNote(
  row: Pick<FaqRow, 'status' | 'indexed'>,
  capability: Capability | null,
): string {
  if (row.status === 'ACTIVE') return 'Ответ выдаётся сотрудникам автоматически.';
  if (row.status === 'ARCHIVED') return 'Архивная запись в ответах не участвует.';
  if (!row.indexed && capability && !capability.embeddings_available) {
    return 'Черновик сохранён. Включить в автоматические ответы нельзя: AI-ассистент выключен.';
  }
  if (!row.indexed) return 'Запись не проиндексирована — в автоответы она не попадёт.';
  return 'Черновик сохранён, в автоответах не участвует.';
}
