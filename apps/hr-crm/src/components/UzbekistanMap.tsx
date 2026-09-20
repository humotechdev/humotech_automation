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

import { memo, useMemo, useRef, useState } from 'react';
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
  tone: 'ok' | 'warn' | 'off';
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

type Label = { x: number; y: number; anchor: 'start' | 'middle' | 'end' };
type Box = [number, number, number, number];
type Point = MapOffice & { at: [number, number] };

const hits = (a: Box, b: Box) => a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];

/**
 * Подписи офисов без наложений: сверху, снизу, справа или слева — первое
 * место, которое не задевает поставленные подписи и чужие маркеры.
 */
function placeLabels(points: Point[]): { labels: Map<string, Label>; taken: Box[] } {
  const markers: Box[] = points.map((one) => {
    const r = one.head ? 22 : 17;
    return [one.at[0] - r, one.at[1] - r, one.at[0] + r, one.at[1] + r];
  });
  const placed: Box[] = [];
  const out = new Map<string, Label>();

  const order = [...points].sort((a, b) => Number(Boolean(b.head)) - Number(Boolean(a.head)));
  for (const one of order) {
    const w = one.name.length * 7.4 + 4;
    const h = 13;
    const r = one.head ? 24 : 19;
    const [x, y] = one.at;
    const options: { label: Label; box: Box }[] = [
      { label: { x: 0, y: -r - 4, anchor: 'middle' }, box: [x - w / 2, y - r - 4 - h, x + w / 2, y - r - 2] },
      { label: { x: 0, y: r + 13, anchor: 'middle' }, box: [x - w / 2, y + r, x + w / 2, y + r + h + 2] },
      { label: { x: r + 4, y: 4, anchor: 'start' }, box: [x + r + 2, y - h / 2, x + r + 4 + w, y + h / 2] },
      { label: { x: -r - 4, y: 4, anchor: 'end' }, box: [x - r - 4 - w, y - h / 2, x - r - 2, y + h / 2] },
    ];
    const free = options.find((option) =>
      option.box[0] >= 0 && option.box[2] <= WIDTH && option.box[1] >= 0 && option.box[3] <= HEIGHT &&
      !placed.some((box) => hits(box, option.box)) &&
      !markers.some((box, at) => points[at]?.id !== one.id && hits(box, option.box)));
    const chosen = free ?? options[0]!;
    placed.push(chosen.box);
    out.set(one.id, chosen.label);
  }
  return { labels: out, taken: [...placed, ...markers] };
}

export const UzbekistanMap = memo(function UzbekistanMap({
  areas, offices, area, office, onPickArea, onPickOffice, popup,
}: {
  areas: Area[];
  offices: MapOffice[];
  /** Выбранная область, код ISO. */
  area: string;
  office: string;
  onPickArea: (iso: string | null) => void;
  onPickOffice: (id: string) => void;
  /** Карточка над выбранным маркером. */
  popup?: React.ReactNode;
}) {
  const [zoom, setZoom] = useState(1);
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
  const layout = useMemo(() => {
    const points: Point[] = offices
      .map((one) => ({ ...one, at: projection([one.longitude, one.latitude]) }))
      .filter((one): one is Point => one.at !== null);
    const { labels, taken } = placeLabels(points);

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
    return { points, labels, names };
  }, [offices, projection, shapes]);

  const chosen = layout.points.find((one) => one.id === office);

  // Масштаб — трансформацией группы SVG, не CSS `zoom`.
  const center: [number, number] = chosen?.at ?? [WIDTH / 2, HEIGHT / 2];
  const transform = zoom === 1
    ? undefined
    : `translate(${center[0]} ${center[1]}) scale(${zoom}) translate(${-center[0]} ${-center[1]})`;

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

  const popupAt = chosen
    ? { left: `${(chosen.at[0] / WIDTH) * 100}%`, top: `${(chosen.at[1] / HEIGHT) * 100}%` }
    : null;

  return (
    <div className="map" ref={frame}>
      <svg className="map__svg" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img"
           aria-label="Карта офисов на границах Узбекистана">
        <g transform={transform}>
          {neighbours.map((one) => (
            <text key={one.name} className="map__neighbour" x={one.x} y={one.y}>{one.name}</text>
          ))}

          {shapes.map((shape) => {
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

          {layout.names.map((one) => (
            <text key={`name-${one.iso}`}
                  className={one.iso === area ? 'map__area-name map__area-name--on' : 'map__area-name'}
                  x={one.x} y={one.y}>
              <tspan x={one.x} dy="0">{one.lines[0]}</tspan>
              <tspan x={one.x} dy="13">{one.lines[1]}</tspan>
            </text>
          ))}

          {layout.points.map((one) => {
            const on = one.id === office;
            const label = layout.labels.get(one.id) ?? { x: 0, y: -22, anchor: 'middle' as const };
            return (
              <g key={one.id}
                 className={`map__office map__office--${one.tone}${on ? ' map__office--on' : ''}${one.head ? ' map__office--head' : ''}`}
                 transform={`translate(${one.at[0]} ${one.at[1]})`}
                 role="button" tabIndex={0} aria-label={`Офис ${one.name}`}
                 onClick={() => onPickOffice(one.id)}
                 onKeyDown={(event) => { if (event.key === 'Enter') onPickOffice(one.id); }}>
                <circle className="map__halo" r={one.head ? 20 : 15} />
                <circle className="map__dot" r={one.head ? 9 : 7} />
                <circle className="map__core" r={one.head ? 3.6 : 2.8} />
                <text className="map__label" x={label.x} y={label.y} textAnchor={label.anchor}>
                  {one.name.toUpperCase()}
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      <span className="map__tip" ref={tip} hidden />

      {popup && popupAt && zoom === 1 && (
        <div className="map__popup" style={popupAt}>{popup}</div>
      )}

      <div className="map__zoom" role="group" aria-label="Масштаб карты">
        <button type="button" aria-label="Приблизить" disabled={zoom >= 3}
                onClick={() => setZoom((was) => Math.min(was + 0.5, 3))}>
          <AppIcon name="plus" size={20} />
        </button>
        <button type="button" aria-label="Отдалить" disabled={zoom <= 1}
                onClick={() => setZoom((was) => Math.max(was - 0.5, 1))}>
          <span className="map__minus" />
        </button>
        <button type="button" aria-label="Показать всю страну"
                onClick={() => { setZoom(1); onPickArea(null); }}>
          <AppIcon name="pin" size={20} />
        </button>
      </div>
    </div>
  );
});
