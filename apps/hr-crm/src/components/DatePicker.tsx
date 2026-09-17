/**
 * Поле даты с мини-календарём.
 *
 * Поле остаётся настоящим `input`, а не кнопкой: дату быстрее набрать с
 * клавиатуры, чем искать в сетке, и отнимать эту возможность ради
 * красивого календаря нельзя. Календарь — дополнение к вводу, а не
 * замена ему.
 *
 * Источник истины один — `value` в формате `ГГГГ-ММ-ДД`. То, что видно
 * в поле, это его перевод в `ДД.ММ.ГГГГ`; пока человек печатает,
 * черновик живёт отдельно и наружу не уходит. Наружу значение отдаётся
 * только когда оно разобрано и проверено: по Enter, по уходу фокуса, по
 * выбору дня и по кнопке «Применить».
 *
 * Всплывающий календарь рисуется порталом в `body`. Иначе его обрезает
 * первая же панель с `overflow: hidden` — а таких на странице хватает.
 */

import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { AppIcon } from './AppIcon';
import {
  WEEKDAYS, addDays, addMonths, firstOfMonth, maskRu, monthGrid, monthTitle,
  parseRu, parts, sameMonth, toRu,
} from '../features/calendar/date';

type Props = {
  /** Календарная дата `ГГГГ-ММ-ДД`. Пустая строка — значения нет. */
  value: string;
  onChange: (value: string) => void;
  /** Подпись для диктора: на экране у поля её нет. */
  label: string;
  /** Границы допустимого, тоже `ГГГГ-ММ-ДД`. */
  min?: string;
  max?: string;
  /** Сегодняшняя дата. Отдельно — чтобы тест не зависел от часов машины. */
  now: string;
  disabled?: boolean;
  rangeStart?: string;
  rangeEnd?: string;
  allowEmpty?: boolean;
};

