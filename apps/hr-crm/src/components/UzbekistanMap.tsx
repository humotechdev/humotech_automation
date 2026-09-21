/**
 * Карта сети офисов на настоящих границах Узбекистана.
 *
 * Границы — из `public/geo/uzbekistan-adm1.geojson` (geoBoundaries, ADM1:
 * 12 областей, Каракалпакстан и город Ташкент). Файл отдаёт само
 * приложение: внешних тайлов и запросов к картографическим сервисам нет.
 *
 * Стиль — чистая справочная карта: страна одним спокойным цветом, области
 * тонкими линиями, подписи областей светлыми заглавными, офисы крупными
 * маркерами.
 *
 * Проекция — Меркатор, вписанный в фиксированную область `fitExtent`.
 * SVG масштабируется целиком через `viewBox`, поэтому пропорции страны
 * сохраняются при любой ширине.
 *
 * Быстродействие. Геометрия подробная (тысячи точек), поэтому всё тяжёлое
 * — контуры, расстановка подписей, попадание подписи в область — считается
 * один раз и кешируется. Наведение не меняет состояние React: подсветка
 * идёт через CSS `:hover`, подсказка двигается напрямую через `ref`. Иначе
 * каждое движение мыши перерисовывало всю карту, и она тормозила.
 *
 * Офисы стоят только по своим координатам из backend. Офис без координат
 * на карту не попадает.
 */

import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { geoArea, geoContains, geoMercator, geoPath } from 'd3-geo';
import type { Feature, FeatureCollection, MultiPolygon, Polygon } from 'geojson';

import { AppIcon } from './AppIcon';

export type AreaProps = { shapeISO: string; shapeName: string };
export type Area = Feature<Polygon | MultiPolygon, AreaProps>;

export type MapOffice = {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  /** Главный офис: маркер крупнее. */
  head?: boolean;
};

/** Русские названия административных единиц по коду ISO 3166-2. */
export const AREA_NAMES: Record<string, string> = {
  'UZ-AN': 'Андижанская область',
  'UZ-BU': 'Бухарская область',
  'UZ-FA': 'Ферганская область',
  'UZ-JI': 'Джизакская область',
  'UZ-NG': 'Наманганская область',
  'UZ-NW': 'Навоийская область',
  'UZ-QA': 'Кашкадарьинская область',
  'UZ-QR': 'Республика Каракалпакстан',
  'UZ-SA': 'Самаркандская область',
  'UZ-SI': 'Сырдарьинская область',
  'UZ-SU': 'Сурхандарьинская область',
  'UZ-TK': 'город Ташкент',
  'UZ-TO': 'Ташкентская область',
  'UZ-XO': 'Хорезмская область',
};

/**
 * Подписи областей в две строки, как на справочной карте. Город Ташкент
 * не подписан: он меньше маркера офиса, который стоит на нём же.
 */
const AREA_LABELS: Record<string, [string, string]> = {
  'UZ-AN': ['АНДИЖАНСКАЯ', 'ОБЛАСТЬ'],
  'UZ-BU': ['БУХАРСКАЯ', 'ОБЛАСТЬ'],
  'UZ-FA': ['ФЕРГАНСКАЯ', 'ОБЛАСТЬ'],
  'UZ-JI': ['ДЖИЗАКСКАЯ', 'ОБЛАСТЬ'],
  'UZ-NG': ['НАМАНГАНСКАЯ', 'ОБЛАСТЬ'],
  'UZ-NW': ['НАВОИЙСКАЯ', 'ОБЛАСТЬ'],
  'UZ-QA': ['КАШКАДАРЬИНСКАЯ', 'ОБЛАСТЬ'],
  'UZ-QR': ['РЕСПУБЛИКА', 'КАРАКАЛПАКСТАН'],
  'UZ-SA': ['САМАРКАНДСКАЯ', 'ОБЛАСТЬ'],
  'UZ-SI': ['СЫРДАРЬИНСКАЯ', 'ОБЛАСТЬ'],
  'UZ-SU': ['СУРХАНДАРЬИНСКАЯ', 'ОБЛАСТЬ'],
  'UZ-TO': ['ТАШКЕНТСКАЯ', 'ОБЛАСТЬ'],
  'UZ-XO': ['ХОРЕЗМСКАЯ', 'ОБЛАСТЬ'],
};

