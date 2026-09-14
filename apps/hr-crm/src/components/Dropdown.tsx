/**
 * Выпадающий список в оформлении CRM.
 *
 * Родной `<select>` рисует свой список средствами системы: он не берёт
 * ни шрифт, ни цвета, ни скругления страницы и появляется мгновенно,
 * как окно из другой программы. Поэтому список здесь свой.
 *
 * Взамен приходится вернуть то, что у `<select>` было бесплатно, и это
 * не украшения, а условие работоспособности:
 *
 * - клавиатура: стрелки водят по пунктам, Enter выбирает, Escape
 *   закрывает и возвращает фокус на кнопку;
 * - роли `combobox`/`listbox`/`option` и `aria-activedescendant`, чтобы
 *   экранный диктор называл и список, и текущий пункт;
 * - закрытие по клику мимо и по уходу фокуса за пределы списка.
 *
 * Появление анимировано, но анимация выключается при
 * `prefers-reduced-motion`: для части людей движение на экране — не
 * приятная мелочь, а причина закрыть страницу.
 */

import { useEffect, useId, useRef, useState } from 'react';

import { AppIcon } from './AppIcon';

export type Option = { id: string; name: string };

type Props = {
  /** Подпись для диктора: на экране её нет, у поля только значение. */
  label: string;
  value: string;
  /** Что показывать и что значит пустое значение. */
  empty: string;
  options: Option[];
  onChange: (value: string) => void;
};

export function Dropdown({ label, value, empty, options, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const listId = useId();

  const all: Option[] = [{ id: '', name: empty }, ...options];
  const current = all.find((item) => item.id === value) ?? all[0];

  useEffect(() => {
    if (!open) return;
    const stop = new AbortController();
    document.addEventListener(
      'mousedown',
      (event) => {
        if (!box.current?.contains(event.target as Node)) setOpen(false);
      },
      { signal: stop.signal },
    );
    return () => stop.abort();
  }, [open]);

  // Выбранный пункт должен быть виден сразу: в длинном списке офисов
  // он иначе оказывается за пределами прокрутки.
  useEffect(() => {
    if (!open || !list.current) return;
    const node = list.current.children[active] as HTMLElement | undefined;
    node?.scrollIntoView({ block: 'nearest' });
  }, [open, active]);

  function show() {
    setActive(Math.max(all.findIndex((item) => item.id === value), 0));
    setOpen(true);
  }

  function choose(index: number) {
    const picked = all[index];
    if (picked) onChange(picked.id);
    setOpen(false);
  }

  function onKey(event: React.KeyboardEvent) {
    if (!open) {
      if (event.key === 'Enter' || event.key === ' ' || event.key === 'ArrowDown') {
        event.preventDefault();
        show();
      }
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false);
      return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      choose(active);
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive((was) => (was + step + all.length) % all.length);
      return;
    }
    if (event.key === 'Home') {
      event.preventDefault();
      setActive(0);
    }
    if (event.key === 'End') {
      event.preventDefault();
      setActive(all.length - 1);
    }
  }

  return (
    <div className="drop" ref={box}>
      <button
        type="button"
        className={open ? 'pick pick--drop pick--open' : 'pick pick--drop'}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={`${label}: ${current?.name ?? empty}`}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={onKey}
      >
        <span className="drop__value">{current?.name ?? empty}</span>
        <AppIcon name="chevron" size={18} />
      </button>

      {open && (
        <ul
          className="drop__list"
          id={listId}
          role="listbox"
          ref={list}
          aria-label={label}
          aria-activedescendant={`${listId}-${active}`}
          tabIndex={-1}
        >
          {all.map((item, index) => (
            <li key={item.id || 'all'}>
              <button
                type="button"
                id={`${listId}-${index}`}
                role="option"
                aria-selected={item.id === value}
                className={
                  index === active ? 'drop__option drop__option--on' : 'drop__option'
                }
                onMouseEnter={() => setActive(index)}
                onClick={() => choose(index)}
              >
                {item.name}
                {item.id === value && <AppIcon name="check" size={16} />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