export function DatePicker({ value, onChange, label, min, max, now, disabled, rangeStart, rangeEnd, allowEmpty }: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [anchor, setAnchor] = useState(() => firstOfMonth(value || now));
  const [cursor, setCursor] = useState(value || now);
  const [place, setPlace] = useState({ top: 0, left: 0 });
  /** Что показано в окне: сетка дней или быстрый выбор года. */
  const [view, setView] = useState<'days' | 'years'>('days');

  const field = useRef<HTMLInputElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const popupId = useId();

  const shown = draft ?? toRu(value);

  useEffect(() => {
    if (!open) return;
    setAnchor(firstOfMonth(value || now));
    setCursor(value || now);
    setView('days');
  }, [open, value, now]);

  // Положение всплывающего окна считается от поля. Если снизу не
  // помещается — открывается вверх; если не помещается справа —
  // сдвигается влево. Выйти за экран он не должен ни в одну сторону.
  useLayoutEffect(() => {
    if (!open) return;
    const put = () => {
      const box = frame.current?.getBoundingClientRect();
      const card = popup.current?.getBoundingClientRect();
      if (!box) return;
      const width = card?.width || 320;
      const height = card?.height || 380;
      const below = window.innerHeight - box.bottom - 8;
      const top = below >= height || box.top < height + 8
        ? box.bottom + 8
        : box.top - height - 8;
      const left = Math.max(12, Math.min(box.left, window.innerWidth - width - 12));
      setPlace({ top, left });
    };
    put();
    window.addEventListener('resize', put);
    window.addEventListener('scroll', put, true);
    return () => {
      window.removeEventListener('resize', put);
      window.removeEventListener('scroll', put, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const stop = new AbortController();
    document.addEventListener(
      'mousedown',
      (event) => {
        const target = event.target as Node;
        if (frame.current?.contains(target) || popup.current?.contains(target)) return;
        setOpen(false);
      },
      { signal: stop.signal },
    );
    return () => stop.abort();
  }, [open]);

  function blocked(day: string): boolean {
    if (min && day < min) return true;
    return Boolean(max && day > max);
  }

  /** Разобрать черновик и, если он годится, отдать наружу. */
  function apply(text: string): boolean {
    const got = parseRu(text);
    if (!got.ok) {
      if (got.reason === 'empty' && allowEmpty) {
        setError(null);
        setDraft(null);
        onChange('');
        return true;
      }
      setError(got.reason === 'empty' ? null : 'Введите корректную дату');
      return got.reason === 'empty';
    }
    if (min && got.value < min) {
      setError(`Дата должна быть не раньше ${toRu(min)}`);
      return false;
    }
    if (max && got.value > max) {
      setError(
        max === now
          ? 'Дата должна быть не позже сегодняшнего дня'
          : `Дата должна быть не позже ${toRu(max)}`,
      );
      return false;
    }
    setError(null);
    setDraft(null);
    onChange(got.value);
    return true;
  }

  function pick(day: string) {
    if (blocked(day)) return;
    setError(null);
    setDraft(null);
    onChange(day);
    setOpen(false);
    field.current?.focus({ preventScroll: true });
  }

  function onFieldKey(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') {
      event.preventDefault();
      // Набранное подтверждается, даже если календарь закрыт. Открывать
      // его вместо применения значило бы потерять то, что человек уже
      // напечатал, ради жеста, о котором он не просил.
      if (draft === null && !open) { setOpen(true); return; }
      if (apply(shown)) setOpen(false);
      return;
    }
    if (event.key === 'Escape' && open) {
      event.preventDefault();
      setOpen(false);
      return;
    }
    if (event.key === 'ArrowDown' && !open) {
      event.preventDefault();
      setOpen(true);
    }
  }

  function onGridKey(event: React.KeyboardEvent) {
    const step: Record<string, number> = {
      ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7,
    };
    if (event.key in step) {
      event.preventDefault();
      setCursor((was) => {
        const next = addDays(was, step[event.key] as number);
        if (!sameMonth(next, anchor)) setAnchor(firstOfMonth(next));
        return next;
      });
      return;
    }
    if (event.key === 'PageUp' || event.key === 'PageDown') {
      event.preventDefault();
      const next = addMonths(cursor, event.key === 'PageUp' ? -1 : 1);
      setCursor(next);
      setAnchor(firstOfMonth(next));
      return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      pick(cursor);
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false);
      field.current?.focus({ preventScroll: true });
    }
  }

  const grid = monthGrid(anchor);

  return (
    <div className="date" ref={frame}>
      <span
        className={
          `date__field${open ? ' date__field--open' : ''}${error ? ' date__field--bad' : ''}`
        }
      >
        <AppIcon name="calendar" size={18} />
        <input
          ref={field}
          className="date__input"
          type="text"
          inputMode="numeric"
          autoComplete="off"
          placeholder="ДД.ММ.ГГГГ"
          aria-label={label}
          aria-expanded={open}
          aria-controls={open ? popupId : undefined}
          aria-invalid={error ? true : undefined}
          disabled={disabled}
          value={shown}
          onChange={(event) => {
            const node = event.target;
            const atEnd = node.selectionStart === node.value.length;
            setDraft(maskRu(node.value, atEnd));
            setError(null);
          }}
          onClick={() => setOpen(true)}
          onBlur={() => { if (draft !== null) apply(draft); }}
          onKeyDown={onFieldKey}
        />
        <button
          type="button"
          className="date__toggle"
          aria-label={open ? 'Закрыть календарь' : 'Открыть календарь'}
          disabled={disabled}
          onClick={() => { setOpen((was) => !was); field.current?.focus({ preventScroll: true }); }}
        >
          <AppIcon name="chevron" size={16} />
        </button>
      </span>

      {error && <span className="date__error" role="alert">{error}</span>}

      {open && createPortal(
        <div
          className="cal"
          id={popupId}
          ref={popup}
          role="dialog"
          aria-label={`Выбор даты: ${label}`}
          style={{ top: place.top, left: place.left }}
          onKeyDown={onGridKey}
        >
          <div className="cal__head">
            <button type="button" className="cal__step" aria-label="Предыдущий месяц"
                    onClick={() => setAnchor(addMonths(anchor, -1))}>
              <AppIcon name="chevron" size={18} />
            </button>
            {/* Заголовок — кнопка: до нужного года иначе приходится
                щёлкать стрелкой по разу на месяц. */}
            <button
              type="button"
              className="cal__title"
              aria-expanded={view === 'years'}
              aria-label={`${monthTitle(anchor)}. Выбрать год`}
              onClick={() => setView((was) => (was === 'days' ? 'years' : 'days'))}
            >
              {monthTitle(anchor)}
              <AppIcon name="chevron" size={16} />
            </button>
            <button type="button" className="cal__step cal__step--next" aria-label="Следующий месяц"
                    onClick={() => setAnchor(addMonths(anchor, 1))}>
              <AppIcon name="chevron" size={18} />
            </button>
          </div>

          {view === 'years' ? (
            <YearGrid
              anchor={anchor}
              now={now}
              onPick={(year) => {
                setAnchor(`${year}${anchor.slice(4)}`);
                setView('days');
              }}
            />
          ) : (
          <>
          <div className="cal__week" aria-hidden="true">
            {WEEKDAYS.map((day) => <span key={day}>{day}</span>)}
          </div>

          <div className="cal__grid" role="grid" aria-label={monthTitle(anchor)} tabIndex={0}>
            {grid.map((day) => {
              const off = !sameMonth(day, anchor);
              const no = blocked(day);
              const on = day === value;
              const inRange = Boolean(rangeStart && rangeEnd && day >= rangeStart && day <= rangeEnd);
              const marks = [
                'cal__day',
                off ? 'cal__day--off' : '',
                inRange ? 'cal__day--range' : '',
                day === rangeStart ? 'cal__day--range-start' : '',
                day === rangeEnd ? 'cal__day--range-end' : '',
                on ? 'cal__day--on' : '',
                day === now ? 'cal__day--now' : '',
                day === cursor ? 'cal__day--cursor' : '',
              ].filter(Boolean).join(' ');
              return (
                <button
                  key={day}
                  type="button"
                  role="gridcell"
                  className={marks}
                  disabled={no}
                  aria-selected={on}
                  aria-current={day === now ? 'date' : undefined}
                  aria-label={toRu(day)}
                  onClick={() => pick(day)}
                >
                  {parts(day)?.[2]}
                </button>
              );
            })}
          </div>

          </>
          )}

          <p className="cal__hint">Можно ввести дату вручную</p>
          <div className="cal__foot">
            <button type="button" className="cal__today" onClick={() => pick(now)}>
              Сегодня
            </button>
            <button
              type="button"
              className="cal__apply"
              onClick={() => { if (apply(shown)) setOpen(false); field.current?.focus({ preventScroll: true }); }}
            >
              Применить
            </button>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}

export { DatePicker as AppDatePicker };

/**
 * Быстрый выбор года: три ряда по четыре.
 *
 * Двенадцать лет на странице, а не длинный список: список на сотню
 * значений приходится прокручивать, и попасть в нужный год в нём не
 * быстрее, чем щёлкать стрелкой по месяцу.
 */
function YearGrid({ anchor, now, onPick }: {
  anchor: string;
  now: string;
  onPick: (year: number) => void;
}) {
  const year = Number(anchor.slice(0, 4));
  const nowYear = Number(now.slice(0, 4));
  const [page, setPage] = useState(() => year - ((year % 12) + 12) % 12);
  const years = Array.from({ length: 12 }, (_, i) => page + i);

  return (
    <div className="cal__years">
      <div className="cal__years-head">
        <button type="button" className="cal__step" aria-label="Предыдущие годы"
                onClick={() => setPage((was) => was - 12)}>
          <AppIcon name="chevron" size={18} />
        </button>
        <span>{page} — {page + 11}</span>
        <button type="button" className="cal__step cal__step--next" aria-label="Следующие годы"
                onClick={() => setPage((was) => was + 12)}>
          <AppIcon name="chevron" size={18} />
        </button>
      </div>
      <div className="cal__years-grid" role="listbox" aria-label="Год">
        {years.map((item) => (
          <button
            key={item}
            type="button"
            role="option"
            aria-selected={item === year}
            className={
              `cal__year${item === year ? ' cal__year--on' : ''}` +
              (item === nowYear && item !== year ? ' cal__year--now' : '')
            }
            onClick={() => onPick(item)}
          >
            {item}
          </button>
        ))}
      </div>
    </div>
  );
}
