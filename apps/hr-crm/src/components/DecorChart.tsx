/**
 * Декоративный график левой панели.
 *
 * Он именно декоративный: ни одной цифры отсюда нельзя прочитать как
 * показатель работы компании. Придуманные «98% вовлечённости» на экране
 * входа выглядят как настоящие данные, а это ложь ещё до того, как
 * человек вошёл.
 *
 * Поэтому: без подписей значений, без анимации и `aria-hidden` — для
 * читалки экрана здесь ничего нет.
 *
 * Пропорции держит сам рисунок. `preserveAspectRatio` не отключён, и
 * система координат `viewBox` совпадает по отношению сторон с
 * контейнером: иначе на высокой панели круглые точки превращаются в
 * овалы, а столбцы вытягиваются вверх — рисунок перестаёт быть тем,
 * что нарисовано.
 */

/** Отношение сторон рисунка. То же число задано контейнеру в CSS. */
export const RATIO = 340 / 190;

const BASELINE = 186;
const BARS = [26, 34, 52, 64, 58, 72, 50, 88, 62, 76, 92, 112];
// Правый верхний угол занимает подпись, поэтому линия туда не заходит:
// иначе точка ложится прямо на текст.
const LINE = [
  [14, 168],
  [72, 146],
  [136, 126],
  [186, 130],
  [242, 110],
  [300, 86],
] as const;

export function DecorChart() {
  const path = LINE.map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${x} ${y}`).join(' ');

  return (
    <div className="chart">
      <svg className="chart__canvas" viewBox="0 0 340 190" aria-hidden="true">
        <g className="chart__grid">
          {[38, 76, 114, 152].map((y) => (
            <line key={`h${y}`} x1="0" y1={y} x2="340" y2={y} />
          ))}
          {[68, 136, 204, 272].map((x) => (
            <line key={`v${x}`} x1={x} y1="0" x2={x} y2="190" />
          ))}
        </g>

        <g className="chart__bars">
          {BARS.map((height, index) => (
            <rect key={index} x={8 + index * 27} y={BASELINE - height} width="16" height={height}
                  rx="2" opacity={0.18 + index * 0.035} />
          ))}
        </g>

        <path className="chart__line" d={path} />
        {LINE.map(([x, y]) => (
          <circle key={`${x}-${y}`} className="chart__dot" cx={x} cy={y} r="4" />
        ))}
      </svg>

      <div className="chart__note" aria-hidden="true">
        <span>Команда</span>
        <span>Развитие</span>
        <span>Результат</span>
      </div>
    </div>
  );
}
