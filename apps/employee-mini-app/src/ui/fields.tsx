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

import {
  useEffect,
  useId,
  useState,
  type ChangeEvent,
  type ReactNode,
} from 'react';

import { CameraIcon, DocumentIcon, ImageIcon } from './icons';

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

// --- справка ----------------------------------------------------------------

/** Что принимает сервер, пока настройки организации не пришли. */
const DEFAULT_TYPES = ['application/pdf', 'image/jpeg', 'image/png'];
const DEFAULT_MAX_BYTES = 10 * 1024 * 1024;

/** Расширение → тип. По нему узнаём файл, у которого MIME не пришёл. */
const BY_EXTENSION: Record<string, string> = {
  '.pdf': 'application/pdf',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
};

/** Как формат называется в разговоре с человеком. */
const TYPE_LABEL: Record<string, string> = {
  'application/pdf': 'PDF',
  'image/jpeg': 'JPG',
  'image/png': 'PNG',
};

/** Чем «выбрать документ» отличается от «выбрать фото»: не картинки. */
const DOCUMENT_ACCEPT: Record<string, string> = {
  'application/pdf': '.pdf,application/pdf',
};

export function isImageFile(file: File): boolean {
  return resolvedType(file).startsWith('image/');
}

/**
 * Проверка справки на стороне телефона.
 *
 * Не защита — защита на сервере, и она там есть целиком. Смысл в другом:
 * человек с плохой связью не должен узнать про формат после того, как
 * десять мегабайт уехали и вернулись отказом.
 *
 * `accept` в разметке для этого не годится: он подсказка для системного
 * окна, и часть Android-провайдеров показывает мимо неё что угодно.
 *
 * @returns жалоба человеческими словами или `null`, если файл годится.
 */
export function checkCertificate(
  file: File,
  {
    allowedTypes = DEFAULT_TYPES,
    maxBytes = DEFAULT_MAX_BYTES,
  }: { allowedTypes?: string[]; maxBytes?: number } = {},
): string | null {
  if (!file.size) return 'Файл пустой — выберите другой.';
  if (file.size > maxBytes) {
    return `Файл больше ${formatSize(maxBytes)}. Снимите ещё раз или выберите файл поменьше.`;
  }

  const declared = declaredType(file);
  const guessed = BY_EXTENSION[extensionOf(file.name)];

  // Тип не пришёл вовсе. Так делают некоторые файловые провайдеры
  // Android: файл настоящий, а `type` пустой. Отказать по этому
  // признаку значит сломать ровно тот путь, который мы чиним.
  const actual = declared || guessed;
  if (!actual) return onlyThese(allowedTypes);
  if (!allowedTypes.includes(actual)) return onlyThese(allowedTypes);

  // Расширение и тип спорят между собой. Сервер поймает это по
  // содержимому, но сказать человеку лучше здесь и понятнее.
  if (declared && guessed && declared !== guessed) {
    return 'Расширение файла не совпадает с его содержимым.';
  }
  return null;
}

/**
 * Загрузка справки: снять камерой, взять из галереи или выбрать документ.
 *
 * Три разных `input`, а не один и не своя камера.
 *
 * Своя камера здесь была — `getUserMedia`, видоискатель, кадр через
 * `canvas`. Появилась она из-за Android, где «сфотографировать» открывало
 * галерею, и проблему закрыла ценой того, что телефон перестал быть
 * телефоном: ни вспышки, ни фокуса, ни поворота, ни пересъёмки — всего
 * того, что у человека в системной камере уже есть и работает лучше.
 * Убрана.
 *
 * Вместо неё три родных элемента, по одному на намерение:
 *
 *   * `capture="environment"` — задняя камера;
 *   * `accept="image/*"` без `capture` — готовое фото;
 *   * `accept=".pdf,…"` — документ.
 *
 * Открывает их `<label for>`, а не `ref.current.click()`. Между касанием
 * и системным окном не должно быть ни одной строки JavaScript: WebView
 * считает пользовательским действием само нажатие, и программный клик
 * — первое, что он теряет.
 *
 * Что именно покажет Android — камеру, выбор между камерой и галереей
 * или своё окно Telegram — решает не страница. Это ограничение WebView,
 * и обходить его нестандартными способами здесь нечем: панели вложений
 * Telegram у мини-приложения тоже нет, в WebApp API есть только
 * `downloadFile` — отдать файл человеку, но не взять.
 */
