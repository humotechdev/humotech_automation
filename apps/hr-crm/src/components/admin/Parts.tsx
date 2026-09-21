/**
 * Общие части разделов «Администрирования».
 *
 * Все шесть списков устроены одинаково: шапка с возвратом на стартовую
 * страницу, поиск, список, боковая форма и подтверждение опасного
 * действия. Повторять это пять раз значит получить пять слегка разных
 * подтверждений — и однажды одно из них без вопроса выключит отдел с
 * людьми.
 */

import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { ApiFailure, messageFor } from '../../api/errors';
import { AppIcon } from '../AppIcon';

/** Шапка раздела: куда вернуться, что это и главное действие. */
export function SectionHead({ title, about, action }: {
  title: string;
  about: string;
  action?: ReactNode;
}) {
  return (
    <header className="head head--tight adm-head">
      <div>
        <p className="adm-head__up">
          <Link to="/administration" className="link adm-back">
            <AppIcon name="back" size={16} /> Администрирование
          </Link>
        </p>
        <h1 className="head__title">{title}</h1>
        <p className="head__sub">{about}</p>
      </div>
      {action && <div className="head__actions">{action}</div>}
    </header>
  );
}

/** Поиск и отбор по состоянию — одной строкой над списком. */
export function SectionBar({ search, onSearch, placeholder, children }: {
  search: string;
  onSearch: (value: string) => void;
  placeholder: string;
  children?: ReactNode;
}) {
  return (
    <div className="toolbar adm-bar">
      <label className="find find--wide">
        <AppIcon name="search" size={16} />
        <input
          type="search"
          value={search}
          placeholder={placeholder}
          aria-label={placeholder}
          onChange={(event) => onSearch(event.target.value)}
        />
      </label>
      {children}
    </div>
  );
}

/**
 * Пустое место со своей причиной.
 *
 * «Ничего не найдено» и «здесь пока пусто» — разные сообщения: первое
 * значит, что надо изменить запрос, второе — что надо завести первую
 * запись. Общая фраза на оба случая отправляет человека не туда.
 */
export function Empty({ filtered, nothing, none, action }: {
  filtered: boolean;
  /** Текст, когда отбор ничего не дал. */
  nothing: string;
  /** Текст, когда записей нет вообще. */
  none: string;
  action?: ReactNode;
}) {
  return (
    <div className="adm-empty">
      <span className="adm-empty__icon" aria-hidden="true">
        <AppIcon name={filtered ? 'search' : 'plus'} size={20} />
      </span>
      <p className="adm-empty__text">{filtered ? nothing : none}</p>
      {!filtered && action}
    </div>
  );
}

/** Состояние загрузки и сбоя — одинаково во всех разделах. */
export function Loading() {
  return <p className="empty">Загружаем…</p>;
}

export function Failed({ onRetry }: { onRetry: () => void }) {
  return (
    <p className="empty empty--bad">
      Не удалось загрузить список. Это ошибка запроса, а не пустой список.{' '}
      <button type="button" className="link" onClick={onRetry}>
        Повторить
      </button>
    </p>
  );
}

/**
 * Боковая форма. Закрывается крестиком, Escape и щелчком вне неё —
 * всеми тремя способами, которых человек ждёт от такой панели.
 */
export function SidePanel({ title, onClose, children }: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    const onDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target || !box.current || box.current.contains(target)) return;
      // Списки и всплывающие окна рисуются поверх панели отдельным
      // слоем, вне её рамки. Без этой проверки выбор значения в списке
      // считался бы щелчком снаружи — панель закрывалась бы ровно в тот
      // момент, когда человек что-то выбрал.
      if (target.closest?.('.app-select__menu, .app-multi__menu, .app-popover')) {
        return;
      }
      // Щелчок по элементу, который уже убрали из документа (пункт
      // закрывшегося списка), «вне панели» тоже не считается.
      if (target.isConnected) onClose();
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onDown);
    };
  }, [onClose]);

  return (
    <section className="panel panel--view adm-side" ref={box} aria-label={title}>
      <header className="adm-side__head">
        <h2 className="adm-side__title">{title}</h2>
        <button type="button" className="tool tool--ghost" aria-label="Закрыть"
                onClick={onClose}>
          <AppIcon name="close" size={16} />
        </button>
      </header>
      <div className="adm-side__body">{children}</div>
    </section>
  );
}

/**
 * Поле формы с подписью и — при ошибке — причиной отказа.
 *
 * Подсказка и ошибка стоят ВНЕ элемента `label`. Внутри они попадали бы
 * в его текст, и подписью поля становилось бы «Новый пароль
 * Проверяется правилами Django…» — то есть у поля не было бы имени ни
 * для человека со средством чтения с экрана, ни для теста.
 */
export function Field({ label, error, hint, children }: {
  label: string;
  error?: string | undefined;
  hint?: string | undefined;
  children: ReactNode;
}) {
  return (
    <div className={error ? 'adm-field adm-field--bad' : 'adm-field'}>
      <label className="adm-field__row">
        <span className="adm-field__label">{label}</span>
        {children}
      </label>
      {error && <span className="adm-field__error" role="alert">{error}</span>}
      {!error && hint && <span className="adm-field__hint">{hint}</span>}
    </div>
  );
}

/**
 * Подтверждение действия, которое трудно отменить.
 *
 * Спрашивается не «уверены ли вы», а что именно произойдёт: человек
 * соглашается с последствием, а не с вопросом.
 */
export function Confirm({ title, what, consequence, confirmLabel, busy, refusal, onConfirm, onCancel }: {
  title: string;
  what: string;
  consequence?: string | undefined;
  confirmLabel: string;
  busy?: boolean;
  /** Отказ сервера показывается здесь же: окно остаётся открытым. */
  refusal?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel]);

  return (
    <div className="adm-ask" role="dialog" aria-modal="true" aria-label={title}>
      <div className="adm-ask__box">
        <h2 className="adm-ask__title">{title}</h2>
        <p className="adm-ask__what">{what}</p>
        {consequence && <p className="adm-ask__note">{consequence}</p>}
        <Refusal text={refusal ?? null} />
        <div className="adm-ask__tools">
          <button type="button" className="btn" onClick={onCancel} disabled={busy}>
            Отмена
          </button>
          <button type="button" className="btn btn--primary" onClick={onConfirm}
                  disabled={busy}>
            {busy ? 'Выполняем…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Отказ сервера рядом с формой.
 *
 * Показывается ровно то, что ответил сервер: собственная догадка о
 * причине отказа расходится с настоящей ровно тогда, когда она нужнее
 * всего.
 */
export function Refusal({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <p className="adm-refusal" role="alert">
      <AppIcon name="alert" size={16} /> {text}
    </p>
  );
}

/**
 * Причина отказа человеческим текстом.
 *
 * Сначала — то, что написал сервер: «в отделе ещё числятся семеро»
 * объясняет отказ, а «конфликт» не объясняет ничего. Общая фраза по
 * виду ошибки остаётся запасным вариантом на случай, когда сервер
 * причины не назвал.
 */
export function refusalText(error: unknown): string {
  if (error instanceof ApiFailure) return error.detail ?? messageFor(error);
  return messageFor(error);
}

/** Состояние «сохраняем» с защитой от двойного нажатия. */
export function useSaving() {
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  const run = async (action: () => Promise<unknown>, done?: () => void) => {
    if (busy) return;
    setBusy(true);
    setRefusal(null);
    try {
      await action();
      done?.();
    } catch (error) {
      setRefusal(refusalText(error));
    } finally {
      setBusy(false);
    }
  };

  return { busy, refusal, setRefusal, run };
}
