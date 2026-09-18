/**
 * Кому отправить опрос.
 *
 * Круг задаётся видом и списком: сотрудники, отдел, офис или все
 * действующие. Раскрывается он в строки получателей на сервере и в
 * момент отправки, а не здесь и не сейчас — за неделю до запланированной
 * даты состав отдела меняется, и спрашивать надо тех, кто работает
 * тогда, а не тех, кого HR видел при создании.
 *
 * Поэтому здесь не показывается число «получит N человек»: оно было бы
 * обещанием, которое сервер не обязан сдержать.
 */

import { useMemo } from 'react';

import * as api from '../../api/crm';
import { AppMultiSelect, AppSelectField } from '../AppSelect';
import { Field } from '../admin/Parts';
import { useBlock } from '../../features/dashboard/data';

const KINDS: Array<[api.SurveyAudienceKind, string]> = [
  ['ALL', 'Все действующие сотрудники'],
  ['OFFICE', 'Сотрудники офиса'],
  ['DEPARTMENT', 'Сотрудники отдела'],
  ['EMPLOYEES', 'Выбранные сотрудники'],
];

export function AudiencePicker({ kind, ids, error, onChange }: {
  kind: api.SurveyAudienceKind;
  ids: string[];
  error?: string | undefined;
  onChange: (kind: api.SurveyAudienceKind, ids: string[]) => void;
}) {
  const [offices] = useBlock(
    (signal) => api.officesPage({ status: 'ACTIVE', limit: '200' }, signal),
    'surveys-offices',
    kind === 'OFFICE',
  );
  const [departments] = useBlock(
    (signal) => api.departmentsPage({ status: 'ACTIVE' }, signal),
    'surveys-departments',
    kind === 'DEPARTMENT',
  );
  const [people] = useBlock(
    (signal) => api.employees({ status: 'ACTIVE', limit: '200' }, signal),
    'surveys-people',
    kind === 'EMPLOYEES',
  );

  const options = useMemo(() => {
    if (kind === 'OFFICE') {
      return (offices.state === 'ready' ? offices.data.items : []).map((one) => ({
        value: one.id,
        label: one.name,
      }));
    }
    if (kind === 'DEPARTMENT') {
      return (departments.state === 'ready' ? departments.data.items : []).map(
        (one) => ({ value: one.id, label: `${one.name} — ${one.office_name}` }),
      );
    }
    if (kind === 'EMPLOYEES') {
      return (people.state === 'ready' ? people.data.items : []).map((one) => ({
        value: one.id,
        label: one.full_name,
      }));
    }
    return [];
  }, [kind, offices, departments, people]);

  return (
    <>
      <Field label="Кому отправить">
        <AppSelectField label="Кому отправить" value={kind}
                        onChange={(value) =>
                          onChange(value as api.SurveyAudienceKind, [])}>
          {KINDS.map(([key, title]) => (
            <option key={key} value={key}>{title}</option>
          ))}
        </AppSelectField>
      </Field>

      {kind !== 'ALL' && (
        <Field
          label={
            kind === 'OFFICE' ? 'Офисы'
              : kind === 'DEPARTMENT' ? 'Отделы' : 'Сотрудники'
          }
          error={error}
          hint="Список получателей собирается при отправке: пришедшие до неё тоже получат опрос, уволенные — нет"
        >
          <AppMultiSelect
            label={kind === 'OFFICE' ? 'Офисы'
              : kind === 'DEPARTMENT' ? 'Отделы' : 'Сотрудники'}
            value={ids}
            options={options}
            empty="Не выбрано"
            onChange={(next) => onChange(kind, next)}
          />
        </Field>
      )}

      {kind === 'ALL' && (
        <p className="adm-note">
          Опрос получат все сотрудники со статусом «работает» и «испытательный
          срок». Уволенным и без подключённого Telegram он не уйдёт.
        </p>
      )}
    </>
  );
}
