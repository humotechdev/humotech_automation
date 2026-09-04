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

import { useId, type ReactNode } from 'react';

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
 * или открыть телеграмное окно вложений мини-приложению нечем.
 *
 * Зато родной `input[type=file]` в вебвью Telegram открывает системное
 * окно, где есть и камера, и галерея, и файлы. Камера вынесена отдельной
 * кнопкой с `capture`: бумажную справку почти всегда фотографируют, и
 * лишний выбор в системном меню здесь ни к чему.
 *
 * Сам элемент остаётся в разметке — с ним работают камера, клавиатура
 * и экранный диктор; спрятан только его вид.
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

  return (
    <Field
      label={`Справка${required ? '' : ' (можно приложить позже)'}`}
      htmlFor={pickId}
      hint={hint}
      error={error}
    >
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
          <label className="file-field" htmlFor={shotId}>
            <CameraIcon size={20} />
            <span>Сфотографировать</span>
            <input
              id={shotId}
              type="file"
              accept="image/*"
              // Подсказка системе открыть камеру сразу, минуя галерею.
              // Где камеры нет, браузер её просто игнорирует.
              capture="environment"
              disabled={disabled}
              onChange={(event) => onFile(event.target.files?.[0] ?? null)}
            />
          </label>
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