/** Подписи соседних стран: только ориентир, стоят за границей Узбекистана. */
const NEIGHBOURS: { name: string; at: [number, number] }[] = [
  { name: 'КАЗАХСТАН', at: [65.4, 44.0] },
  { name: 'КЫРГЫЗСТАН', at: [72.9, 41.9] },
  { name: 'ТАДЖИКИСТАН', at: [70.6, 38.6] },
  { name: 'ТУРКМЕНИСТАН', at: [60.6, 39.1] },
  { name: 'АФГАНИСТАН', at: [67.0, 36.7] },
];

const WIDTH = 960;
const HEIGHT = 560;
const PAD = 36;

let cache: Promise<Area[]> | null = null;

/** Границы грузятся один раз на всё приложение. */
export function loadAreas(): Promise<Area[]> {
  cache ??= fetch('/geo/uzbekistan-adm1.geojson')
    .then((answer) => {
      if (!answer.ok) throw new Error('Файл границ не найден');
      return answer.json() as Promise<FeatureCollection<Polygon | MultiPolygon, AreaProps>>;
    })
    .then((collection) => collection.features.map(rewind));
  cache.catch(() => { cache = null; });
  return cache;
}

/**
 * Порядок обхода колец.
 *
 * GeoJSON (RFC 7946) обходит внешнее кольцо против часовой стрелки, а
 * d3 на сфере считает внутренностью то, что слева по ходу. Без разворота
 * область с «неправильным» обходом означала бы весь земной шар без неё.
 */
function rewind(feature: Area): Area {
  if (geoArea(feature) <= 2 * Math.PI) return feature;
  const geometry = feature.geometry;
  const reversed =
    geometry.type === 'Polygon'
      ? { ...geometry, coordinates: geometry.coordinates.map((ring) => [...ring].reverse()) }
      : { ...geometry, coordinates: geometry.coordinates.map((polygon) => polygon.map((ring) => [...ring].reverse())) };
  return { ...feature, geometry: reversed };
}

/** В какую административную единицу попадает точка. `null` — вне страны. */
export function areaOf(areas: Area[], longitude: number, latitude: number): string | null {
  // Город Ташкент лежит внутри Ташкентской области: проверяется первым.
  const ordered = [...areas].sort((a, b) =>
    (a.properties.shapeISO === 'UZ-TK' ? -1 : 0) - (b.properties.shapeISO === 'UZ-TK' ? -1 : 0));
  const hit = ordered.find((area) => geoContains(area, [longitude, latitude]));
  return hit?.properties.shapeISO ?? null;
}

type Box = [number, number, number, number];

//: Насколько можно приблизить карту. Дальше третьего шага контуры
//: областей перестают помещаться в рамку, а мельче единицы смотреть
//: нечего: карта и так занимает всю ширину блока.
/** Город Ташкент: на карте — точка, а не область. */
const CITY = 'UZ-TK';

/** Насколько пальцы должны разъехаться, чтобы считать это щипком. */
const PINCH_START_PX = 10;

const MIN_ZOOM = 1;
const MAX_ZOOM = 3;

const hits = (a: Box, b: Box) => a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];

