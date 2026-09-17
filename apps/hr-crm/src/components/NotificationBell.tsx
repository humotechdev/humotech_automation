/**
 * Колокольчик и выпадающее окно уведомлений.
 *
 * Показывает ленту событий кадровика — заявки, справки, исправления
 * отметок, незакрытые выходы, обращения, ошибки доставки. Это НЕ
 * очередь отправки: та отвечает «ушло ли сообщение сотруднику» и живёт
 * на своей вкладке страницы `/notifications`.
 *
 * Три правила, которые легко нарушить и которые здесь закреплены.
 *
 * **Прочтение — это действие человека.** Открытие окна ничего не
 * помечает: первая строка показана справа для удобства, а не потому,
 * что её прочитали. Отметка ставится при нажатии на строку и по кнопке
 * «Прочитать все» — и только для того, кто нажал.
 *
 * **Фоновое обновление не трогает выбранное.** Пока окно открыто, лента
 * перечитывается; выбранная строка остаётся выбранной, даже если новое
 * событие встало выше неё. Карточка справа живёт своим запросом, поэтому
 * она не гаснет, даже если заявку успели рассмотреть и она ушла из ленты.
 *
 * **Числа приходят с сервера.** Ни счётчика, ни строки здесь не
 * придумывается: пустая лента показывается пустой, а не «примером».
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import * as api from '../api/crm';
import { ApiFailure, messageFor } from '../api/errors';
import { AppIcon, ICON_SIZE } from './AppIcon';
import { FeedCard, FeedRow } from './FeedParts';
import {
  FEED_FILTERS,
  badgeText,
  filterTitle,
} from '../features/notifications/feed-model';

/** Сколько строк помещается в окне. Задание: последние 5–7. */
const ROWS = 7;
/** Как часто перечитывается ОТКРЫТАЯ лента. */
const OPEN_TICK = 20_000;
/** Как часто обновляется число у колокольчика, пока окно закрыто. */
const IDLE_TICK = 60_000;

