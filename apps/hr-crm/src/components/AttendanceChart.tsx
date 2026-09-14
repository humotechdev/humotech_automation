/**
 * Явка по дням: доля отметившихся хотя бы раз.
 *
 * Рисуется по настоящему ряду `/analytics`. Два правила, без которых
 * график врёт:
 *
 * 1. День без знаменателя (никто не должен был работать) — это ПРОПУСК,
 *    а не ноль процентов. Линия в таком месте разрывается.
 * 2. Предыдущий период показывается только когда он сопоставим — столько
 *    же дней и с данными. Иначе пунктира просто нет.
 *
 * Пропорции держит `viewBox` вместе с фиксированным отношением сторон у
 * контейнера: `preserveAspectRatio` не отключён, поэтому точки остаются
 * круглыми на любой ширине, а высота не растёт вслед за окном.
 */

import { useEffect, useId, useRef, useState } from 'react';

import type { DayPoint } from '../api/crm';
import { AppIcon } from './AppIcon';
import {
  formatPercent, longDate, percent, shortDate, today as todayIso,
} from '../features/dashboard/data';

/*
 * Система координат графика — настоящие пиксели поля, а не условные
 * единицы `viewBox`.
 *
 * Раньше здесь стоял `viewBox="0 0 1000 262"`, и браузер сжимал его до
 * ширины панели. Вместе с картинкой сжимались подписи: 13 пикселей в
 * системе координат превращались на экране в восемь, и ось читалась
 * хуже всего остального на странице. Толщина линии спасалась
 * `non-scaling-stroke`, у текста такого свойства нет.
 *
 * Поэтому поле измеряется, и один пиксель графика равен одному пикселю
 * экрана. Запасные числа нужны там, где измерить нечего — в тестовой
 * среде без вёрстки.
 */
const FALLBACK = { w: 1000, h: 262 };
const PAD = { top: 14, right: 14, bottom: 34, left: 46 };

/** Уровни сетки. Пять, а не три: между 50 и 100 иначе нечем мерить. */
const LEVELS = [0, 25, 50, 75, 100];

export type Point = { day: string; attended: number; expected: number; value: number | null };

export function toPoints(series: DayPoint[]): Point[] {
  return series.map((row) => ({
    day: row.day,
    attended: row.attended,
    expected: row.expected,
    value: percent(row.attended, row.expected),
  }));
}

type Props = {
  points: Point[];
  previous: Point[];
  /** Сколько дней показывать: подписи оси прореживаются по их числу. */
  label: string;
};