export function FileUploadField({
  file,
  onFile,
  allowedTypes,
  maxBytes,
  hint,
  error,
  disabled,
  required,
}: {
  file: File | null;
  onFile: (file: File | null) => void;
  /** Что принимает сервер. Приходит из настроек организации. */
  allowedTypes?: string[];
  maxBytes?: number;
  hint?: string;
  error?: string;
  disabled?: boolean;
  required?: boolean;
}) {
  const id = useId();
  const shotId = `${id}-shot`;
  const galleryId = `${id}-gallery`;
  const documentId = `${id}-document`;
  const [rejected, setRejected] = useState<string | null>(null);
  const preview = usePreview(file);

  const types = allowedTypes?.length ? allowedTypes : DEFAULT_TYPES;
  const limit = maxBytes && maxBytes > 0 ? maxBytes : DEFAULT_MAX_BYTES;

  function take(event: ChangeEvent<HTMLInputElement>) {
    const input = event.target;
    const picked = input.files?.[0] ?? null;

    // Сброс всегда и до всего остального. Без него повторный выбор того
    // же файла не даёт события вовсе: значение не изменилось.
    input.value = '';

    // Окно закрыли, ничего не выбрав. Уже приложенное не трогаем: отмена
    // — это «передумал открывать», а не «убери справку».
    if (!picked) return;

    const complaint = checkCertificate(picked, {
      allowedTypes: types,
      maxBytes: limit,
    });
    if (complaint) {
      setRejected(complaint);
      return;
    }
    setRejected(null);
    onFile(picked);
  }

  return (
    <Field
      label={`Справка${required ? '' : ' (можно приложить позже)'}`}
      htmlFor={shotId}
      hint={hint}
      error={rejected ?? error}
    >
      {file && (
        <div className="file-chosen">
          {preview ? (
            <img
              className="file-preview"
              src={preview}
              alt={`Справка: ${file.name}`}
            />
          ) : (
            <span className="file-badge">
              <DocumentIcon size={20} />
            </span>
          )}
          <span className="file-meta">
            <span className="file-name">{file.name}</span>
            <span className="file-size">{formatSize(file.size)}</span>
          </span>
          <button
            type="button"
            className="file-clear"
            onClick={() => {
              setRejected(null);
              onFile(null);
            }}
            disabled={disabled}
          >
            Удалить
          </button>
        </div>
      )}

      <div className="file-actions">
        <label className="file-field" htmlFor={shotId}>
          <CameraIcon size={20} />
          <span>Сфотографировать</span>
          <input
            id={shotId}
            type="file"
            accept="image/*"
            capture="environment"
            disabled={disabled}
            onChange={take}
          />
        </label>

        <label className="file-field" htmlFor={galleryId}>
          <ImageIcon size={20} />
          <span>Выбрать из галереи</span>
          <input
            id={galleryId}
            type="file"
            accept="image/*"
            disabled={disabled}
            onChange={take}
          />
        </label>

        <label className="file-field" htmlFor={documentId}>
          <DocumentIcon size={20} />
          <span>Выбрать документ</span>
          <input
            id={documentId}
            type="file"
            accept={documentAccept(types)}
            disabled={disabled}
            onChange={take}
          />
        </label>
      </div>

      {file && (
        <p className="field-hint">
          Чтобы заменить справку, снимите или выберите другую.
        </p>
      )}
    </Field>
  );
}

/**
 * Адрес для показа снимка — и его освобождение.
 *
 * `createObjectURL` держит файл в памяти вкладки, пока адрес не отозван.
 * На старом Android это несколько мегабайт за каждую пересъёмку, и они
 * не уходят сами до перезагрузки мини-приложения.
 *
 * Метода может не быть вовсе (так в jsdom и в совсем старых вебвью) —
 * тогда просто не показываем картинку: поле остаётся рабочим, у файла
 * видно имя и размер.
 */
function usePreview(file: File | null): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!file || !isImageFile(file)) {
      setUrl(null);
      return;
    }
    if (typeof URL?.createObjectURL !== 'function') {
      setUrl(null);
      return;
    }
    const created = URL.createObjectURL(file);
    setUrl(created);
    return () => {
      // Отзываем именно тот адрес, который создали в этом проходе, а не
      // тот, что лежит в состоянии: к моменту уборки он уже другой.
      URL.revokeObjectURL?.(created);
    };
  }, [file]);

  return url;
}

/** `accept` для документов: только то, что сервер и так принимает. */
function documentAccept(allowedTypes: string[]): string {
  const listed = allowedTypes
    .map((type) => DOCUMENT_ACCEPT[type])
    .filter((value): value is string => Boolean(value));
  // `*/*` не ставим ни при каких условиях: он открывает выбор чего
  // угодно, а отказ приходит уже после загрузки.
  return listed.join(',') || DOCUMENT_ACCEPT['application/pdf'];
}

function declaredType(file: File): string {
  return (file.type || '').split(';')[0].trim().toLowerCase();
}

/** Тип файла: заявленный, а если его нет — по расширению имени. */
function resolvedType(file: File): string {
  return declaredType(file) || BY_EXTENSION[extensionOf(file.name)] || '';
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot).toLowerCase() : '';
}

function onlyThese(allowedTypes: string[]): string {
  const names = allowedTypes
    .map((type) => TYPE_LABEL[type] ?? type)
    .filter((value, index, all) => all.indexOf(value) === index);
  return `Такой файл приложить нельзя. Подойдёт ${names.join(', ')}.`;
}

/** Размер человеческими словами: «2,4 МБ», а не «2516582». */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
  const megabytes = bytes / (1024 * 1024);
  // Десятая доля нужна только мелким файлам: «9,8 МБ» полезно, «10,0 МБ»
  // нет — и «1,0 МБ» тоже, поэтому ноль в дробной части не пишется.
  const shown =
    megabytes < 10 ? Math.round(megabytes * 10) / 10 : Math.round(megabytes);
  return `${String(shown).replace('.', ',')} МБ`;
}
