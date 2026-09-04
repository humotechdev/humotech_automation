/**
 * Наложения: нижняя шторка и подтверждение.
 *
 * Обе закрывают собой экран, поэтому обе обязаны уметь закрываться
 * тремя способами: кнопкой, касанием подложки и Escape. Шторка, из
 * которой не выйти, — это приложение, из которого выходят целиком.
 *
 * Фокус уводится внутрь при открытии и возвращается на место при
 * закрытии: иначе после закрытия шторки клавиатурный фокус остаётся
 * на элементе, которого больше нет на экране.
 *
 * Третий способ — родная кнопка «назад» Telegram. Без неё на Android
 * системная «назад» закрывает не шторку, а всё приложение: заполненная
 * заявка вместе с прикреплённой справкой пропадает, и человек начинает
 * заново, не понимая, что произошло.
 */

import { useEffect, useRef, type ReactNode } from 'react';

import { backButton } from '../telegram';
import { CloseIcon } from './icons';
import { IconButton, PrimaryButton, SecondaryButton } from './primitives';

function useDismiss(open: boolean, onClose: () => void) {
  const restoreTo = useRef<HTMLElement | null>(null);

  // Обработчик берётся через ссылку, а не из зависимостей: `onClose`
  // почти всегда стрелка прямо в разметке, и новая на каждый отрисованный
  // кадр перезапускала бы эффект — то есть возвращала бы фокус и
  // переподключала кнопку «назад» непрерывно, пока шторка открыта.
  const latest = useRef(onClose);
  latest.current = onClose;

  useEffect(() => {
    if (!open) return undefined;
    restoreTo.current = document.activeElement as HTMLElement | null;

    const close = () => latest.current();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKey);
    const detach = backButton(close);

    return () => {
      document.removeEventListener('keydown', onKey);
      detach();
      restoreTo.current?.focus?.();
    };
  }, [open]);
}

/**
 * Форма во весь низ экрана.
 *
 * Именно снизу, а не по центру: приложением пользуются одной рукой, и
 * до верха экрана телефона большим пальцем не достать.
 */
export function BottomSheet({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useDismiss(open, onClose);
  if (!open) return null;

  return (
    <div className="overlay">
      {/* Подложка — кнопка, а не div с onClick: так до неё добирается
          клавиатура и объявляет диктор. */}
      <button
        type="button"
        className="overlay-backdrop"
        aria-label="Закрыть"
        onClick={onClose}
      />
      <div
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="sheet-head">
          <h2>{title}</h2>
          <IconButton
            label="Закрыть"
            icon={<CloseIcon size={20} />}
            onClick={onClose}
          />
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>
  );
}

/**
 * Подтверждение перед необратимым.
 *
 * Своё, а не `window.confirm`: системный диалог в вебвью Telegram
 * выглядит чужим, не переводится и на части клиентов блокирует всё
 * приложение до ответа.
 */
export function ConfirmationDialog({
  open,
  title,
  description,
  confirmLabel = 'Подтвердить',
  cancelLabel = 'Отмена',
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  // Пока запрос в пути, закрывать нечего: кнопка отмены отключена, и
  // «назад» ведёт себя так же — иначе диалог исчез бы, а заявка ушла.
  useDismiss(open, busy ? noop : onCancel);
  if (!open) return null;

  return (
    <div className="overlay">
      <button
        type="button"
        className="overlay-backdrop"
        aria-label={cancelLabel}
        onClick={onCancel}
      />
      <div
        className="dialog"
        role="alertdialog"
        aria-modal="true"
        aria-label={title}
      >
        <h2>{title}</h2>
        {description && <p className="muted">{description}</p>}
        <div className="dialog-actions">
          <SecondaryButton onClick={onCancel} disabled={busy} wide>
            {cancelLabel}
          </SecondaryButton>
          <PrimaryButton onClick={onConfirm} disabled={busy} wide>
            {busy ? 'Отправляем…' : confirmLabel}
          </PrimaryButton>
        </div>
      </div>
    </div>
  );
}

function noop(): void {
  // Осознанно ничего: см. `busy` в `ConfirmationDialog`.
}
