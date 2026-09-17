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
  const [failed, setFailed] = useState(false);

  handler.current = onPlace;
  pickingRef.current = picking;

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
      });
      L.tileLayer(TILES, { maxZoom: 19, attribution: ATTRIBUTION }).addTo(created);
    } catch {
      setFailed(true);
      return;
    }
    created.on('click', (event: L.LeafletMouseEvent) => {
      if (!pickingRef.current) return;
      handler.current({ lat: event.latlng.lat, lon: event.latlng.lng });
    });
    map.current = created;

    // Контейнер меняет размер вместе с окном и вкладками: без этого
    // Leaflet дорисовывает тайлы только в исходный прямоугольник.
    const observer = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(() => created.invalidateSize())
      : null;
    observer?.observe(box.current);

    return () => {
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

/**
 * Поиск адреса в OpenStreetMap (Nominatim). Только по явному нажатию
 * «Найти», не на каждую букву: у сервиса ограничение — запрос в секунду.
 */
export async function searchAddress(text: string, signal?: AbortSignal): Promise<Found[]> {
  const url = new URL('https://nominatim.openstreetmap.org/search');
  url.searchParams.set('format', 'jsonv2');
  url.searchParams.set('q', text);
  url.searchParams.set('limit', '5');
  url.searchParams.set('accept-language', 'ru');
  const response = await fetch(url, signal ? { signal } : {});
  if (!response.ok) throw new Error(`search ${response.status}`);
  const rows = (await response.json()) as { display_name: string; lat: string; lon: string }[];
  return rows
    .map((row) => ({ label: row.display_name, place: { lat: Number(row.lat), lon: Number(row.lon) } }))
    .filter((row) => Number.isFinite(row.place.lat) && Number.isFinite(row.place.lon));
}

/** Адрес точки на карте — чтобы HR не набирал его заново. */
export async function addressOf(place: Place, signal?: AbortSignal): Promise<string | null> {
  const url = new URL('https://nominatim.openstreetmap.org/reverse');
  url.searchParams.set('format', 'jsonv2');
  url.searchParams.set('lat', String(place.lat));
  url.searchParams.set('lon', String(place.lon));
  url.searchParams.set('zoom', '18');
  url.searchParams.set('accept-language', 'ru');
  const response = await fetch(url, signal ? { signal } : {});
  if (!response.ok) return null;
  const body = (await response.json()) as { display_name?: string; address?: Record<string, string> };
  const parts = body.address;
  if (parts) {
    const street = [parts['road'], parts['house_number']].filter(Boolean).join(', ');
    const city = parts['city'] ?? parts['town'] ?? parts['village'] ?? parts['state'];
    const short = [city, street].filter(Boolean).join(', ');
    if (short) return short;
  }
  return body.display_name ?? null;
}