export const UzbekistanMap = memo(function UzbekistanMap({
  areas, area, onPickArea,
}: {
  areas: Area[];
  /** Выбранная область, код ISO. */
  area: string;
  onPickArea: (iso: string | null) => void;
}) {
  const [zoom, setZoom] = useState(1);
  /**
   * Живое состояние жеста.
   *
   * Здесь, а не в состоянии React: палец двигается шестьдесят раз в
   * секунду, и перерисовывать на каждое движение карту со всеми
   * контурами областей — верный способ получить рывки. Во время жеста
   * преобразование пишется прямо в узел SVG, а React узнаёт итог один
   * раз, когда палец отпустили.
   */
  const view = useRef({ zoom: 1, x: 0, y: 0 });
  const gesture = useRef<
    | { kind: 'drag'; x: number; y: number; from: [number, number] }
    | { kind: 'pinch'; span: number; zoom: number; pinching: boolean }
    | null
  >(null);
  const canvas = useRef<SVGGElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const tip = useRef<HTMLSpanElement>(null);

  const projection = useMemo(() => {
    const collection: FeatureCollection<Polygon | MultiPolygon, AreaProps> = {
      type: 'FeatureCollection',
      features: areas,
    };
    return geoMercator().fitExtent([[PAD, PAD], [WIDTH - PAD, HEIGHT - PAD]], collection);
  }, [areas]);

  // Контуры областей — строкой один раз на данные, а не на каждый кадр.
  const shapes = useMemo(() => {
    const path = geoPath(projection);
    return areas.map((feature) => ({
      iso: feature.properties.shapeISO,
      name: AREA_NAMES[feature.properties.shapeISO] ?? feature.properties.shapeName,
      d: path(feature) ?? '',
      centroid: path.centroid(feature),
      feature,
    }));
  }, [areas, projection]);

  /**
   * Город Ташкент — отдельной точкой.
   *
   * На карте страны он размером с полсантиметра и лежит внутри
   * Ташкентской области: попасть по нему мышью нельзя, а пальцем — тем
   * более, и область под курсором всегда перехватывала нажатие. Точка
   * даёт цель, по которой можно попасть, и подпись, по которой видно,
   * что это отдельная единица, а не кружок на месте столицы.
   */
  const city = useMemo(() => {
    const found = shapes.find((shape) => shape.iso === CITY);
    if (!found) return null;
    const [x, y] = found.centroid;
    return Number.isFinite(x) && Number.isFinite(y) ? { x, y, name: found.name } : null;
  }, [shapes]);

  const neighbours = useMemo(() => NEIGHBOURS.flatMap((one) => {
    const at = projection(one.at);
    if (!at) return [];
    return [{
      name: one.name,
      x: Math.min(Math.max(at[0], 70), WIDTH - 70),
      y: Math.min(Math.max(at[1], 22), HEIGHT - 14),
    }];
  }), [projection]);

  // Офисы, их подписи и подписи областей зависят только от данных.
  /**
   * Подписи областей.
   *
   * Маркеров офисов на карте нет: в области их бывает несколько, и одна
   * точка либо врёт про остальные, либо повторяет название области,
   * которое и так написано рядом. Карта отвечает на вопрос «где это»,
   * а список офисов под ней — на вопрос «какие они».
   */
  const layout = useMemo(() => {
    const taken: Box[] = [];
    const names = shapes.flatMap((shape) => {
      const lines = AREA_LABELS[shape.iso];
      const [cx, cy] = shape.centroid;
      if (!lines || !Number.isFinite(cx) || !Number.isFinite(cy)) return [];
      const w = Math.max(...lines.map((line) => line.length)) * 6.6;
      const spots: [number, number][] = [[0, 0], [0, 34], [0, -34], [46, 0], [-46, 0], [0, 58]];
      for (const [dx, dy] of spots) {
        const x = cx + dx;
        const y = cy + dy;
        const box: Box = [x - w / 2, y - 12, x + w / 2, y + 16];
        if (taken.some((one) => hits(one, box))) continue;
        const inside = projection.invert?.([x, y]);
        if (!inside || !geoContains(shape.feature, inside)) continue;
        taken.push(box);
        return [{ iso: shape.iso, lines, x, y }];
      }
      return [];
    });
    return { names };
  }, [projection, shapes]);

  // Масштаб — трансформацией группы SVG, не CSS `zoom`: так карта
  // остаётся векторной и не мылится, а подписи не растягиваются.
  const transform = useCallback((scale: number, x: number, y: number) => (
    `translate(${x} ${y}) translate(${WIDTH / 2} ${HEIGHT / 2}) `
    + `scale(${scale}) translate(${-WIDTH / 2} ${-HEIGHT / 2})`
  ), []);

  /** Держим карту в рамке: при единице сдвигать её некуда. */
  const clamp = useCallback((x: number, y: number, scale: number) => {
    const roomX = (WIDTH * (scale - 1)) / 2;
    const roomY = (HEIGHT * (scale - 1)) / 2;
    return {
      x: Math.max(-roomX, Math.min(roomX, x)),
      y: Math.max(-roomY, Math.min(roomY, y)),
    };
  }, []);

  /** Применить вид к узлу немедленно, без перерисовки React. */
  const apply = useCallback((scale: number, x: number, y: number) => {
    const next = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, scale));
    const held = clamp(x, y, next);
    view.current = { zoom: next, x: held.x, y: held.y };
    canvas.current?.setAttribute('transform', transform(next, held.x, held.y));
  }, [clamp, transform]);

  /**
   * Жесты вешаются вручную, а не через свойства React.
   *
   * React добавляет слушатели касаний пассивными, и `preventDefault`
   * в них не работает: браузер успевает начать собственный зум, и
   * вместе с картой увеличивается вся страница. Ручная подписка с
   * `passive: false` — единственный способ это остановить.
   */
  useEffect(() => {
    const box = frame.current;
    if (!box) return;

    const spanOf = (touches: TouchList) => {
      const [a, b] = [touches[0], touches[1]];
      if (!a || !b) return 0;
      return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    };

    const onStart = (event: TouchEvent) => {
      if (event.touches.length === 2) {
        // Без `preventDefault`: два пальца — это ещё не щипок. Чаще
        // ими просто листают страницу, и забирать такой жест себе
        // нельзя. Решаем по тому, меняется ли расстояние.
        gesture.current = {
          kind: 'pinch', span: spanOf(event.touches), zoom: view.current.zoom,
          pinching: false,
        };
        return;
      }
      if (event.touches.length === 1 && view.current.zoom > 1) {
        const touch = event.touches[0]!;
        gesture.current = {
          kind: 'drag', x: touch.clientX, y: touch.clientY,
          from: [view.current.x, view.current.y],
        };
      }
    };

    const onMove = (event: TouchEvent) => {
      const held = gesture.current;
      if (!held) return;

      if (held.kind === 'pinch' && event.touches.length === 2) {
        const span = spanOf(event.touches);
        if (held.span <= 0 || span <= 0) return;
        // Пальцы разъезжаются — это щипок, и он наш. Пальцы едут
        // вместе — это прокрутка, и страница должна листаться.
        // Порог нужен, чтобы дрожание руки в начале движения не
        // выглядело как попытка масштабировать.
        if (!held.pinching && Math.abs(span - held.span) < PINCH_START_PX) return;
        held.pinching = true;
        event.preventDefault();
        apply(held.zoom * (span / held.span), view.current.x, view.current.y);
        return;
      }

      if (held.kind === 'drag' && event.touches.length === 1) {
        event.preventDefault();
        const touch = event.touches[0]!;
        const bounds = box.getBoundingClientRect();
        // Пиксели рамки — в координаты `viewBox`: иначе на узком
        // экране палец тащил бы карту вдвое быстрее ожидаемого.
        const kx = WIDTH / bounds.width;
        const ky = HEIGHT / bounds.height;
        apply(
          view.current.zoom,
          held.from[0] + (touch.clientX - held.x) * kx,
          held.from[1] + (touch.clientY - held.y) * ky,
        );
      }
    };

    const onEnd = (event: TouchEvent) => {
      if (event.touches.length > 0) return;
      gesture.current = null;
      // Итог жеста — в состояние: от него зависят кнопки масштаба и
      // курсор. Один раз, а не шестьдесят раз в секунду.
      setZoom(view.current.zoom);
    };

    /**
     * Колесо масштабирует только вместе с Ctrl.
     *
     * Обычное вращение колеса и прокрутка двумя пальцами по тачпаду
     * приходят сюда одним и тем же событием, и если забирать его
     * себе, страницу над картой становится не пролистать: курсор
     * заехал на блок — и лист встал. Щипок по тачпаду браузер
     * присылает с поднятым `ctrlKey`; по нему и отличаем.
     */
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      apply(
        view.current.zoom * (event.deltaY > 0 ? 0.9 : 1.1),
        view.current.x, view.current.y,
      );
      setZoom(view.current.zoom);
    };

    box.addEventListener('touchstart', onStart, { passive: false });
    box.addEventListener('touchmove', onMove, { passive: false });
    box.addEventListener('touchend', onEnd);
    box.addEventListener('touchcancel', onEnd);
    box.addEventListener('wheel', onWheel, { passive: false });
    return () => {
      box.removeEventListener('touchstart', onStart);
      box.removeEventListener('touchmove', onMove);
      box.removeEventListener('touchend', onEnd);
      box.removeEventListener('touchcancel', onEnd);
      box.removeEventListener('wheel', onWheel);
    };
  }, [apply]);

  /** Кнопки масштаба идут той же дорогой, что и жесты. */
  function zoomTo(scale: number) {
    apply(scale, view.current.x, view.current.y);
    setZoom(view.current.zoom);
  }

  /** Подсказка двигается напрямую: состояние React на движение мыши не меняется. */
  function track(event: React.MouseEvent, name: string) {
    const box = frame.current?.getBoundingClientRect();
    const node = tip.current;
    if (!box || !node) return;
    if (node.textContent !== name) node.textContent = name;
    node.style.transform = `translate(${event.clientX - box.left + 14}px, ${event.clientY - box.top + 14}px)`;
    node.hidden = false;
  }

  function leave() {
    if (tip.current) tip.current.hidden = true;
  }

  return (
    <div className={zoom > 1 ? 'map map--zoomed' : 'map'} ref={frame}>
      <svg className="map__svg" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img"
           aria-label="Карта офисов на границах Узбекистана">
        <g ref={canvas}>
          {neighbours.map((one) => (
            <text key={one.name} className="map__neighbour" x={one.x} y={one.y}>{one.name}</text>
          ))}

          {[...shapes].sort((a, b) => Number(a.iso === CITY) - Number(b.iso === CITY)).map((shape) => {
            const on = shape.iso === area;
            return (
              <path
                key={shape.iso}
                d={shape.d}
                className={on ? 'map__area map__area--on' : 'map__area'}
                role="button"
                tabIndex={0}
                aria-label={shape.name}
                aria-pressed={on}
                onMouseMove={(event) => track(event, shape.name)}
                onMouseLeave={leave}
                onClick={() => onPickArea(on ? null : shape.iso)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onPickArea(on ? null : shape.iso);
                  }
                }}
              />
            );
          })}

          {city && (
            <g className="map__city" role="button" tabIndex={0}
               aria-label={city.name} aria-pressed={area === CITY}
               onMouseMove={(event) => track(event, city.name)}
               onMouseLeave={leave}
               onClick={() => onPickArea(area === CITY ? null : CITY)}
               onKeyDown={(event) => {
                 if (event.key === 'Enter' || event.key === ' ') {
                   event.preventDefault();
                   onPickArea(area === CITY ? null : CITY);
                 }
               }}>
              {/* Прозрачный круг пошире — чтобы попадать пальцем. */}
              <circle className="map__city-hit" cx={city.x} cy={city.y} r={14} />
              <circle className={area === CITY ? 'map__city-dot map__city-dot--on' : 'map__city-dot'}
                      cx={city.x} cy={city.y} r={6.5} />
              <text className="map__city-name" x={city.x} y={city.y - 12}>ТАШКЕНТ</text>
            </g>
          )}

          {layout.names.map((one) => (
            <text key={`name-${one.iso}`}
                  className={one.iso === area ? 'map__area-name map__area-name--on' : 'map__area-name'}
                  x={one.x} y={one.y}>
              <tspan x={one.x} dy="0">{one.lines[0]}</tspan>
              <tspan x={one.x} dy="13">{one.lines[1]}</tspan>
            </text>
          ))}

        </g>
      </svg>

      <span className="map__tip" ref={tip} hidden />


      <div className="map__zoom" role="group" aria-label="Масштаб карты">
        <button type="button" aria-label="Приблизить" disabled={zoom >= MAX_ZOOM}
                onClick={() => zoomTo(view.current.zoom + 0.5)}>
          <AppIcon name="plus" size={20} />
        </button>
        <button type="button" aria-label="Отдалить" disabled={zoom <= MIN_ZOOM}
                onClick={() => zoomTo(view.current.zoom - 0.5)}>
          <span className="map__minus" />
        </button>
        <button type="button" aria-label="Показать всю страну"
                onClick={() => { zoomTo(1); onPickArea(null); }}>
          <AppIcon name="pin" size={20} />
        </button>
      </div>
    </div>
  );
});
