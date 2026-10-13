/**
 * Подменённая очередь заявок `/requests` для тестов CRM.
 *
 * Отбирает строки по тем же параметрам, что и настоящий сервер: `kind`
 * (включая `fixes`), `type`, `request_kind`, `status`, `stage`,
 * `certificate`, `office_id`. У исправления отметки стадия — как у
 * отпуска: открытое — `PENDING`, решённое — своим статусом. Совпадение
 * этих правил с сервером проверяют backend-тесты
 * `django_tests/test_home_attention.py` и `test_requests_groups.py`.
 */

export type Row = {
  id: string;
  name: string;
  kind: 'absence' | 'correction';
  type?: string;
  requestKind?: string;
  status: string;
  stage?: string;
  /** Судьба справки — как её отдал бы `certificate_state`. */
  paper?: 'PENDING' | 'VERIFIED' | 'REJECTED' | null;
  missing?: string[];
  office: string;
};

export const ROWS: Row[] = [
  { id: 'wait', name: 'Ждёт Справку', kind: 'absence', type: 'SICK_LEAVE', requestKind: 'CREATE',
    status: 'SUBMITTED', stage: 'WAITING_DOCUMENTS', paper: null, missing: ['certificate', 'application', 'period'], office: 'o-1' },
  { id: 'fix', name: 'Вернули Справку', kind: 'absence', type: 'SICK_LEAVE', requestKind: 'CREATE',
    status: 'IN_REVIEW', stage: 'NEEDS_FIX', paper: 'REJECTED', missing: ['certificate', 'application', 'period'], office: 'o-1' },
  { id: 'review', name: 'Справка Напроверке', kind: 'absence', type: 'SICK_LEAVE', requestKind: 'CREATE',
    status: 'IN_REVIEW', stage: 'HR_REVIEW', paper: 'PENDING', missing: ['certificate', 'application'], office: 'o-1' },
  // Справку уже приняли — ждут заявление. В «справка ждёт проверки» не входит.
  { id: 'verified', name: 'Справка Принята', kind: 'absence', type: 'SICK_LEAVE', requestKind: 'CREATE',
    status: 'IN_REVIEW', stage: 'HR_REVIEW', paper: 'VERIFIED', missing: ['application'], office: 'o-1' },
  { id: 'sick-done', name: 'Больничный Подтверждён', kind: 'absence', type: 'SICK_LEAVE', requestKind: 'CREATE',
    status: 'APPROVED', stage: 'APPROVED', paper: 'VERIFIED', missing: [], office: 'o-1' },
  { id: 'leave', name: 'Отпуск Ждёт', kind: 'absence', type: 'ANNUAL_LEAVE', requestKind: 'CREATE',
    status: 'SUBMITTED', stage: 'PENDING', office: 'o-1' },
  { id: 'leave-done', name: 'Отпуск Решён', kind: 'absence', type: 'ANNUAL_LEAVE', requestKind: 'CREATE',
    status: 'REJECTED', stage: 'REJECTED', office: 'o-1' },
  { id: 'leave-far', name: 'Отпуск Бухара', kind: 'absence', type: 'ANNUAL_LEAVE', requestKind: 'CREATE',
    status: 'SUBMITTED', stage: 'PENDING', office: 'o-2' },
  { id: 'corr', name: 'Исправление Ждёт', kind: 'correction', status: 'SUBMITTED', office: 'o-1' },
  { id: 'corr-done', name: 'Исправление Рассмотрено', kind: 'correction', status: 'APPROVED', office: 'o-1' },
];

/** Стадия строки; у исправления отметки — как у отпуска. */
function stageOf(row: Row): string {
  if (row.kind === 'absence') return row.stage!;
  return row.status === 'SUBMITTED' || row.status === 'IN_REVIEW' ? 'PENDING' : row.status;
}

const list = (raw: string | null) => (raw ? raw.split(',').filter(Boolean) : null);

/** Отбор как у `/requests`: незнакомый статус даёт пустой набор, как в бою. */
export function serveQueue(url: URL, rows = ROWS) {
  const p = url.searchParams;
  const kind = p.get('kind');
  const status = list(p.get('status'));
  const type = list(p.get('type'));
  const requestKind = list(p.get('request_kind'));
  const stage = list(p.get('stage'));
  const certificate = list(p.get('certificate'));
  const office = p.get('office_id');
  const picked = rows.filter((row) => {
    if (kind === 'fixes') {
      // «Исправления»: запросы на исправление отметки и больничные,
      // которым вернули справку.
      if (row.kind === 'absence' && row.stage !== 'NEEDS_FIX') return false;
    } else if (kind && row.kind !== kind) return false;
    if (certificate && row.kind !== 'absence') return false;
    if (status && !status.includes(row.status)) return false;
    if (stage && !stage.includes(stageOf(row))) return false;
    if (row.kind === 'absence') {
      if (type && !type.includes(row.type!)) return false;
      if (requestKind && !requestKind.includes(row.requestKind!)) return false;
      if (certificate && !certificate.includes(row.paper ?? '')) return false;
    }
    if (office && row.office !== office) return false;
    return true;
  });
  return { items: picked.map(item), next_cursor: null, has_more: false };
}

export function item(row: Row) {
  const person = { id: `e-${row.id}`, full_name: row.name, employee_number: null };
  if (row.kind === 'correction') {
    return {
      kind: 'correction', id: row.id, created_at: '2026-09-20T05:00:00Z', place: null,
      correction: { id: row.id, status: row.status, employee: person, submitted_at: '2026-09-20T05:00:00Z' },
    };
  }
  return {
    kind: 'absence', id: row.id, created_at: '2026-09-20T05:00:00Z', place: null,
    absence: {
      id: row.id, employee: person, kind: row.requestKind,
      absence_type: { code: row.type, name: row.type === 'SICK_LEAVE' ? 'Больничный' : 'Ежегодный отпуск' },
      status: row.status, stage: row.stage, missing_for_approval: row.missing ?? [],
      first_day: '2026-09-22', last_day: '2026-09-27', submitted_at: '2026-09-20T05:00:00Z',
      requires_document: row.type === 'SICK_LEAVE', comment: null, review_comment: null,
      documents: row.paper ? [{
        id: `d-${row.id}`, document_type: 'SICK_NOTE', verification_status: row.paper,
        file: { id: `f-${row.id}`, name: 'spravka.pdf', mime_type: 'application/pdf', size_bytes: 1, uploaded_at: '2026-09-20T05:00:00Z', scan_status: 'CLEAN' },
      }] : [],
    },
  };
}

