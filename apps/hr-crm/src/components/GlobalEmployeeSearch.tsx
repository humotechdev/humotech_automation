import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';

import * as api from '../api/crm';
import { AppIcon, ICON_SIZE } from './AppIcon';
import { employmentTitle, isWorking } from '../features/employees/status';

export function normalizeEmployeeSearch(value: string): string {
  return value.trim().toLocaleLowerCase('ru-RU').replaceAll('ё', 'е').replace(/\s+/g, ' ');
}

function initials(name: string) {
  return name.split(/\s+/).slice(0, 2).map((part) => part[0] ?? '').join('').toUpperCase();
}

export function GlobalEmployeeSearch() {
  const [value, setValue] = useState('');
  const [results, setResults] = useState<api.EmployeeSearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [active, setActive] = useState(0);
  const [retryNonce, setRetryNonce] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [panelPosition, setPanelPosition] = useState({ top: 0, left: 0, width: 530 });
  const listId = useId();
  const navigate = useNavigate();
  const normalized = normalizeEmployeeSearch(value);
  const showPanel = open && normalized.length > 0;

  useEffect(() => {
    if (normalized.length < 2) {
      setLoading(false);
      setError(false);
      setResults([]);
      setActive(0);
      return;
    }
    setLoading(true);
    setError(false);
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void api.searchEmployees(normalized, controller.signal)
        .then(({ items }) => {
          if (controller.signal.aborted) return;
          setResults(items);
          setActive(0);
        })
        .catch(() => {
          if (!controller.signal.aborted) setError(true);
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 280);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [normalized, retryNonce]);

  useEffect(() => {
    if (!showPanel) return;
    const placePanel = () => {
      const rect = input.current?.getBoundingClientRect();
      if (!rect) return;
      const width = Math.min(530, window.innerWidth - 32);
      setPanelPosition({
        top: rect.bottom + 9,
        left: Math.max(16, Math.min(rect.right - width, window.innerWidth - width - 16)),
        width,
      });
    };
    placePanel();
    window.addEventListener('resize', placePanel);
    window.addEventListener('scroll', placePanel, true);
    return () => {
      window.removeEventListener('resize', placePanel);
      window.removeEventListener('scroll', placePanel, true);
    };
  }, [showPanel]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    document.addEventListener('mousedown', (event) => {
      const target = event.target as Node;
      if (!box.current?.contains(target) && !panel.current?.contains(target)) setOpen(false);
    }, { signal: controller.signal });
    return () => controller.abort();
  }, [open]);

  const choose = (row: api.EmployeeSearchResult) => {
    setOpen(false);
    setValue('');
    // Карточка уже имеет собственный маршрут; не создаём второй вариант URL.
    navigate(`/employees/${row.id}`);
  };

  const retry = () => {
    if (normalized.length < 2) return;
    setRetryNonce((current) => current + 1);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!open) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false);
      input.current?.blur();
    } else if (event.key === 'ArrowDown' && results.length) {
      event.preventDefault();
      setActive((current) => (current + 1) % results.length);
    } else if (event.key === 'ArrowUp' && results.length) {
      event.preventDefault();
      setActive((current) => (current - 1 + results.length) % results.length);
    } else if (event.key === 'Enter' && results[active]) {
      event.preventDefault();
      choose(results[active]);
    }
  };

  return (
    <div className="global-search" ref={box}>
      <label className="find">
        <AppIcon name="search" size={ICON_SIZE.action} />
        <input
          ref={input}
          type="search"
          placeholder="Поиск по сотрудникам, отделам, опросам…"
          value={value}
          onFocus={() => setOpen(true)}
          onChange={(event) => { setValue(event.target.value); setOpen(true); }}
          onKeyDown={onKeyDown}
          aria-label="Поиск сотрудника"
          role="combobox"
          aria-expanded={showPanel}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={results[active] ? `${listId}-${results[active].id}` : undefined}
        />
      </label>
      {showPanel && createPortal(
        <div
          ref={panel}
          className="global-search__panel"
          id={listId}
          role="listbox"
          aria-label="Результаты поиска сотрудников"
          style={{ top: panelPosition.top, left: panelPosition.left, width: panelPosition.width }}
        >
          {normalized.length < 2 ? (
            <p className="global-search__message">Введите минимум 2 символа</p>
          ) : error ? (
            <p className="global-search__message" role="alert">Не удалось выполнить поиск. <button type="button" onClick={retry}>Повторить</button></p>
          ) : (
            <>
              <div className="global-search__heading"><strong>Лучшие совпадения</strong><span>{results.length} результатов</span></div>
              {loading && results.length === 0 ? <SearchSkeleton /> : results.length === 0 && !loading ? (
                <p className="global-search__message">Сотрудники не найдены</p>
              ) : results.map((row, index) => (
                <button
                  type="button"
                  key={row.id}
                  id={`${listId}-${row.id}`}
                  role="option"
                  aria-selected={index === active}
                  className={index === active ? 'global-search__row global-search__row--active' : 'global-search__row'}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => choose(row)}
                >
                  <span className="global-search__avatar" aria-hidden="true">
                    {row.photo ? <img src={api.employeePhotoUrl(row.id)} alt="" /> : initials(row.full_name)}
                  </span>
                  <span className="global-search__identity">
                    <strong>{row.full_name}</strong>
                    <small>{row.position_name || 'Должность не назначена'}</small>
                  </span>
                  <span className="global-search__place">
                    {row.office_name && <small>{row.office_name}</small>}
                    {!isWorking(row.employment_status) && <em>{employmentTitle(row.employment_status)}</em>}
                  </span>
                  <AppIcon name="chevron" size={16} />
                </button>
              ))}
              <div className="global-search__footer"><span>Не нашли сотрудника? Уточните имя, должность или Telegram</span><kbd>↑↓</kbd><span>выбрать</span><kbd>Enter</kbd><span>открыть</span><kbd>Esc</kbd></div>
            </>
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}

function SearchSkeleton() {
  return <div className="global-search__skeleton" aria-label="Поиск сотрудников"><i /><i /><i /></div>;
}
