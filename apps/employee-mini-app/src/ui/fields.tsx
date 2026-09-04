/**
 * Поля форм.
 *
 * Календарь и загрузка файла — родные, `input[type=date]` и
 * `input[type=file]`. Библиотека календаря весит больше, чем весь этот
 * кабинет, а системный выбор дат человек уже знает, он переведён на его
 * язык и работает с экранным диктором без единой строки от нас.
 *
 * У каждого поля есть подпись, связанная через `htmlFor`, и ошибка
 * рядом с самим полем, а не общим списком вверху формы: искать, к чему
 * относится «поле заполнено неверно», человек не должен.
 */

import { useId, useRef, useState, type ReactNode } from 'react';

import { CameraCapture, supportsCamera } from './CameraCapture';
import { CameraIcon, DocumentIcon } from './icons';

/** Общая обвязка поля: подпись, подсказка, ошибка. */
export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
  htmlFor: string;
}) {
  return (
    <div className={`field${error ? ' field-invalid' : ''}`}>
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && !error && <p className="field-hint">{hint}</p>}
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/**
 * Две даты периода.
 *
 * Обёртка вокруг пары родных полей, а не свой календарь: смысл в том,
 * чтобы конец не оказался раньше начала (`min`) и чтобы количество
 * рабочих дней было видно сразу под полями.
 */
export function DateRangePicker({
  first,
  last,
  onFirst,
  onLast,
  disabled,
  error,
  footnote,
}: {
  first: string;
  last: string;
  onFirst: (value: string) => void;
  onLast: (value: string) => void;
  disabled?: boolean;
  error?: string;
  /** Например, «10 рабочих дней» — считает сервер, показываем его число. */
  footnote?: ReactNode;
}) {
  const id = useId();
  const firstId = `${id}-first`;
  const lastId = `${id}-last`;

  return (
    <div className="date-range">
      <div className="date-range-row">
        <Field label="С какого дня" htmlFor={firstId}>
          <input
            id={firstId}
            type="date"
            value={first}
            onChange={(event) => onFirst(event.target.value)}
            disabled={disabled}
            required
          />
        </Field>
        <Field label="По какой день" htmlFor={lastId} error={error}>
          <input
            id={lastId}
            type="date"
            value={last}
            min={first}
            onChange={(event) => onLast(event.target.value)}
            disabled={disabled}
            required
          />
        </Field>
      </div>
      {footnote && <p className="date-range-note">{footnote}</p>}
    </div>
  );
}

/**
 * Загрузка справки: снять камерой или выбрать файл.
 *
 * Панель вложений Telegram здесь недоступна и не появится: в WebApp API
 * есть только `downloadFile` — отдать файл человеку. Взять файл из чата
 * или открыть телеграмную «скрепку» мини-приложению нечем, такого метода
 * в API нет.
 *
 * «Выбрать файл» — родной `input[type=file]`. На Android его перехватывает
 * сам Telegram и показывает своё окно снизу; на iPhone открывается
 * системное. И там, и там есть галерея и файлы.
 *
 * «Сфотографировать» через тот же `input` с `capture` работало только на
 * iPhone: Android-Telegram этот атрибут игнорирует и открывает галерею.
 * Поэтому камера своя (`CameraCapture`), а `input` с `capture` остался
 * запасным путём — на него откатываемся, если камеры нет или к ней не
 * пустили.
 *
 * Скрытые `input` остаются в разметке: с ними работают клавиатура и
 * экранный диктор; спрятан только их вид.
 */
export function FileUploadField({
  file,
  onFile,
  accept,
  hint,
  error,
  disabled,
  required,
}: {
  file: File | null;
  onFile: (file: File | null) => void;
  accept?: string;
  hint?: string;
  error?: string;
  disabled?: boolean;
  required?: boolean;
}) {
  const id = useId();
  const pickId = `${id}-pick`;
  const shotId = `${id}-shot`;
  const shotInput = useRef<HTMLInputElement | null>(null);
  const [camera, setCamera] = useState(false);

  /** Камера не открылась — отдаём человека системному выбору. */
  function fallBackToSystem() {
    setCamera(false);
    shotInput.current?.click();
  }

  return (
    <Field
      label={`Справка${required ? '' : ' (можно приложить позже)'}`}
      htmlFor={pickId}
      hint={hint}
      error={error}
    >
      {camera && (
        <CameraCapture
          onShot={(shot) => {
            setCamera(false);
            onFile(shot);
          }}
          onCancel={() => setCamera(false)}
          onUnavailable={fallBackToSystem}
        />
      )}

      {file ? (
        <div className="file-chosen">
          <DocumentIcon size={20} />
          <span className="file-name">{file.name}</span>
          <button
            type="button"
            className="file-clear"
            onClick={() => onFile(null)}
            disabled={disabled}
          >
            Убрать
          </button>
        </div>
      ) : (
        <div className="file-actions">
          <button
            type="button"
            className="file-field"
            disabled={disabled}
            onClick={() => {
              // Своя камера там, где она есть. Где нет — сразу прежний
              // путь, без промежуточного окна с извинениями.
              if (supportsCamera()) setCamera(true);
              else fallBackToSystem();
            }}
          >
            <CameraIcon size={20} />
            <span>Сфотографировать</span>
          </button>
          <input
            id={shotId}
            ref={shotInput}
            className="file-input-hidden"
            type="file"
            accept="image/*"
            // Запасной путь. На iPhone открывает камеру, на Android
            // Telegram этот атрибут игнорирует — ради этого и написана
            // своя камера выше.
            capture="environment"
            disabled={disabled}
            onChange={(event) => onFile(event.target.files?.[0] ?? null)}
          />
          <label className="file-field" htmlFor={pickId}>
            <DocumentIcon size={20} />
            <span>Выбрать файл</span>
            <input
              id={pickId}
              type="file"
              accept={accept}
              disabled={disabled}
              onChange={(event) => onFile(event.target.files?.[0] ?? null)}
            />
          </label>
        </div>
      )}
    </Field>
  );
}
