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
 */

const BARS = [26, 34, 52, 64, 58, 72, 50, 88, 62, 76, 92, 112];
const LINE = [
  [12, 176],
  [66, 150],
  [124, 128],
  [166, 132],
  [214, 104],
  [258, 62],
] as const;

export function DecorChart() {
  const path = LINE.map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${x} ${y}`).join(' ');

  return (
    <div className="chart">
      <svg className="chart__canvas" viewBox="0 0 300 200" aria-hidden="true"
           preserveAspectRatio="none">
        <g className="chart__grid">
          {[40, 80, 120, 160].map((y) => (
            <line key={`h${y}`} x1="0" y1={y} x2="300" y2={y} />
          ))}
          {[60, 120, 180, 240].map((x) => (
            <line key={`v${x}`} x1={x} y1="0" x2={x} y2="200" />
          ))}
        </g>

        <g className="chart__bars">
          {BARS.map((height, index) => (
            <rect key={index} x={8 + index * 24} y={196 - height} width="14" height={height}
                  rx="2" opacity={0.18 + index * 0.035} />
          ))}
        </g>

        <path className="chart__line" d={path} />
        {LINE.map(([x, y]) => (
          <circle key={`${x}-${y}`} className="chart__dot" cx={x} cy={y} r="3.4" />
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
