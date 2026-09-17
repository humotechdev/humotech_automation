import { Children, isValidElement, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { AppIcon } from './AppIcon';

export type AppSelectOption = { value: string; label: string; disabled?: boolean };
type SelectProps = {
  label: string;
  value: string;
  options: AppSelectOption[];
  onChange: (value: string) => void;
  empty?: string;
  searchable?: boolean;
  disabled?: boolean;
  error?: boolean;
  className?: string;
  minWidth?: number;
};

/** Shared, keyboard-accessible select used across HR screens and dialogs. */
export function AppSelect({ label, value, options, onChange, empty = 'Выберите…', searchable, disabled, error, className = '', minWidth = 172 }: SelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [place, setPlace] = useState({ top: 0, left: 0, width: minWidth, maxHeight: 280 });
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  const all = useMemo(() => [{ value: '', label: empty }, ...options], [empty, options]);
  const filtered = useMemo(() => {
    const text = query.trim().toLocaleLowerCase('ru');
    return all.filter((option) => !text || option.label.toLocaleLowerCase('ru').includes(text));
  }, [all, query]);
  const selected = all.find((option) => option.value === value) ?? all[0];
  const useSearch = searchable ?? options.length > 8;

  useLayoutEffect(() => {
    if (!open) return;
    const position = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.max(rect.width, Math.min(minWidth, window.innerWidth - 24));
      const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
      const roomBelow = window.innerHeight - rect.bottom - 8;
      const roomAbove = rect.top - 8;
      const maxHeight = Math.max(120, Math.min(320, Math.max(roomBelow, roomAbove)));
      const top = roomBelow >= Math.min(280, maxHeight) || roomBelow >= roomAbove
        ? rect.bottom + 6
        : Math.max(8, rect.top - Math.min(280, maxHeight) - 6);
      setPlace({ top, left, width, maxHeight });
    };
    position();
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    return () => {
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
    };
  }, [open, minWidth]);

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!root.current?.contains(target) && !menu.current?.contains(target)) setOpen(false);
    };
    const closeEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        trigger.current?.focus({ preventScroll: true });
      }
    };
    document.addEventListener('mousedown', closeOutside);
    document.addEventListener('keydown', closeEscape);
    return () => {
      document.removeEventListener('mousedown', closeOutside);
      document.removeEventListener('keydown', closeEscape);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const index = filtered.findIndex((option) => option.value === value);
    setActive(Math.max(index, 0));
  }, [open, value, filtered.length]);
  useEffect(() => {
    if (!open) return;
    const option = menu.current?.querySelectorAll<HTMLElement>('[role="option"]')[active];
    const list = option?.parentElement;
    if (!option || !list) return;
    // Своя арифметика вместо `scrollIntoView`: тот прокручивает всех
    // предков до окна включительно, и выбор стрелками уводил бы саму
    // страницу. Здесь двигается только список опций.
    const top = option.offsetTop;
    const bottom = top + option.offsetHeight;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (bottom > list.scrollTop + list.clientHeight) {
      list.scrollTop = bottom - list.clientHeight;
    }
  }, [open, active]);

  function move(delta: number) {
    if (!filtered.length) return;
    let next = active;
    for (let i = 0; i < filtered.length; i += 1) {
      next = (next + delta + filtered.length) % filtered.length;
      if (!filtered[next]?.disabled) break;
    }
    setActive(next);
  }
  function choose(option: AppSelectOption | undefined) {
    if (!option || option.disabled) return;
    onChange(option.value);
    setOpen(false);
    setQuery('');
    trigger.current?.focus({ preventScroll: true });
  }
  function onTriggerKey(event: React.KeyboardEvent<HTMLButtonElement>) {
    if (open && event.key === 'ArrowDown') { event.preventDefault(); move(1); return; }
    if (open && event.key === 'ArrowUp') { event.preventDefault(); move(-1); return; }
    if (open && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); choose(filtered[active]); return; }
    if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      setOpen(true);
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen(true);
      setActive(Math.max(filtered.length - 1, 0));
    }
  }

  return (
    <div className={`app-select ${className}`} ref={root}>
      <button ref={trigger} type="button" className={`app-select__trigger${open ? ' app-select__trigger--open' : ''}${error ? ' app-select__trigger--error' : ''}`}
        aria-label={`${label}: ${selected?.label ?? empty}`} aria-haspopup="listbox" aria-expanded={open}
        aria-activedescendant={open ? `${id}-option-${active}` : undefined}
        aria-controls={open ? id : undefined} disabled={disabled} onClick={() => setOpen((was) => !was)} onKeyDown={onTriggerKey}>
        <span className="app-select__value">{selected?.label || empty}</span><AppIcon name="chevron" size={16} />
      </button>
      {open && createPortal(
        <div ref={menu} id={id} className="app-select__menu" role="listbox" aria-label={label} style={{ top: place.top, left: place.left, width: place.width, maxHeight: place.maxHeight }}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown') { event.preventDefault(); move(1); }
            if (event.key === 'ArrowUp') { event.preventDefault(); move(-1); }
            if (event.key === 'Home') { event.preventDefault(); setActive(0); }
            if (event.key === 'End') { event.preventDefault(); setActive(filtered.length - 1); }
            if (event.key === 'Enter') { event.preventDefault(); choose(filtered[active]); }
          }}>
          {useSearch && <label className="app-select__search"><AppIcon name="search" size={16} /><input autoFocus value={query} aria-label={`Поиск: ${label}`} onChange={(event) => { setQuery(event.target.value); setActive(0); }} /></label>}
          <div className="app-select__options" style={{ maxHeight: useSearch ? place.maxHeight - 48 : place.maxHeight }}>
            {filtered.length ? filtered.map((option, index) => <button type="button" role="option" id={`${id}-option-${index}`} key={option.value || '__all'} aria-selected={option.value === value}
              disabled={option.disabled} className={`app-select__option${index === active ? ' app-select__option--active' : ''}${option.value === value ? ' app-select__option--selected' : ''}`}
              onMouseEnter={() => setActive(index)} onClick={() => choose(option)}>{option.label}{option.value === value && <AppIcon name="check" size={16} />}</button>)
              : <p className="app-select__empty">Ничего не найдено</p>}
          </div>
        </div>, document.body,
      )}
    </div>
  );
}

