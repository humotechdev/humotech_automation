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

/**
 * Статус из адреса — в статус сервера.
 *
 * `status=open` — короткое имя открытых заявок, его ставят ссылки
 * главной и карточки сотрудника. Сервер такого статуса не знает: раньше
 * слово уходило ему как есть, и список по ссылке приходил пустым, а
 * ручной выбор вкладки, сбрасывавший статус, «чинил» его.
 */
export function queueStatus(raw: string): string {
  return raw === 'open' ? OPEN_STATUSES : raw;
}

/** Больничные, где ждут бумагу от сотрудника: справки нет или её вернули. */
export const WAITING_PAPER = { stage: 'WAITING_DOCUMENTS,NEEDS_FIX' } as const;
/** Справка принесена и ждёт проверки кадровиком — принятая сюда не входит. */
export const PAPER_ON_REVIEW = { stage: 'HR_REVIEW', certificate: 'PENDING' } as const;

const LEAVE_TYPES = ['ANNUAL_LEAVE', 'UNPAID_LEAVE'];
const OWN_KINDS = ['CREATE', 'EXTEND'];

export function waitsForPaper(row: api.AbsenceRow): boolean {
  return row.absence_type.code === 'SICK_LEAVE'
    && (row.stage === 'WAITING_DOCUMENTS' || row.stage === 'NEEDS_FIX');
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
  if (stage === WAITING_PAPER.stage && !certificate) return 'Ждут справку';
  if (stage === PAPER_ON_REVIEW.stage && certificate === PAPER_ON_REVIEW.certificate) return 'Справка ждёт проверки';
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
