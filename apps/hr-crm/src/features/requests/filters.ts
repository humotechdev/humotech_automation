/**
 * Отбор очереди заявок — адресом страницы «Заявки».
 *
 * Главная, карточка сотрудника и сама страница говорят об одном отборе
 * одними словами: ссылка с главной кладёт в адрес ровно те параметры,
 * которые страница потом читает и отправляет серверу. Второго пути
 * «применить фильтр» нет — ни имитации клика по вкладке, ни отложенной
 * подстановки после открытия.
 *
 * Числа главной считаются по тем же условиям, что и список по ссылке:
 * правила ниже читают только то, что прислал сервер (`stage`,
 * `missing_for_approval`, `kind`), и повторяют серверные отборы
 * `stage` / `certificate` / `type` / `request_kind`.
 */

import * as api from '../../api/crm';

/** Заявка, по которой ещё не решили. */
export const OPEN_STATUSES = 'SUBMITTED,IN_REVIEW';

/*
 * Пункты фильтра «Статус». Это группы, а не статусы базы: «ожидают
 * документа», «на рассмотрении» и «исправление» — всё SUBMITTED или
 * IN_REVIEW, различает их стадия (`stage_of` на сервере). Поэтому пункт
 * уходит серверу стадией, и группы не пересекаются: раньше «На
 * рассмотрении» был SUBMITTED,IN_REVIEW и собирал все три вместе.
 */
export const STATUS_OPTIONS = [
  { id: 'waiting', name: 'Ожидают документа' },
  { id: 'open', name: 'На рассмотрении' },
  { id: 'fix', name: 'Исправление' },
  { id: 'APPROVED', name: 'Одобрена' },
  { id: 'REJECTED', name: 'Отклонена' },
  // Отозвана сотрудником или снята отменой — не отказ кадровика.
  { id: 'CANCELLED', name: 'Отозвана' },
];

const STAGES_OF: Record<string, string> = {
  waiting: 'WAITING_DOCUMENTS',
  // Ждут кадровика: справка у него, или бумаг не нужно (отпуск,
  // исправление отметки — у них стадия PENDING).
  open: 'HR_REVIEW,PENDING',
  fix: 'NEEDS_FIX',
  APPROVED: 'APPROVED',
  REJECTED: 'REJECTED',
  CANCELLED: 'CANCELLED',
};

/**
 * Пункт фильтра из адреса.
 *
 * `status=open` ставят ссылки главной и карточки сотрудника; старые
 * ссылки несут статусы базы `SUBMITTED,IN_REVIEW`. Раньше «open» уходил
 * серверу как статус, которого тот не знает, и список по ссылке
 * приходил пустым, а ручной выбор вкладки, сбрасывавший статус,
 * «чинил» его.
 */
export function queueStatus(raw: string): string {
  return raw === OPEN_STATUSES ? 'open' : raw;
}

/** Стадии пункта фильтра; `null` — пункт не из списка, уйдёт статусом как есть. */
export function stagesOf(token: string): string | null {
  return STAGES_OF[token] ?? null;
}

/**
 * Пересечение отборов по стадии: вкладки, пункта фильтра и ссылки.
 * `undefined` — отбора нет; пустая строка — не подходит ничего.
 */
export function bothStages(...lists: (string | null | undefined)[]): string | undefined {
  let result: string[] | undefined;
  for (const list of lists) {
    if (!list) continue;
    const codes = list.split(',').filter(Boolean);
    result = result ? result.filter((one) => codes.includes(one)) : codes;
  }
  return result?.join(',');
}

/** Больничные, где справку ещё не приносили, — пункт «Ожидают документа». */
export const WAITING_PAPER = { status: 'waiting' } as const;
/** Справка принесена и ждёт проверки кадровиком — принятая сюда не входит. */
export const PAPER_ON_REVIEW = { status: 'open', certificate: 'PENDING' } as const;

const LEAVE_TYPES = ['ANNUAL_LEAVE', 'UNPAID_LEAVE'];
const OWN_KINDS = ['CREATE', 'EXTEND'];

export function waitsForPaper(row: api.AbsenceRow): boolean {
  // Вернули справку на исправление — это «Исправление», а не ожидание
  // первой бумаги.
  return row.absence_type.code === 'SICK_LEAVE' && row.stage === 'WAITING_DOCUMENTS';
}

export function paperOnReview(row: api.AbsenceRow): boolean {
  // На стадии HR_REVIEW справка либо принята, либо ждёт кадровика; «не
  // хватает справки» сервер пишет только во втором случае.
  return row.absence_type.code === 'SICK_LEAVE' && row.stage === 'HR_REVIEW'
    && (row.missing_for_approval ?? []).includes('certificate');
}

/** Отпуск во вкладке «Отпуска»: те же типы и виды заявок, что её отбор. */
export function isLeave(row: api.AbsenceRow): boolean {
  return LEAVE_TYPES.includes(row.absence_type.code) && OWN_KINDS.includes(row.kind ?? 'CREATE');
}

/** Подпись отбора по стадии — для метки рядом с полями. */
export function paperFilterTitle(stage: string, certificate: string): string {
  if (certificate === 'PENDING' && !stage) return 'Справка ждёт проверки';
  if (stage === 'WAITING_DOCUMENTS,NEEDS_FIX' && !certificate) return 'Ждут справку';
  return 'Отбор по справке';
}

export type RequestsLink = {
  tab?: string;
  status?: string;
  stage?: string;
  certificate?: string;
  region_id?: string;
  office_id?: string;
};

/** Адрес страницы «Заявки» с отбором. Пустые параметры в адрес не попадают. */
export function requestsHref(filters: RequestsLink): string {
  return '/requests' + api.query(filters);
}
