/**
 * Карта расположения офиса: точка, которую можно перетащить, и круг
 * допустимой зоны вокруг неё.
 *
 * Подложка — OpenStreetMap через Leaflet. Нарисованная от руки карта
 * страны на странице «Офисы» для этого не годится: по ней нельзя
 * поставить точку у нужного подъезда, а геозона в сто метров требует
 * именно этой точности.
 *
 * Компонент ничего не сохраняет сам. Он показывает переданные
 * координаты и радиус и сообщает наверх, куда человек поставил или
 * перетащил точку. Решение «сохранить» принимает форма над ним.
 *
 * Запросы к тайлам и к поиску адресов уходят из браузера HR напрямую в
 * OpenStreetMap. Сервер CRM в этом не участвует, и данных сотрудников в
 * этих запросах нет — только видимая область карты и строка поиска.
 */

import { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

export type Place = { lat: number; lon: number };

/** Центр по умолчанию, если у офиса ещё нет точки: Ташкент. */
export const DEFAULT_CENTER: Place = { lat: 41.2995, lon: 69.2401 };

const TILES = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const ATTRIBUTION = '&copy; участники <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

// Своя метка вместо картинки Leaflet: стандартная ищет файлы по пути,
// который сборщик переписывает, и на странице оказывается пустой квадрат.
const PIN = L.divIcon({
  className: 'ofs-pin',
  html: '<span class="ofs-pin__head"></span><span class="ofs-pin__tail"></span>',
  iconSize: [30, 40],
  iconAnchor: [15, 38],
});

type Props = {
  place: Place | null;
  radius: number;
  /** Режим «указать на карте»: щелчок ставит точку. */
  picking: boolean;
  editable: boolean;
  onPlace: (place: Place) => void;
  /** Куда перелететь: меняется ссылка — карта едет к новой точке. */
  focus?: Place | null;
};

export function OfficeMap({ place, radius, picking, editable, onPlace, focus = null }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const marker = useRef<L.Marker | null>(null);
  const circle = useRef<L.Circle | null>(null);
  const handler = useRef(onPlace);
  const pickingRef = useRef(picking);
  const editableRef = useRef(editable);
  const [failed, setFailed] = useState(false);

  handler.current = onPlace;
  pickingRef.current = picking;
  editableRef.current = editable;

  // Карта создаётся один раз на жизнь компонента.
  useEffect(() => {
    if (!box.current || map.current) return;
    let created: L.Map;
    try {
      const start = place ?? DEFAULT_CENTER;
      created = L.map(box.current, {
        center: [start.lat, start.lon],
        zoom: place ? 17 : 12,
        zoomControl: true,
        attributionControl: true,
        // Колесо карта слушает сама — ниже, и только вместе с Ctrl.
        scrollWheelZoom: false,
      });
      L.tileLayer(TILES, { maxZoom: 19, attribution: ATTRIBUTION }).addTo(created);
    } catch {
      setFailed(true);
      return;
    }
    // Нажатие по карте ставит точку сразу, без отдельного режима:
    // тащить метку через полгорода — работа, которой можно не быть.
    created.on('click', (event: L.LeafletMouseEvent) => {
      if (!editableRef.current) return;
      handler.current({ lat: event.latlng.lat, lon: event.latlng.lng });
    });
    map.current = created;

    /*
     * Два пальца по тачпаду листают страницу, а не масштабируют карту.
     *
     * Браузер шлёт и прокрутку, и щипок одним событием `wheel`; щипок
     * отличает `ctrlKey`. Карта во всю высоту окна перехватывала оба, и
     * страница переставала листаться, стоило увести указатель на карту.
     *
     * Шаг копится: щипок по тачпаду сыплет events по паре пикселей, и
     * масштабировать на каждое — значит улететь на весь мир с одного
     * движения.
     */
    const canvas = box.current;
    let gathered = 0;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      gathered += event.deltaY;
      const step = gathered <= -40 ? 1 : gathered >= 40 ? -1 : 0;
      if (step === 0) return;
      gathered = 0;
      created.setZoomAround(created.mouseEventToLatLng(event), created.getZoom() + step);
    };
    canvas.addEventListener('wheel', onWheel, { passive: false });

    // Контейнер меняет размер вместе с окном и вкладками: без этого
    // Leaflet дорисовывает тайлы только в исходный прямоугольник.
    const observer = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(() => created.invalidateSize())
      : null;
    observer?.observe(box.current);

    return () => {
      canvas.removeEventListener('wheel', onWheel);
      observer?.disconnect();
      created.remove();
      map.current = null;
      marker.current = null;
      circle.current = null;
    };
    // Координаты подхватывает эффект ниже; пересоздавать карту на каждое
    // перемещение точки незачем.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Точка и круг следуют за переданными значениями.
  useEffect(() => {
    const current = map.current;
    if (!current) return;
    if (!place) {
      marker.current?.remove();
      circle.current?.remove();
      marker.current = null;
      circle.current = null;
      return;
    }
    const at: L.LatLngExpression = [place.lat, place.lon];
    if (!marker.current) {
      marker.current = L.marker(at, { icon: PIN, draggable: editable, keyboard: false })
        .addTo(current);
      marker.current.on('dragend', () => {
        const next = marker.current?.getLatLng();
        if (next) handler.current({ lat: next.lat, lon: next.lng });
      });
    } else {
      marker.current.setLatLng(at);
    }
    if (editable) marker.current.dragging?.enable();
    else marker.current.dragging?.disable();

    if (!circle.current) {
      circle.current = L.circle(at, {
        radius,
        color: '#1f6fe0',
        weight: 2,
        fillColor: '#1f6fe0',
        fillOpacity: 0.12,
      }).addTo(current);
    } else {
      circle.current.setLatLng(at);
      circle.current.setRadius(radius);
    }
  }, [place, radius, editable]);

  useEffect(() => {
    if (!focus || !map.current) return;
    map.current.flyTo([focus.lat, focus.lon], Math.max(map.current.getZoom(), 17), { duration: 0.6 });
  }, [focus]);

  useEffect(() => {
    box.current?.classList.toggle('ofs-map__canvas--picking', picking);
  }, [picking]);

  if (failed) {
    return <p className="ofs-map__failed">Карта не загрузилась. Координаты можно ввести вручную ниже.</p>;
  }
  return <div ref={box} className="ofs-map__canvas" role="application" aria-label="Карта расположения офиса" />;
}

// --- поиск адреса ----------------------------------------------------------------

export type Found = { label: string; place: Place };

/*
 * Геокодер — Photon (komoot), а не Nominatim.
 *
 * Nominatim отвечает браузеру «Access denied»: его правила запрещают
 * ходить туда прямо со страницы, и поиск адреса на странице просто не
 * работал — человек набирал улицу и не получал ничего. Photon построен
 * на тех же данных OSM, отдаёт CORS-заголовки и ключа не требует.
 *
 * Русского языка у Photon нет: названия приходят так, как записаны в
 * OSM — «Namozgoh ko'chasi». Для Узбекистана это привычная запись, а с
 * ключом Google геокодирование всё равно уходит к нему и по-русски.
 */

/** Границы Узбекистана: запад, юг, восток, север. */
const UZ_BOX = '55.9,37.1,73.2,45.6';

type Spot = {
  name?: string;
  street?: string;
  housenumber?: string;
  district?: string;
  city?: string;
  state?: string;
  county?: string;
};

/** Из частей адреса — одна строка без повторов. */
function labelOf(one: Spot): string {
  const street = [one.street, one.housenumber].filter(Boolean).join(' ');
  const parts = [one.name, street, one.city ?? one.district ?? one.county, one.state];
  const seen = new Set<string>();
  return parts
    .filter((part): part is string => Boolean(part))
    .filter((part) => !seen.has(part) && seen.add(part))
    .join(', ');
}

export async function searchAddress(text: string, signal?: AbortSignal): Promise<Found[]> {
  const url = new URL('https://photon.komoot.io/api/');
  url.searchParams.set('q', text);
  url.searchParams.set('limit', '8');
  // Рамка страны и середина Ташкента: без них «Бухара» находится
  // сперва в Румынии, а «Навои» — в Японии.
  url.searchParams.set('bbox', UZ_BOX);
  url.searchParams.set('lat', String(DEFAULT_CENTER.lat));
  url.searchParams.set('lon', String(DEFAULT_CENTER.lon));
  const response = await fetch(url, signal ? { signal } : {});
  if (!response.ok) throw new Error(`search ${response.status}`);
  const body = (await response.json()) as {
    features?: { properties?: Spot & { countrycode?: string };
      geometry?: { coordinates?: [number, number] } }[];
  };
  return (body.features ?? [])
    .filter((one) => (one.properties?.countrycode ?? 'UZ') === 'UZ')
    .flatMap((one) => {
      const at = one.geometry?.coordinates;
      const label = labelOf(one.properties ?? {});
      if (!at || !label) return [];
      const [lon, lat] = at;
      return Number.isFinite(lat) && Number.isFinite(lon) ? [{ label, place: { lat, lon } }] : [];
    });
}

/** Адрес точки на карте — чтобы HR не набирал его заново. */
export async function addressOf(place: Place, signal?: AbortSignal): Promise<string | null> {
  const url = new URL('https://photon.komoot.io/reverse');
  url.searchParams.set('lat', String(place.lat));
  url.searchParams.set('lon', String(place.lon));
  url.searchParams.set('limit', '1');
  const response = await fetch(url, signal ? { signal } : {});
  if (!response.ok) return null;
  const body = (await response.json()) as { features?: { properties?: Spot }[] };
  const one = body.features?.[0]?.properties;
  if (!one) return null;
  // В карточку офиса идёт короткая запись: город и улица. Название
  // ближайшей аптеки адресом офиса не является.
  const street = [one.street, one.housenumber].filter(Boolean).join(' ');
  const city = one.city ?? one.district ?? one.county ?? one.state;
  const short = [city, street].filter(Boolean).join(', ');
  return short || labelOf(one) || null;
}
