/**
 * Переключатель языка.
 *
 * Язык сейчас один — русский, и это честное состояние, а не заглушка:
 * список открывается, показывает выбранный язык и закрывается. Как
 * только появится второй перевод, сюда добавится строка, и ничего
 * больше менять не придётся.
 */

import { useEffect, useRef, useState } from 'react';

import { AppIcon, ICON_SIZE } from './AppIcon';

const LANGUAGES = [{ code: 'RU', title: 'Русский' }] as const;

export function LanguageSwitch() {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const stop = new AbortController();
    const outside = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', outside, { signal: stop.signal });
    document.addEventListener('keydown', escape, { signal: stop.signal });
    return () => stop.abort();
  }, [open]);

  return (
    <div className="lang" ref={box}>
      <button
        type="button"
        className="lang__button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Язык интерфейса: русский"
        onClick={() => setOpen((was) => !was)}
      >
        <AppIcon name="globe" size={ICON_SIZE.action} className="lang__globe" />
        <span>RU</span>
        <AppIcon name="chevron" size={16} className="lang__chevron" />
      </button>

      {open && (
        <ul className="lang__list" role="listbox" aria-label="Язык интерфейса">
          {LANGUAGES.map((language) => (
            <li key={language.code}>
              <button
                type="button"
                role="option"
                aria-selected={true}
                className="lang__option"
                onClick={() => setOpen(false)}
              >
                {language.title}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
