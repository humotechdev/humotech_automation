/**
 * Та же карта офиса, но на Google Maps.
 *
 * Подложка OpenStreetMap в Узбекистане нарисована неровно: дома есть не
 * везде, названия организаций встречаются редко, и HR не узнаёт место,
 * куда должен поставить точку. У Google эта страна подписана целиком, и
 * геозона в сто метров ставится по знакомым ориентирам.
 *
 * Компонент включается только при наличии ключа `VITE_GOOGLE_MAPS_KEY`.
 * Без него `OfficeMap` рисует прежнюю карту — не показывать ничего
 * из-за отсутствующего ключа было бы хуже, чем показать OSM.
 *
 * Сам ключ уходит в браузер: так устроен Maps JavaScript API, и иначе
 * карту на странице не нарисовать. Ограничивать его нужно по адресу
 * сайта в консоли Google, а не прятать в коде.
 */

import { useEffect, useRef, useState } from 'react';

import { DEFAULT_CENTER, type Found, type Place } from './OfficeMap';

export const GOOGLE_KEY: string = import.meta.env['VITE_GOOGLE_MAPS_KEY'] ?? '';

type Props = {
  place: Place | null;
  radius: number;
  picking: boolean;
  editable: boolean;
  onPlace: (place: Place) => void;
  focus?: Place | null;
};

/* eslint-disable @typescript-eslint/no-explicit-any */
type Maps = any;

declare global {
  interface Window { google?: { maps?: Maps } }
}

let loading: Promise<Maps> | null = null;

/**
 * Скрипт грузится один раз на вкладку.
 *
 * Google отдаёт библиотеку в глобальный объект, и второй `<script>` с
 * тем же ключом печатает в консоль предупреждение и переопределяет уже
 * созданные классы. Поэтому обещание запоминается.
 */
function loadMaps(): Promise<Maps> {
  if (window.google?.maps) return Promise.resolve(window.google.maps);
  if (loading) return loading;
  loading = new Promise<Maps>((resolve, reject) => {
    const script = document.createElement('script');
    const params = new URLSearchParams({
      key: GOOGLE_KEY,
      language: 'ru',
      region: 'UZ',
      libraries: 'geocoding',
      loading: 'async',
      callback: '__humotechMapsReady',
    });
    (window as unknown as Record<string, unknown>)['__humotechMapsReady'] = () => {
      if (window.google?.maps) resolve(window.google.maps);
      else reject(new Error('maps'));
    };
    script.src = `https://maps.googleapis.com/maps/api/js?${params.toString()}`;
    script.async = true;
    script.onerror = () => reject(new Error('script'));
    document.head.appendChild(script);
  });
  return loading;
}

export function GoogleOfficeMap({ place, radius, picking, editable, onPlace, focus = null }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<Maps>(null);
  const marker = useRef<Maps>(null);
  const circle = useRef<Maps>(null);
  const handler = useRef(onPlace);
  const pickingRef = useRef(picking);
  const editableRef = useRef(editable);
  const [failed, setFailed] = useState(false);

  handler.current = onPlace;
  pickingRef.current = picking;
  editableRef.current = editable;

  // Карта создаётся один раз: пересоздавать её на каждое изменение
  // точки значит терять масштаб и положение, которые человек выбрал.
  useEffect(() => {
    let alive = true;
    loadMaps()
      .then((maps) => {
        if (!alive || !box.current || map.current) return;
        const start = place ?? DEFAULT_CENTER;
        const created = new maps.Map(box.current, {
          center: { lat: start.lat, lng: start.lon },
          zoom: place ? 17 : 12,
          mapTypeControl: false,
          streetViewControl: false,
          fullscreenControl: false,
          zoomControl: true,
          zoomControlOptions: { position: maps.ControlPosition.LEFT_TOP },
          clickableIcons: false,
          /*
           * Два пальца по тачпаду листают страницу, а не масштабируют
           * карту: карта берёт колесо только вместе с Ctrl, а щипок на
           * экране — как был. Иначе страница замирает, стоило увести
           * указатель на карту.
           */
          gestureHandling: 'cooperative',
        });
        // Нажатие по карте ставит точку сразу, без отдельного режима.
        created.addListener('click', (event: Maps) => {
          if (!editableRef.current || !event.latLng) return;
          handler.current({ lat: event.latLng.lat(), lon: event.latLng.lng() });
        });
        map.current = created;
      })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Точка и круг: появляются, двигаются и исчезают вслед за формой.
  useEffect(() => {
    const maps = window.google?.maps;
    if (!maps || !map.current) return;

    if (!place) {
      marker.current?.setMap(null);
      circle.current?.setMap(null);
      marker.current = null;
      circle.current = null;
      return;
    }

    const at = { lat: place.lat, lng: place.lon };
    if (!marker.current) {
      marker.current = new maps.Marker({
        position: at,
        map: map.current,
        draggable: editable,
      });
      marker.current.addListener('dragend', (event: Maps) => {
        if (!event.latLng) return;
        handler.current({ lat: event.latLng.lat(), lon: event.latLng.lng() });
      });
    } else {
      marker.current.setPosition(at);
      marker.current.setDraggable(editable);
    }

    if (!circle.current) {
      circle.current = new maps.Circle({
        map: map.current,
        center: at,
        radius,
        strokeColor: '#1f6fe0',
        strokeOpacity: 0.9,
        strokeWeight: 2,
        fillColor: '#1f6fe0',
        fillOpacity: 0.16,
        clickable: false,
      });
    } else {
      circle.current.setCenter(at);
      circle.current.setRadius(radius);
    }
  }, [place, radius, editable]);

  // Перелёт к найденному адресу. Масштаб поднимается только если карта
  // стоит далеко: приближать уже приближенную — значит отнимать у
  // человека выбранный им вид.
  useEffect(() => {
    if (!focus || !map.current) return;
    map.current.panTo({ lat: focus.lat, lng: focus.lon });
    if ((map.current.getZoom() ?? 0) < 16) map.current.setZoom(17);
  }, [focus]);

  if (failed) {
    return (
      <div className="ofs-map__canvas">
        <p className="ofs-map__failed">
          Карта не загрузилась. Проверьте ключ Google Maps и доступ в интернет —
          точку можно ввести и вручную, координатами.
        </p>
      </div>
    );
  }

  return (
    <div className={picking ? 'ofs-map__canvas ofs-map__canvas--picking' : 'ofs-map__canvas'}
         ref={box} />
  );
}

/** Поиск адреса силами Google: тот же ответ, что и у прежнего поиска. */
export async function googleSearch(text: string): Promise<Found[]> {
  const maps = await loadMaps();
  const geocoder = new maps.Geocoder();
  const answer = await geocoder.geocode({
    address: text,
    componentRestrictions: { country: 'UZ' },
  }).catch(() => null);
  const results: Maps[] = answer?.results ?? [];
  return results.slice(0, 8).map((one) => ({
    label: one.formatted_address as string,
    place: { lat: one.geometry.location.lat(), lon: one.geometry.location.lng() },
  }));
}

/** Адрес по точке. `null` — Google его не знает, и подставлять нечего. */
export async function googleAddress(place: Place): Promise<string | null> {
  const maps = await loadMaps();
  const geocoder = new maps.Geocoder();
  const answer = await geocoder.geocode({
    location: { lat: place.lat, lng: place.lon },
  }).catch(() => null);
  const first = answer?.results?.[0];
  return first ? (first.formatted_address as string) : null;
}
