/**
 * «Сравнение» на странице посещаемости: что сравниваем, за какой период
 * и как грузить несколько объектов сразу.
 *
 * Сравнение не считает явку само. Каждый объект — сотрудник, офис или
 * регион — это отдельный запрос сводки сервера со своим фильтром; здесь
 * только решается, какие запросы отправить, и ответы раскладываются по
 * местам. Правило явки одно — серверное, и для сравнения второго нет.
 */

import { useEffect, useRef, useState } from 'react';

import { ApiFailure } from '../../api/errors';
import { LOADING, shift, type Block } from '../dashboard/data';

/** Что сравниваем. `staff` — все сотрудники одного офиса (или всей области) между собой. */
export type CompareKind = 'employee' | 'office' | 'region' | 'staff';

export const COMPARE_KINDS: CompareKind[] = ['employee', 'office', 'region', 'staff'];

/**
 * Не больше восьми объектов на графике: девятая линия уже не отличается
 * от соседних по цвету, а каждый объект — отдельный запрос к серверу.
 */
export const COMPARE_LIMIT = 8;

/** Самый длинный период, который принимает сервер. */
export const MAX_SPAN_DAYS = 366;

/**
 * Цвета линий: синяя гамма страницы и несколько акцентов. Соседние по
 * порядку цвета заметно различаются по светлоте или тону — первые две
 * линии не сливаются даже на тусклом экране.
 */
export const SERIES_COLORS = [
  '#0875e8', // синий страницы
  '#f08c00', // янтарный акцент
  '#12316b', // тёмно-синий
  '#18a7b5', // бирюзовый
  '#7c55d8', // сиреневый
  '#6aa9f2', // голубой
  '#1d9a5b', // зелёный
  '#c2417a', // малиновый
] as const;

export function colorOf(index: number): string {
  return SERIES_COLORS[index % SERIES_COLORS.length]!;
}

export function kindOf(raw: string | null): CompareKind | null {
  return COMPARE_KINDS.includes(raw as CompareKind) ? (raw as CompareKind) : null;
}

/**
 * «Выбрано пусто» в адресе. Без отметки офисы и регионы показываются по
 * умолчанию — первые восемь; человек, снявший все галочки, должен увидеть
 * пустое сравнение, а не снова те же восемь.
 */
export const NONE = '~';

/** Список из адреса: без пустых, без повторов, не длиннее предела. */
export function idsOf(raw: string | null): string[] {
  const seen = new Set<string>();
  for (const one of (raw ?? '').split(',')) {
    const id = one.trim();
    if (id && id !== NONE) seen.add(id);
  }
  return [...seen].slice(0, COMPARE_LIMIT);
}

function lastOfMonth(day: string): string {
  const [y, m] = day.split('-').map(Number);
  const date = new Date(Date.UTC(y ?? 1970, m ?? 1, 0));
  return date.toISOString().slice(0, 10);
}

/**
 * Период сравнения из адреса.
 *
 * `from`/`to` — как их выбрал человек. Старая ссылка `period=month`
 * означала месяц вокруг `date`, и её смысл сохраняется. Без дат — последние
 * 30 дней: месяц «с первого числа» в начале месяца — это один-два дня, и
 * сравнивать по ним нечего. Будущее отрезается, начало не позже конца,
 * длина — не больше, чем принимает сервер.
 */
export function compareSpan(params: URLSearchParams, now: string): { from: string; to: string } {
  const valid = (value: string | null) => (value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null);
  let from = valid(params.get('from'));
  let to = valid(params.get('to'));
  if (params.get('period') === 'month' && !from && !to) {
    const anchor = valid(params.get('date')) ?? now;
    from = `${anchor.slice(0, 8)}01`;
    to = lastOfMonth(anchor);
  }
  to = to && to < now ? to : now;
  from = from ?? shift(to, -29);
  if (from > to) from = to;
  if (dayCount(from, to) > MAX_SPAN_DAYS) from = shift(to, -(MAX_SPAN_DAYS - 1));
  return { from, to };
}

export function dayCount(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000) + 1;
}

/** Все дни периода по порядку — общая ось графика для всех объектов. */
export function daysOf(from: string, to: string): string[] {
  const out: string[] = [];
  for (let day = from; day <= to && out.length <= MAX_SPAN_DAYS; day = shift(day, 1)) out.push(day);
  return out;
}

/**
 * Нижняя граница шкалы явки: чуть ниже самого низкого значения, с шагом
 * в 10 пунктов. Колебания видны, а не прижаты к верху, и ноль не
 * подставляется туда, где его никто не измерял.
 */
export function chartFloor(values: (number | null)[]): number {
  const known = values.filter((one): one is number => one !== null);
  if (!known.length) return 0;
  return Math.max(0, Math.floor((Math.min(...known) - 5) / 10) * 10);
}

