/**
 * Левая, чёрная половина карточки входа.
 *
 * Нижний ряд из трёх колонок сохранён по макету, но вместо чисел
 * «125+ / 12 / 98%» здесь названия того, что система действительно
 * делает. Числа на экране входа читаются как настоящие показатели
 * организации, а взять их пока неоткуда: человек ещё не вошёл, и
 * никакой организации у страницы нет.
 */

import { DecorChart } from './DecorChart';
import { Wordmark } from './Logo';

const COLUMNS = [
  { title: 'Единый учёт', hint: 'Сотрудники, отделы, должности' },
  { title: 'Контроль доступа', hint: 'Офисы, QR-точки, роли' },
  { title: 'Прозрачная аналитика', hint: 'Посещаемость и отчёты' },
] as const;

export function BrandPanel() {
  return (
    <aside className="brand">
      <div className="brand__head">
        <Wordmark />
        <p className="brand__tagline">Единое управление персоналом</p>
      </div>

      <DecorChart />

      <ul className="brand__columns">
        {COLUMNS.map((column) => (
          <li key={column.title} className="brand__column">
            <span className="brand__column-title">{column.title}</span>
            <span className="brand__column-hint">{column.hint}</span>
          </li>
        ))}
      </ul>
    </aside>
  );
}
