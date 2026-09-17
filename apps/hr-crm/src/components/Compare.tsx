/**
 * Сравнение: два объекта за один период либо один объект за два периода.
 *
 * Разница считается сервером и приходит в процентных ПУНКТАХ — здесь она
 * только показывается. `comparable = false` означает, что у одной из
 * сторон нулевой знаменатель: разница не определена, и рисовать ноль на
 * её месте нельзя.
 *
 * Обе стороны считаются одной методикой и одними правилами исключений.
 * Разный размер офиса и разное число рабочих дней видны в исходных
 * числах рядом с процентом — иначе крупный офис выглядел бы «лучше»
 * просто потому, что он крупный.
 */

import { useState } from 'react';

import * as api from '../api/crm';
import { AppIcon } from './AppIcon';
import { AppSelectField } from './AppSelect';
import {
  UNIT_TITLE, formatCount, formatPercent, formatPoints,
} from '../features/analytics/metrics';
import { longDate, useBlock, type Block } from '../features/dashboard/data';

type Props = {
  from: string;
  to: string;
  prevFrom: string;
  prevTo: string;
  group: 'offices' | 'regions';
  objects: { id: string; name: string }[];
  chosen: string[];
  onPick: (ids: string[]) => void;
};

export function Compare({ from, to, prevFrom, prevTo, group, objects, chosen, onPick }: Props) {
  const [mode, setMode] = useState<'objects' | 'periods'>(
    chosen.length === 2 ? 'objects' : 'periods',
  );

  const left = chosen[0] ?? objects[0]?.id ?? '';
  const right = chosen[1] ?? objects[1]?.id ?? '';
  const kind = group === 'regions' ? 'region' : 'office';

  const enough = mode === 'periods' || (Boolean(left) && Boolean(right) && left !== right);

  const [result] = useBlock(
    (signal) =>
      api.compare(
        mode === 'periods'
          ? {
              kind: 'period',
              date_from: from,
              date_to: to,
              right_first: prevFrom,
              right_last: prevTo,
              ...(chosen[0] ? { left_id: chosen[0] } : {}),
            }
          : { kind, date_from: from, date_to: to, left_id: left, right_id: right },
        signal,
      ),
    `cmp|${mode}|${kind}|${left}|${right}|${from}|${to}|${prevFrom}`,
    enough,
  );

  return (
    <section className="panel">
      <div className="panel__head">
        <div>
          <h2 className="panel__title">Сравнение</h2>
          <p className="panel__sub">
            Обе стороны считаются одной методикой и одними правилами исключений
          </p>
        </div>
        <div className="switch" role="group" aria-label="Что сравнивать">
          {[
            { key: 'objects', title: group === 'regions' ? 'Два региона' : 'Два офиса' },
            { key: 'periods', title: 'Два периода' },
          ].map((item) => (
            <button key={item.key} type="button"
                    className={item.key === mode ? 'switch__on' : ''}
                    onClick={() => setMode(item.key as 'objects' | 'periods')}>
              {item.title}
            </button>
          ))}
        </div>
      </div>

      {mode === 'objects' && (
        <div className="toolbar toolbar--flat">
          <Picker label="Слева" value={left} objects={objects}
                  onChange={(id) => onPick([id, right].filter(Boolean))} />
          <Picker label="Справа" value={right} objects={objects}
                  onChange={(id) => onPick([left, id].filter(Boolean))} />
        </div>
      )}

      {mode === 'periods' && (
        <p className="side-panel__text muted">
          {longDate(from)} — {longDate(to)} против {longDate(prevFrom)} — {longDate(prevTo)}.
          Периоды разной длины сравнимы только долями: абсолютные числа
          зависят от количества рабочих дней.
        </p>
      )}

      {!enough ? (
        <p className="empty">
          Выберите два разных {group === 'regions' ? 'региона' : 'офиса'}.
        </p>
      ) : (
        <Body block={result}>
          {(data) => (
            <>
              <div className="versus">
                <Side report={data.left} />
                <span className="versus__mid" aria-hidden="true">
                  <AppIcon name="arrow" size={18} />
                </span>
                <Side report={data.right} />
              </div>

              <div className="scroller">
                <table className="people">
                  <thead>
                    <tr>
                      <th>Показатель</th>
                      <th>{data.left.scope.name}</th>
                      <th>{data.right.scope.name}</th>
                      <th>Разница</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.differences.map((row) => (
                      <tr key={row.key}>
                        <td className="grid-table__name">
                          {row.title}
                          <span className="who__id">
                            {UNIT_TITLE[row.left.unit] ?? row.left.unit}
                          </span>
                        </td>
                        <td className="num">
                          {formatPercent(row.left.percent)}
                          <span className="who__id">
                            {formatCount(row.left.numerator)} / {formatCount(row.left.denominator)}
                          </span>
                        </td>
                        <td className="num">
                          {formatPercent(row.right.percent)}
                          <span className="who__id">
                            {formatCount(row.right.numerator)} / {formatCount(row.right.denominator)}
                          </span>
                        </td>
                        <td className="num">
                          {row.comparable ? formatPoints(row.points) : (
                            <span className="muted">Не сравнимо</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <p className="panel__foot">
                Разница — в процентных пунктах. «Не сравнимо» значит, что у
                одной из сторон нулевой знаменатель: разницы там нет, а не ноль.
              </p>
            </>
          )}
        </Body>
      )}
    </section>
  );
}

function Side({ report }: { report: api.Report }) {
  return (
    <div className="versus__side">
      <p className="versus__name">{report.scope.name}</p>
      <p className="versus__period">
        {longDate(report.period.first)} — {longDate(report.period.last)}
      </p>
      <p className="versus__note">
        {formatCount(report.totals['expected_working_days'] ?? 0)} сотрудник-дней по графику
        {' · '}
        {report.coverage.employees_with_schedule} из {report.coverage.employees_total} с графиком
      </p>
    </div>
  );
}

function Picker({ label, value, objects, onChange }: {
  label: string; value: string;
  objects: { id: string; name: string }[]; onChange: (id: string) => void;
}) {
  return (
    <AppSelectField className="toolbar-select" label={label} value={value} onChange={onChange}>
        <option value="">{label}</option>
        {objects.map((item) => (
          <option key={item.id} value={item.id}>{item.name}</option>
        ))}
    </AppSelectField>
  );
}

function Body<T>({ block, children }: {
  block: Block<T>; children: (data: T) => React.ReactNode;
}) {
  if (block.state === 'loading') return <p className="empty">Считаем сравнение…</p>;
  if (block.state === 'denied') return <p className="empty">Нет доступа к аналитике.</p>;
  if (block.state === 'error') {
    return (
      <p className="empty empty--bad">
        Не удалось посчитать сравнение. Это ошибка запроса, а не равенство сторон.
      </p>
    );
  }
  return <>{children(block.data)}</>;
}