/** Existing dashboard API retained; implementation is shared with AppSelect. */
export function Dropdown({ label, value, empty, options, onChange }: { label: string; value: string; empty: string; options: { id: string; name: string }[]; onChange: (value: string) => void }) {
  return <AppSelect label={label} value={value} empty={empty} options={options.map(({ id, name }) => ({ value: id, label: name }))} onChange={onChange} />;
}

export function AppSearchSelect(props: Omit<SelectProps, 'searchable'>) {
  return <AppSelect {...props} searchable />;
}

/** Adapter for existing option-list markup; keeps page data and API logic unchanged. */
export function AppSelectField({ label, value, onChange, children, className = '', disabled, searchable }: {
  label: string; value: string; onChange: (value: string) => void; children: ReactNode; className?: string; disabled?: boolean; searchable?: boolean;
}) {
  const childrenArray = Children.toArray(children).filter(isValidElement);
  const options = childrenArray.flatMap((child) => {
    if (child.type !== 'option') return [];
    const option = child as ReactElement<{ value?: string; disabled?: boolean; children?: ReactNode }>;
    const optionValue = option.props.value ?? '';
    return optionValue ? [{ value: optionValue, label: Children.toArray(option.props.children).join(''), ...(option.props.disabled ? { disabled: true } : {}) }] : [];
  });
  const emptyChild = childrenArray.find((child) => child.type === 'option' && (child.props as { value?: string }).value === '');
  const empty = emptyChild ? Children.toArray((emptyChild as ReactElement<{ children?: ReactNode }>).props.children).join('') : 'Выберите…';
  const effectiveValue = !emptyChild && !value ? options[0]?.value ?? '' : value;
  return <AppSelect label={label} value={effectiveValue} options={options} empty={empty} onChange={onChange}
    {...(disabled ? { disabled: true } : {})} {...(searchable !== undefined ? { searchable } : {})} className={className} />;
}