export function NotificationBell() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<string>('all');
  const [filterOpen, setFilterOpen] = useState(false);
  const [counts, setCounts] = useState<api.FeedCounts | null>(null);
  const [page, setPage] = useState<api.FeedPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [card, setCard] = useState<api.FeedDetail | null>(null);
  const [cardFailed, setCardFailed] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  const unread = counts?.unread ?? 0;

  // Число у колокольчика нужно и на страницах без ленты, поэтому оно
  // обновляется само — но реже и только в видимой вкладке: в скрытой
  // опрашивать некого.
  useEffect(() => {
    if (open) return;
    const stop = new AbortController();
    let timer: number | undefined;
    const ask = () => {
      if (document.hidden) return;
      Promise.allSettled([
        api.feedCounts(stop.signal),
        api.feed({ limit: '1' }, stop.signal),
      ]).then(([countResult, pageResult]) => {
        if (countResult.status === 'fulfilled') {
          const fromPage = pageResult.status === 'fulfilled' ? pageResult.value.counts : null;
          setCounts(fromPage && fromPage.unread > countResult.value.unread ? fromPage : countResult.value);
        } else if (pageResult.status === 'fulfilled') {
          // Some deployments expose the unread total only on the feed
          // response; keep the badge visible in that case as well.
          setCounts(pageResult.value.counts);
        }
      });
    };
    ask();
    timer = window.setInterval(ask, IDLE_TICK);
    return () => {
      stop.abort();
      window.clearInterval(timer);
    };
  }, [open]);

  const load = useCallback(
    (signal?: AbortSignal) =>
      api
        .feed({ scope: filter, limit: String(ROWS) }, signal)
        .then((data) => {
          setPage(data);
          setCounts(data.counts);
          setFailed(null);
          return data;
        })
        .catch((error: unknown) => {
          if (signal?.aborted) return null;
          // Неудача не стирает уже показанное: пустой список на месте
          // ошибки читался бы как «уведомлений нет».
          setFailed(messageFor(error));
          return null;
        }),
    [filter],
  );

  // Открытие и смена фильтра — это новый набор, поэтому список гаснет
  // состоянием загрузки. Фоновое обновление строки не трогает.
  useEffect(() => {
    if (!open) return;
    const stop = new AbortController();
    setLoading(true);
    void load(stop.signal).then(() => setLoading(false));
    const timer = window.setInterval(() => {
      if (!document.hidden) void load(stop.signal);
    }, OPEN_TICK);
    return () => {
      stop.abort();
      window.clearInterval(timer);
    };
  }, [open, load]);

  // Первая строка показана справа сразу — но НЕ помечается прочитанной:
  // человек её ещё не открывал.
  useEffect(() => {
    if (!open || !page) return;
    const first = page.items[0];
    if (!picked && first) setPicked(first.id);
  }, [open, page, picked]);

  useEffect(() => {
    if (!open || !picked) return;
    const stop = new AbortController();
    setCardFailed(null);
    api
      .feedEvent(picked, stop.signal)
      .then(setCard)
      .catch((error: unknown) => {
        if (stop.signal.aborted) return;
        setCard(null);
        setCardFailed(
          error instanceof ApiFailure && error.status === 404
            ? 'Событие больше недоступно: запись изменилась или закрыта'
            : messageFor(error),
        );
      });
    return () => stop.abort();
  }, [open, picked]);

  useEffect(() => {
    if (!open) return;
    const stop = new AbortController();
    document.addEventListener(
      'mousedown',
      (event) => {
        if (!box.current?.contains(event.target as Node)) close();
      },
      { signal: stop.signal },
    );
    document.addEventListener(
      'keydown',
      (event) => {
        if (event.key === 'Escape') close();
      },
      { signal: stop.signal },
    );
    return () => stop.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function close() {
    setOpen(false);
    setFilterOpen(false);
  }

  /**
   * Выбор строки: показать карточку и отметить прочитанной.
   *
   * Счётчик меняется сразу, не дожидаясь ответа, — иначе нажатие
   * выглядит как ничего не сделавшее. Отказ возвращает прежнее
   * состояние и говорит об этом одной строкой, без окна поверх окна.
   */
  function choose(event: api.FeedEvent) {
    setPicked(event.id);
    if (event.read_at) return;

    const before = { page, counts };
    const moment = new Date().toISOString();
    setPage((was) =>
      was
        ? {
            ...was,
            items: was.items.map((one) =>
              one.id === event.id ? { ...one, read_at: moment } : one,
            ),
          }
        : was,
    );
    setCounts((was) => (was ? { ...was, unread: Math.max(0, was.unread - 1) } : was));
    setNote(null);

    void api
      .readFeedEvent(event.id)
      .then(setCounts)
      .catch((error: unknown) => {
        setPage(before.page);
        setCounts(before.counts);
        setNote(messageFor(error));
      });
  }

  function readAll() {
    const before = { page, counts };
    const moment = new Date().toISOString();
    setPage((was) =>
      was
        ? {
            ...was,
            items: was.items.map((one) => ({ ...one, read_at: one.read_at ?? moment })),
          }
        : was,
    );
    setCounts((was) => (was ? { ...was, unread: 0 } : was));
    setNote(null);

    void api
      .readAllFeed()
      .then((fresh) => {
        setCounts(fresh);
        void load();
      })
      .catch((error: unknown) => {
        setPage(before.page);
        setCounts(before.counts);
        setNote(messageFor(error));
      });
  }

  function follow(url: string) {
    close();
    navigate(url);
  }

  const rows = page?.items ?? [];

  return (
    <>
      {open && <div className="nf__scrim" aria-hidden="true" />}
      <div className="nf" ref={box}>
        <button
          type="button"
          className="tool tool--bell"
          aria-label={
            unread > 0 ? `Уведомления, непрочитанных ${unread}` : 'Уведомления'
          }
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={() => setOpen((was) => !was)}
        >
          <AppIcon name="bell" size={ICON_SIZE.title} />
          {unread > 0 && <span className="nf__badge">{badgeText(unread)}</span>}
        </button>

        {open && (
          <section className="nf__panel" role="dialog" aria-label="Уведомления">
            <header className="nf__head">
              <h2 className="nf__title">
                Уведомления
                {unread > 0 && <span className="nf__count">{badgeText(unread)}</span>}
              </h2>
              <div className="nf__tools">
                <div className="nf__filter">
                  <button
                    type="button"
                    className="nf__link"
                    aria-haspopup="listbox"
                    aria-expanded={filterOpen}
                    onClick={() => setFilterOpen((was) => !was)}
                  >
                    {filterTitle(filter)}
                    <AppIcon name="chevron" size={16} />
                  </button>
                  {filterOpen && (
                    <ul className="nf__menu" role="listbox" aria-label="Отбор уведомлений">
                      {FEED_FILTERS.map((one) => {
                        const count = counts ? counts[one.key] : null;
                        return (
                          <li key={one.key}>
                            <button
                              type="button"
                              role="option"
                              aria-selected={one.key === filter}
                              className={
                                one.key === filter
                                  ? 'nf__option nf__option--on'
                                  : 'nf__option'
                              }
                              onClick={() => {
                                setFilter(one.key);
                                setFilterOpen(false);
                                setPicked(null);
                                setCard(null);
                              }}
                            >
                              <span>{one.title}</span>
                              {count !== null && count > 0 && (
                                <span className="nf__optionCount">{count}</span>
                              )}
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
                <span className="nf__sep" aria-hidden="true" />
                <button
                  type="button"
                  className="nf__link nf__link--action"
                  onClick={readAll}
                  disabled={unread === 0}
                >
                  Прочитать все
                </button>
              </div>
            </header>

            {note && <p className="nf__note">{note}</p>}

            <div className="nf__body">
              <div className="nf__list">
                {loading && rows.length === 0 && (
                  <p className="nf__empty">Загружаем уведомления…</p>
                )}
                {!loading && failed && rows.length === 0 && (
                  <p className="nf__empty nf__empty--bad">
                    {failed}
                    <button type="button" className="nf__retry" onClick={() => void load()}>
                      Повторить
                    </button>
                  </p>
                )}
                {!loading && !failed && rows.length === 0 && (
                  <p className="nf__empty">
                    {filter === 'all'
                      ? 'Новых событий нет'
                      : 'В этом отборе событий нет'}
                  </p>
                )}

                {rows.map((event) => (
                  <FeedRow
                    key={event.id}
                    event={event}
                    active={event.id === picked}
                    onPick={choose}
                  />
                ))}

                <Link className="nf__history" to="/notifications" onClick={close}>
                  История уведомлений
                  <AppIcon name="arrow" size={16} />
                </Link>
              </div>

              <div className="nf__card">
                {cardFailed && <p className="nf__empty nf__empty--bad">{cardFailed}</p>}
                {!cardFailed && !card && rows.length > 0 && (
                  <p className="nf__empty">Загружаем событие…</p>
                )}
                {!cardFailed && !card && rows.length === 0 && (
                  <p className="nf__empty">Выберите уведомление</p>
                )}
                {card && <FeedCard card={card} onFollow={follow} />}
              </div>
            </div>
          </section>
        )}
      </div>
    </>
  );
}
