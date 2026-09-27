/**
 * Счётчики разделов для бокового меню.
 *
 * Раньше их считала «Главная» и передавала оболочке свойством: на любой
 * другой странице числа рядом с «Заявками» и «Обращениями» исчезали —
 * не потому, что работы не стало, а потому, что считать было некому.
 *
 * Теперь источник один на всё приложение. Он живёт вне React, поэтому
 * переход между разделами его не сбрасывает: оболочка при переходе
 * перемонтируется, а числа остаются те же, что были секунду назад.
 *
 * Число «Обращений» живое: сигнал потока событий пересчитывает его сразу,
 * на любой странице CRM. Остальное — изредка и только в видимой вкладке:
 * редкий опрос остаётся и при живом потоке — обращение становится
 * просроченным само, без всякого события. Ошибка запроса прежние числа
 * не стирает — пустое место читалось бы как «всё разобрано».
 */

import { useEffect, useState } from 'react';

import * as api from '../../api/crm';
import { onLive } from '../realtime/events';

export type Badges = Record<string, number>;

/** Как часто перечитывать. Минута: числа меняются людьми, не машиной. */
const TICK = 60_000;

let current: Badges = {};
let loading: Promise<void> | null = null;
let loadedAt = 0;
const listeners = new Set<(value: Badges) => void>();

function publish(next: Badges) {
  current = next;
  for (const listener of listeners) listener(next);
}

/** Растёт при каждом сбросе: ответ, начатый до выхода, после него не публикуется. */
let generation = 0;

async function load(): Promise<void> {
  const started = generation;
  const [queue, talks] = await Promise.allSettled([
    api.queueCounts({}),
    api.questionCounts({}),
  ]);

  if (started !== generation) return;
  const next: Badges = { ...current };
  if (queue.status === 'fulfilled') {
    // «Требуют решения» — то же число, что на вкладке очереди заявок.
    next['requests'] = Number(queue.value['open'] ?? 0);
  }
  if (talks.status === 'fulfilled') {
    // Обращения без ответа: остальные не ждут кадровика.
    next['questions'] = Number(talks.value.quick?.unanswered ?? 0);
  }
  loadedAt = Date.now();
  publish(next);
}

function refresh(force = false): void {
  if (loading) return;
  if (!force && Date.now() - loadedAt < TICK) return;
  loading = load()
    .catch(() => undefined)
    .finally(() => {
      loading = null;
      if (again) {
        again = false;
        refresh(true);
      }
    });
}

/* --- живое обновление --------------------------------------------------- */

/** Обращение, из-за которого число выросло: по нажатию меню открывается оно. */
let latestQuestion: string | null = null;
let liveBound = false;
let again = false;

/**
 * Пересчёт по сигналу — сразу. Если запрос уже идёт, он начат до сигнала:
 * после него — ещё один, и только один, сколько бы сигналов ни пришло.
 */
function refreshSoon(): void {
  if (loading) {
    again = true;
    return;
  }
  refresh(true);
}

function bindLive(): void {
  if (liveBound) return;
  liveBound = true;
  onLive((signal) => {
    if (signal.type === 'question' && signal.what === 'message') latestQuestion = signal.id;
    refreshSoon();
  });
}

/** Какое обращение открыть по нажатию «Обращения», когда там ждут. */
export function latestQuestionId(): string | null {
  return latestQuestion;
}

/**
 * Числа для меню. Одинаковые на всех страницах и на всех экземплярах
 * оболочки: состояние общее, подписка — на каждый экземпляр.
 */
export function useBadges(): Badges {
  const [value, setValue] = useState<Badges>(current);

  useEffect(() => {
    bindLive();
    listeners.add(setValue);
    refresh();
    const timer = window.setInterval(() => {
      if (!document.hidden) refresh();
    }, TICK);
    return () => {
      listeners.delete(setValue);
      window.clearInterval(timer);
    };
  }, []);

  return value;
}

/** Сбросить кэш — например, после выхода из системы. */
export function forgetBadges(): void {
  generation += 1;
  loading = null;
  loadedAt = 0;
  latestQuestion = null;
  again = false;
  publish({});
}