export function AppMultiSelect({ label, value, options, onChange, empty = 'Все', disabled, className = '' }: {
  label: string; value: string[]; options: AppSelectOption[]; onChange: (value: string[]) => void; empty?: string; disabled?: boolean; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [place, setPlace] = useState({ top: 0, left: 0, width: 260, maxHeight: 280 });
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  const filtered = options.filter((option) => option.label.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru')));
  useLayoutEffect(() => {
    if (!open) return;
    const position = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.max(rect.width, Math.min(280, window.innerWidth - 24));
      const below = window.innerHeight - rect.bottom - 8;
      const above = rect.top - 8;
      const maxHeight = Math.max(120, Math.min(320, Math.max(below, above)));
      setPlace({ top: below >= above ? rect.bottom + 6 : Math.max(8, rect.top - maxHeight - 6), left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)), width, maxHeight });
    };
    position();
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    return () => { window.removeEventListener('resize', position); window.removeEventListener('scroll', position, true); };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node) && !document.getElementById(id)?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setOpen(false); trigger.current?.focus({ preventScroll: true }); } };
    document.addEventListener('mousedown', outside); document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('mousedown', outside); document.removeEventListener('keydown', escape); };
  }, [open, id]);
  const text = value.length ? options.filter((option) => value.includes(option.value)).map((option) => option.label).join(', ') : empty;
  return <div className={`app-select app-multi ${className}`} ref={root}>
    <button ref={trigger} type="button" className={`app-select__trigger${open ? ' app-select__trigger--open' : ''}`} aria-label={`${label}: ${text}`} aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? id : undefined} disabled={disabled} onClick={() => setOpen((was) => !was)}>
      <span className="app-select__value">{text}</span><AppIcon name="chevron" size={16} />
    </button>
    {open && createPortal(<div className="app-select__menu app-multi__menu" id={id} role="listbox" aria-label={label} aria-multiselectable="true" style={{ top: place.top, left: place.left, width: place.width, maxHeight: place.maxHeight }}>
      <label className="app-select__search"><AppIcon name="search" size={16} /><input autoFocus value={query} aria-label={`Поиск: ${label}`} onChange={(event) => setQuery(event.target.value)} /></label>
      <label className="app-multi__option"><input type="checkbox" checked={!value.length} onChange={() => onChange([])} />{empty}</label>
      <div className="app-select__options" style={{ maxHeight: place.maxHeight - 92 }}>{filtered.map((option) => <label className="app-multi__option" key={option.value}><input type="checkbox" checked={value.includes(option.value)} onChange={() => onChange(value.includes(option.value) ? value.filter((one) => one !== option.value) : [...value, option.value])} />{option.label}</label>)}{!filtered.length && <p className="app-select__empty">Ничего не найдено</p>}</div>
    </div>, document.body)}
  </div>;
}

export function AppFilterButton({ active, children, onClick, count, disabled, className = '' }: { active: boolean; children: ReactNode; onClick: () => void; count?: ReactNode; disabled?: boolean; className?: string }) {
  return <button type="button" className={`app-filter${active ? ' app-filter--active' : ''} ${className}`} aria-pressed={active} disabled={disabled} onClick={onClick}>{children}{count !== undefined && <span>{count}</span>}</button>;
}

export function AppSegmentedControl<T extends string | number>({ label, value, options, onChange, className = '', role = 'group' }: { label: string; value: T | null; options: { value: T; label: string; count?: ReactNode; icon?: import('./AppIcon').AppIconName }[]; onChange: (value: T) => void; className?: string; role?: 'group' | 'tablist' }) {
  return <div className={`app-segmented ${className}`} role={role} aria-label={label}>{options.map((option) => <button key={option.value} type="button" role={role === 'tablist' ? 'tab' : undefined} aria-selected={role === 'tablist' ? value === option.value : undefined} aria-pressed={role === 'group' ? value === option.value : undefined} className={value === option.value ? 'app-segmented__button app-segmented__button--active' : 'app-segmented__button'} onClick={() => onChange(option.value)}>{option.icon && <AppIcon name={option.icon} size={18} />}{option.label}{option.count !== undefined && <span className="app-segmented__count">{option.count}</span>}</button>)}</div>;
}

export function AppPopover({ open, onClose, children, className = '' }: { open: boolean; onClose: () => void; children: ReactNode; className?: string }) {
  const marker = useRef<HTMLSpanElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState({ top: 0, left: 0 });
  useLayoutEffect(() => {
    if (!open) return;
    const placePopover = () => {
      const anchor = marker.current?.parentElement?.getBoundingClientRect();
      const card = popup.current;
      if (!anchor) return;
      const width = card?.getBoundingClientRect().width || 300;
      const height = card?.getBoundingClientRect().height || 220;
      const room = window.innerHeight - anchor.bottom;
      const top = room >= height + 8 || anchor.top < height + 8 ? anchor.bottom + 6 : Math.max(8, anchor.top - height - 6);
      setPlace({ top, left: Math.max(12, Math.min(anchor.left, window.innerWidth - width - 12)) });
    };
    placePopover();
    window.addEventListener('resize', placePopover);
    window.addEventListener('scroll', placePopover, true);
    return () => { window.removeEventListener('resize', placePopover); window.removeEventListener('scroll', placePopover, true); };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const stop = new AbortController();
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') onClose(); }, { signal: stop.signal });
    document.addEventListener('mousedown', (event) => {
      const target = event.target as Node;
      if (!marker.current?.parentElement?.contains(target) && !popup.current?.contains(target)) onClose();
    }, { signal: stop.signal });
    return () => stop.abort();
  }, [open, onClose]);
  return <>
    <span ref={marker} className="app-popover-anchor" aria-hidden="true" />
    {open && createPortal(<div ref={popup} className={`app-popover ${className}`} role="dialog" data-positioning="true" style={{ position: 'fixed', top: place.top, left: place.left, maxHeight: 'calc(100vh - 16px)', overflowY: 'auto' }}>{children}</div>, document.body)}
  </>;
}