/** Как рисовать линию: явка каждого дня или накопленная с начала периода. */
export type LineKind = 'day' | 'total';

/** День сводки сервера — ровно то, что нужно линии. */
type DayRow = { day: string; future: boolean; expected: number; attended: number; percent: number | null };

/**
 * Точки линии на общей оси дат.
 *
 * `day` — процент дня, как его отдал сервер. `total` — явка с начала
 * периода по этот день: пришедшие, делённые на ожидаемых, из тех же
 * дневных чисел сервера. У одного сотрудника явка дня — всегда 0 или
 * 100 %, и линия по дням превращается в пилу; накопленная показывает,
 * как складывается его итог, и в последний день совпадает с итогом
 * периода из той же сводки. Новых правил здесь нет: только сумма
 * числителей и знаменателей, которые уже посчитал сервер.
 *
 * Дни без графика и будущие — без точки (`null`): линия их пропускает.
 */
export function seriesOf(days: string[], rows: DayRow[], kind: LineKind): (number | null)[] {
  const byDay = new Map(rows.filter((one) => !one.future).map((one) => [one.day, one]));
  let attended = 0;
  let expected = 0;
  return days.map((day) => {
    const row = byDay.get(day);
    if (!row || row.expected <= 0) return null;
    if (kind === 'day') return row.percent;
    attended += row.attended;
    expected += row.expected;
    return Math.round((attended / expected) * 1000) / 10;
  });
}

/** Место каждого объекта: его блок и то, какому периоду он принадлежит. */
export type Slot<T> = { block: Block<T>; stale: boolean };

/**
 * Несколько независимых запросов — по одному на объект.
 *
 * Как `useBlock`, только объектов несколько и число их меняется:
 *
 * — сбой одного не трогает остальные: у каждого своё состояние, и
 *   ошибка остаётся ошибкой, а не нулём в строке;
 * — добавленный объект не перезапрашивает уже загруженные;
 * — убранный объект и смена периода отменяют свои запросы, и поздний
 *   ответ старого периода до экрана не доходит;
 * — при смене периода прежние числа остаются на месте с пометкой
 *   `stale`, пока не придёт свежий ответ.
 */
export function useEach<T>(
  ids: string[],
  load: (id: string, signal: AbortSignal) => Promise<T>,
  key: string,
  active: boolean,
): Record<string, Slot<T>> {
  const [slots, setSlots] = useState<Record<string, { key: string; block: Block<T> }>>({});
  // Запрос в пути или уже отвеченный — по каждому объекту, с ключом периода.
  const flight = useRef(new Map<string, { key: string; stop: AbortController }>());
  const list = ids.join(',');

  useEffect(() => {
    const running = flight.current;
    const wanted = list ? list.split(',') : [];
    if (!active) {
      // Ушли с вкладки — недогруженное отменяется и при возврате запросится заново.
      for (const one of running.values()) one.stop.abort();
      running.clear();
      return;
    }
    for (const [id, one] of running) {
      if (!wanted.includes(id)) {
        one.stop.abort();
        running.delete(id);
      }
    }
    setSlots((was) => {
      const next: Record<string, { key: string; block: Block<T> }> = {};
      for (const id of wanted) {
        const old = was[id];
        next[id] = old && (old.key === key || old.block.state === 'ready') ? old : { key, block: LOADING };
      }
      return next;
    });
    for (const id of wanted) {
      if (running.get(id)?.key === key) continue;
      running.get(id)?.stop.abort();
      const stop = new AbortController();
      running.set(id, { key, stop });
      load(id, stop.signal)
        .then((data) => {
          if (stop.signal.aborted) return;
          setSlots((was) => (id in was ? { ...was, [id]: { key, block: { state: 'ready', data } } } : was));
        })
        .catch((error) => {
          if (stop.signal.aborted) return;
          // Неудачный ответ — не повод держать числа прошлого периода:
          // строка говорит, что не загрузилась, а не показывает чужое.
          const block: Block<T> = error instanceof ApiFailure && error.kind === 'session'
            ? { state: 'denied' }
            : { state: 'error', kind: error instanceof ApiFailure ? error.kind : 'server' };
          setSlots((was) => (id in was ? { ...was, [id]: { key, block } } : was));
        });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list, key, active]);

  useEffect(() => {
    const running = flight.current;
    return () => {
      for (const one of running.values()) one.stop.abort();
      running.clear();
    };
  }, []);

  const out: Record<string, Slot<T>> = {};
  for (const id of ids) {
    const slot = slots[id];
    out[id] = slot ? { block: slot.block, stale: slot.key !== key } : { block: LOADING, stale: false };
  }
  return out;
}
