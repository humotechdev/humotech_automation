/**
 * Поля формы больничного.
 *
 * Отдельно от `fields.tsx` не ради порядка: у больничного другие
 * правила, и смешивать их с отпуском значило бы городить ветвления в
 * общем коде. Отпуск требует дат — из них считается остаток. Больничный
 * не требует ничего: человек заболел в пятницу вечером и не знает,
 * выйдет ли он во вторник или в четверг. Точный период потом проставит
 * кадровик по справке.
 */

import { useId, useState, type ChangeEvent, type ReactNode } from 'react';

import { checkCertificate, formatSize } from './fields';
import { CalendarIcon, ClipIcon, CloseIcon, DocumentIcon, UserIcon } from './icons';

/** Строка «заявка оформляется на такого-то». Не поле — утверждение. */
export function OnBehalfRow({ fullName }: { fullName: string }) {
  return (
    <p className="sick-behalf">
      <UserIcon size={20} />
      <span>
        Заявка будет оформлена на: <b>{fullName}</b>
      </span>
    </p>
  );
}

/**
 * Две необязательные даты.
 *
 * `placeholder` у `input[type=date]` не работает ни в одном браузере:
 * поле само рисует маску. Поэтому пустое поле держится текстовым и
 * становится датой на фокусе — так «Не указано» видно, а календарь
 * открывается по касанию.
 */
export function OptionalDateRange({
  first,
  last,
  onFirst,
  onLast,
  disabled,
  error,
}: {
  first: string;
  last: string;
  onFirst: (value: string) => void;
  onLast: (value: string) => void;
  disabled?: boolean;
  error?: string;
}) {
  const id = useId();

  return (
    <div className="sick-dates">
      <div className="sick-dates-row">
        <OptionalDate
          id={`${id}-first`}
          label="С какого дня"
          value={first}
          onChange={onFirst}
          disabled={disabled}
        />
        <OptionalDate
          id={`${id}-last`}
          label="По какой день"
          value={last}
          min={first || undefined}
          onChange={onLast}
          disabled={disabled}
        />
      </div>
      {error ? (
        <p className="field-error" role="alert">
          {error}
        </p>
      ) : (
        <p className="sick-note">
          Если даты пока неизвестны, оставьте поля пустыми.
        </p>
      )}
    </div>
  );
}

function OptionalDate({
  id,
  label,
  value,
  min,
  onChange,
  disabled,
}: {
  id: string;
  label: string;
  value: string;
  min?: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  // Пока поле пустое и не в фокусе, оно текстовое: только так вместо
  // «дд.мм.гггг» видно «Не указано».
  const [editing, setEditing] = useState(false);
  const asDate = editing || value !== '';

  return (
    <div className="sick-date">
      <label htmlFor={id}>{label}</label>
      <div className="sick-date-box">
        <CalendarIcon size={20} />
        <input
          id={id}
          type={asDate ? 'date' : 'text'}
          value={value}
          min={min}
          placeholder="Не указано"
          disabled={disabled}
          onFocus={() => setEditing(true)}
          onBlur={() => setEditing(false)}
          onChange={(event) => onChange(event.target.value)}
        />
        {value && (
          <button
            type="button"
            className="sick-date-clear"
            aria-label={`Очистить «${label}»`}
            disabled={disabled}
            onClick={() => onChange('')}
          >
            <CloseIcon size={16} />
          </button>
        )}
      </div>
    </div>
  );
}

/** Заголовок поля с пометкой «Необязательно» справа. */
export function OptionalLabel({
  label,
  htmlFor,
}: {
  label: string;
  htmlFor: string;
}) {
  return (
    <div className="sick-label">
      <label htmlFor={htmlFor}>{label}</label>
      <span>Необязательно</span>
    </div>
  );
}

/**
 * Справка — одна строка, а не три кнопки.
 *
 * Прежние «сфотографировать / галерея / документ» занимали пол-экрана
 * и повторяли то, что телефон и так спрашивает сам: системный выбор
 * файла предлагает и камеру, и галерею. Три кнопки были ответом на
 * вопрос, которого человек не задавал.
 */
export function CertificateRow({
  file,
  onFile,
  allowedTypes,
  maxBytes,
  disabled,
}: {
  file: File | null;
  onFile: (file: File | null) => void;
  allowedTypes?: string[];
  maxBytes?: number;
  disabled?: boolean;
}) {
  const id = useId();
  const [rejected, setRejected] = useState<string | null>(null);

  function take(event: ChangeEvent<HTMLInputElement>) {
    const input = event.target;
    const picked = input.files?.[0] ?? null;
    // Сброс до всего остального: без него повторный выбор того же файла
    // не даёт события вовсе — значение не изменилось.
    input.value = '';
    // Окно закрыли, ничего не выбрав. Уже приложенное не трогаем:
    // отмена — это «передумал открывать», а не «убери справку».
    if (!picked) return;

    const complaint = checkCertificate(picked, {
      ...(allowedTypes?.length ? { allowedTypes } : {}),
      ...(maxBytes && maxBytes > 0 ? { maxBytes } : {}),
    });
    if (complaint) {
      setRejected(complaint);
      return;
    }
    setRejected(null);
    onFile(picked);
  }

  return (
    <div className="sick-block">
      <div className="sick-label">
        <span>Справка</span>
        <span>Необязательно</span>
      </div>

      {file ? (
        <div className="sick-file">
          <DocumentIcon size={20} />
          <span className="sick-file-meta">
            <span className="sick-file-name">{file.name}</span>
            <span className="sick-file-size">{formatSize(file.size)}</span>
          </span>
          <button
            type="button"
            className="sick-file-clear"
            onClick={() => {
              setRejected(null);
              onFile(null);
            }}
            disabled={disabled}
          >
            Удалить
          </button>
        </div>
      ) : (
        <label className="sick-attach" htmlFor={id}>
          <ClipIcon size={20} />
          <span>Прикрепить справку</span>
          <input
            id={id}
            type="file"
            accept="image/*,application/pdf"
            disabled={disabled}
            onChange={take}
          />
        </label>
      )}

      {rejected ? (
        <p className="field-error" role="alert">
          {rejected}
        </p>
      ) : (
        <p className="sick-note">Можно добавить позже в истории заявки.</p>
      )}
    </div>
  );
}

/** Что произойдёт после отправки. Без цветной карточки и рамки. */
export function AfterSubmitNote({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="sick-after">
      <span className="sick-after-mark">{icon}</span>
      <div>
        <p className="sick-after-title">{title}</p>
        <p className="sick-after-text">{children}</p>
      </div>
    </div>
  );
}

/** Согласие с условиями. Пока не отмечено, отправка недоступна. */
export function TermsCheckbox({
  checked,
  onChange,
  onTerms,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  onTerms: () => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="sick-terms">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <label htmlFor={id}>
        Я ознакомился с{' '}
        {/* Ссылка внутри подписи, но не внутри `label`-клика: нажатие
            на неё не должно заодно ставить галочку. */}
        <button type="button" className="sick-terms-link" onClick={onTerms}>
          условиями
        </button>{' '}
        оформления больничного
      </label>
    </div>
  );
}