export function AttendanceChart({ points, previous, label }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const field = useRef<HTMLDivElement>(null);
  const fillId = useId();
  const [size, setSize] = useState(FALLBACK);

  useEffect(() => {
    const node = field.current;
    if (!node) return;
    const measure = () => {
      const box = node.getBoundingClientRect();
      if (box.width > 0 && box.height > 0) {
        setSize({ w: Math.round(box.width), h: Math.round(box.height) });
      }
    };
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const watch = new ResizeObserver(measure);
    watch.observe(node);
    return () => watch.disconnect();
  }, []);

  const W = size.w;
  const H = size.h;
  const measured = points.filter((p) => p.value !== null);

  if (measured.length === 0) {
    return (
      <p className="empty">
        За выбранный период нечего показать: ни одного дня, в который кто-то
        должен был выйти на работу.
      </p>
    );
  }

  const x = (index: number) =>
    PAD.left +
    (index * (W - PAD.left - PAD.right)) / Math.max(points.length - 1, 1);
  const y = (value: number) =>
    PAD.top + ((100 - value) * (H - PAD.top - PAD.bottom)) / 100;

  // Две недели подписываются каждым днём; на месяце подписи ставятся
  // через несколько, иначе даты налезают друг на друга. Прореживание по
  // числу точек, а не по ширине: подписи должны сходиться с датами.
  const step = points.length <= 16 ? 1 : Math.ceil(points.length / 8);
  const main = curve(points, x, y);
  const last = measured[measured.length - 1];
  // Сравнение с прошлым периодом показывается по тому же условию, по
  // которому рисуется пунктир: цифра и линия не должны расходиться в
  // том, есть ли с чем сравнивать.
  const comparable = previous.length === points.length;
  const lastIndex = points.indexOf(last as Point);
  const before = comparable && lastIndex >= 0 ? previous[lastIndex] : undefined;
  const shift =
    last?.value != null && before?.value != null ? last.value - before.value : null;

  return (
    <div className="chart-box">
      <div className="chart-box__plot" ref={field}>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="plot"
          role="img"
          aria-label={label}
          preserveAspectRatio="none"
        >
          {/* Поле графика чуть светлее панели: без него сетка висит
              в воздухе, а линия оказывается прямо на фотографии. */}
          <rect className="plot__field" x={PAD.left} y={PAD.top}
                width={W - PAD.left - PAD.right} height={y(0) - PAD.top} rx={6} />
          {LEVELS.map((value) => (
            <g key={value}>
              <line className="plot__grid" x1={PAD.left} y1={y(value)} x2={W - PAD.right}
                    y2={y(value)} />
              <text className="plot__tick" x={PAD.left - 12} y={y(value) + 5}
                    textAnchor="end">{value}%</text>
            </g>
          ))}
          {/* Ось слева: без неё сетка висит в воздухе и поле графика
              не читается как поле. */}
          <line className="plot__axis" x1={PAD.left} y1={PAD.top}
                x2={PAD.left} y2={y(0)} />

          {/* Заливка под линией — не украшение: она отделяет «сколько
              есть» от пустого поля выше и делает читаемым, где проходит
              линия на светлом фоне. */}
          <defs>
            <linearGradient id={`${fillId}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--chart-main)" stopOpacity="0.22" />
              <stop offset="100%" stopColor="var(--chart-main)" stopOpacity="0" />
            </linearGradient>
          </defs>
          {main.area && <path d={main.area} fill={`url(#${fillId})`} stroke="none" />}
          {previous.length === points.length && (
            <path className="plot__line plot__line--prev" d={curve(previous, x, y).line} />
          )}
          <path className="plot__line" d={main.line} />

          {/* Направляющая под курсором: без неё в месяце трудно понять,
              к какому именно дню относится подсказка. */}
          {hover !== null && points[hover]?.value !== null && (
            <line className="plot__guide" x1={x(hover)} y1={PAD.top}
                  x2={x(hover)} y2={y(0)} />
          )}

          {points.map((point, index) =>
            point.value === null ? null : (
              <circle
                key={point.day}
                className={hover === index ? 'plot__dot plot__dot--on' : 'plot__dot'}
                cx={x(index)}
                cy={y(point.value)}
                r={hover === index ? 4.5 : 5}
              />
            ),
          )}

          {/* Кольцо вокруг точки под курсором. Отдельным кругом, а не
              обводкой: обводка растёт внутрь и съедает саму точку. */}
          {hover !== null && points[hover]?.value != null && (
            <circle className="plot__ring" cx={x(hover)}
                    cy={y(points[hover]?.value as number)} r={8.5} />
          )}

          {points.map((point, index) => (
            <g key={`t-${point.day}`}>
              {index % step === 0 && (
                <text className="plot__tick" x={x(index)} y={H - 8} textAnchor="middle">
                  {shortDate(point.day)}
                </text>
              )}
              <rect
                x={x(index) - (W - PAD.left - PAD.right) / (2 * Math.max(points.length - 1, 1))}
                y={PAD.top}
                width={(W - PAD.left - PAD.right) / Math.max(points.length - 1, 1)}
                height={H - PAD.top - PAD.bottom}
                fill="transparent"
                onMouseEnter={() => setHover(index)}
                onMouseLeave={() => setHover(null)}
              />
            </g>
          ))}
        </svg>

        {hover !== null && points[hover] && (
          <Tip
            point={points[hover] as Point}
            {...(comparable && previous[hover]
              ? { before: previous[hover] as Point }
              : {})}
            at={x(hover) / W}
          />
        )}
      </div>

      <div className="chart-box__total">
        <p className="chart-box__total-label">
          {last?.day === todayIso() ? 'Сегодня' : 'Последний день'}
        </p>
        <p className="chart-box__total-main">
          {last?.attended} из {last?.expected}
        </p>
        <p className="chart-box__total-percent">{formatPercent(last?.value ?? null)}</p>
        {/* Разница с тем же днём прошлого периода. Показывается только
            когда есть с чем сравнивать: выдуманный ноль здесь означал бы
            «ничего не изменилось», а это не то же самое, что «не с чем
            сравнить». */}
        {shift !== null && <Shift value={shift} />}
      </div>
    </div>
  );
}

/**
 * Линия через дни с данными.
 *
 * День без знаменателя — выходной: работать никто не должен был, и
 * ноль процентов там был бы неправдой. Точки в такой день нет, но линия
 * идёт дальше — от последнего рабочего дня к следующему. Разрыв на
 * каждые выходные превращал бы месячный график в набор огрызков, а
 * подстановка нуля — во впадину, которой не было.
 *
 * Календарь при этом не сжимается: `x` считается по порядковому номеру
 * дня, поэтому выходные занимают на оси своё место.
 *
 * Сглаживание — монотонное кубическое (Фрич — Карлсон). Обычный сплайн
 * ради плавности выгибает линию за крайние значения: между 94% и 96% он
 * нарисовал бы горб выше ста процентов, которого в данных нет. Здесь
 * наклоны подрезаются так, что кривая не выходит за соседние точки —
 * плавность есть, выдуманных пиков нет.
 */
function curve(
  points: Point[],
  x: (i: number) => number,
  y: (v: number) => number,
): { line: string; area: string } {
  const seen = points
    .map((point, index) => ({ point, index }))
    .filter((row) => row.point.value !== null)
    .map(({ point, index }) => ({ x: x(index), y: y(point.value as number) }));

  if (seen.length === 0) return { line: '', area: '' };
  if (seen.length === 1) {
    const only = seen[0] as { x: number; y: number };
    return { line: `M${only.x} ${only.y}`, area: '' };
  }

  // Секущие между соседями и наклоны в точках.
  const slopes: number[] = [];
  const tangents: number[] = [];
  for (let i = 0; i < seen.length - 1; i += 1) {
    const a = seen[i] as { x: number; y: number };
    const b = seen[i + 1] as { x: number; y: number };
    slopes.push((b.y - a.y) / (b.x - a.x));
  }
  tangents.push(slopes[0] as number);
  for (let i = 1; i < seen.length - 1; i += 1) {
    const before = slopes[i - 1] as number;
    const after = slopes[i] as number;
    tangents.push(before * after <= 0 ? 0 : (before + after) / 2);
  }
  tangents.push(slopes[slopes.length - 1] as number);

  // Подрезка: без неё кривая вылезает за крайние значения отрезка.
  for (let i = 0; i < slopes.length; i += 1) {
    const step = slopes[i] as number;
    if (step === 0) {
      tangents[i] = 0;
      tangents[i + 1] = 0;
      continue;
    }
    const a = (tangents[i] as number) / step;
    const b = (tangents[i + 1] as number) / step;
    const size = a * a + b * b;
    if (size > 9) {
      const k = 3 / Math.sqrt(size);
      tangents[i] = k * a * step;
      tangents[i + 1] = k * b * step;
    }
  }

  const first = seen[0] as { x: number; y: number };
  let path = `M${first.x.toFixed(1)} ${first.y.toFixed(1)}`;
  for (let i = 0; i < seen.length - 1; i += 1) {
    const a = seen[i] as { x: number; y: number };
    const b = seen[i + 1] as { x: number; y: number };
    const h = (b.x - a.x) / 3;
    const c1y = a.y + (tangents[i] as number) * h;
    const c2y = b.y - (tangents[i + 1] as number) * h;
    path += ` C${(a.x + h).toFixed(1)} ${c1y.toFixed(1)},`
      + ` ${(b.x - h).toFixed(1)} ${c2y.toFixed(1)},`
      + ` ${b.x.toFixed(1)} ${b.y.toFixed(1)}`;
  }

  const last = seen[seen.length - 1] as { x: number; y: number };
  const base = y(0);
  const area = `${path} L${last.x.toFixed(1)} ${base.toFixed(1)}`
    + ` L${first.x.toFixed(1)} ${base.toFixed(1)} Z`;
  return { line: path, area };
}

/**
 * Разница с тем же днём прошлого периода.
 *
 * Основание сравнения названо словами в подсказке и для диктора. Без
 * этого цифра меняется при переключении периода (у недели и у месяца
 * «тот же день» — разные дни календаря) и читается как сбой. Стрелка —
 * не украшение к цвету: зелёное от янтарного различают не все, «вверх»
 * от «вниз» — все, а диктору достаётся только текст.
 */
function Shift({ value }: { value: number }) {
  // Меньше десятой процента — это не рост и не падение. Стрелка вверх
  // при «0,0%» утверждала бы то, чего в данных нет.
  const flat = Math.abs(value) < 0.05;
  const kind = flat ? 'flat' : value > 0 ? 'up' : 'down';
  const sign = { up: 'trend-up', down: 'trend-down', flat: 'arrow' } as const;
  const said = { up: 'Рост на', down: 'Снижение на', flat: 'Без изменений —' }[kind];
  return (
    <p
      className={`chart-box__shift chart-box__shift--${kind}`}
      title={`${said} ${formatPercent(Math.abs(value))} к тому же дню прошлого периода`}
    >
      <AppIcon name={sign[kind]} size={16} />
      <span aria-hidden="true">{formatPercent(Math.abs(value))}</span>
      <span className="visually-hidden">
        {said} {formatPercent(Math.abs(value))} к тому же дню прошлого периода
      </span>
    </p>
  );
}

function Tip({ point, before, at }: { point: Point; before?: Point; at: number }) {
  /*
   * Карточка уходит в ту половину поля, где линии нет.
   *
   * Явка почти всегда выше девяноста, и карточка, привязанная к верху,
   * закрывала бы собой ту самую точку, ради которой к ней подвели
   * курсор. Поэтому при высоком значении карточка опускается вниз, при
   * низком остаётся вверху; направляющая в обоих случаях показывает,
   * к какому дню она относится.
   */
  const under = (point.value ?? 0) >= 55;
  return (
    <div
      className={under ? 'tip tip--under' : 'tip'}
      /*
       * Сдвиг вдоль поля задан в процентах от собственной ширины
       * карточки: у левого края она почти не съезжает, у правого
       * съезжает почти целиком — и не вылезает за панель ни с одной
       * стороны, без замеров и пересчёта на каждое движение мыши.
       */
      style={{ left: `${at * 100}%`, transform: `translateX(-${at * 100}%)` }}
    >
      <span className="tip__day">{longDate(point.day)}</span>
      {point.expected === 0 ? (
        /* Выходной не «ноль процентов», а день, в который никто не
           должен был выйти. Подпись говорит это словами, иначе пустая
           точка выглядела бы сбоем данных. */
        <span className="tip__off">Выходной · по графику нет сотрудников</span>
      ) : (
        <>
          <span className="tip__row">
            <i className="tip__mark" />
            Явка
            <b>{point.attended} / {point.expected}</b>
          </span>
          <span className="tip__big">{formatPercent(point.value)}</span>
          {before?.value != null && (
            <span className="tip__row tip__row--prev">
              <i className="tip__mark tip__mark--dash" />
              Предыдущий период
              <b>{formatPercent(before.value)}</b>
            </span>
          )}
        </>
      )}
    </div>
  );
}
