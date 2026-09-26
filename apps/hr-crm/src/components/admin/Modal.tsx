/**
 * Небольшое окно по центру страницы.
 *
 * Заведение отдела или должности — дело на полминуты, и отдельная
 * страница ради одного поля увела бы человека со списка, к которому он
 * всё равно вернётся.
 *
 * Окно закрывается щелчком по затемнению, крестиком и Escape. Проверять
 * «щелчок вне рамки» нельзя: списки рисуются поверх окна отдельным
 * слоем, и выбор значения считался бы щелчком снаружи — окно
 * закрывалось бы ровно в тот момент, когда человек что-то выбрал.
 */

import { useEffect } from 'react';
import type { ReactNode } from 'react';

import { AppIcon } from '../AppIcon';

export function Modal({ title, onClose, children }: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="adm-modal" role="dialog" aria-modal="true" aria-label={title}
         onMouseDown={(event) => {
           if (event.target === event.currentTarget) onClose();
         }}>
      <div className="adm-modal__box">
        <header className="adm-modal__head">
          <h2 className="adm-modal__title">{title}</h2>
          <button type="button" className="adm-modal__close" aria-label="Закрыть"
                  onClick={onClose}>
            <AppIcon name="close" size={18} />
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}

/**
 * Поле окна: подпись, само поле и — при ошибке — причина отказа.
 *
 * Подсказка и ошибка стоят ВНЕ `label`: внутри они попадали бы в его
 * текст, и подписью поля становилось бы «Пароль Придумайте пароль».
 */
export function ModalField({ label, error, hint, children }: {
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

/** Нижний ряд окна: отмена и главное действие. */
export function ModalTools({ busy, submitLabel, onCancel }: {
  busy: boolean;
  submitLabel: string;
  onCancel: () => void;
}) {
  return (
    <div className="adm-modal__tools">
      <button type="button" className="btn" onClick={onCancel} disabled={busy}>
        Отмена
      </button>
      <button type="submit" className="btn btn--primary" disabled={busy}>
        {busy ? 'Сохраняем…' : submitLabel}
      </button>
    </div>
  );
}
