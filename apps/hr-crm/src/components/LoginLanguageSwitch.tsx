/**
 * Переключатель языка страницы входа: RU · UZ.
 *
 * Оба варианта видны сразу, а не спрятаны в выпадающий список: языков
 * два, и выбор не стоит лишнего щелчка. Внутри — настоящие радиокнопки:
 * Tab приводит в группу, стрелки меняют язык, читалка экрана называет
 * язык полностью, а не двумя буквами.
 */

import { useId } from 'react';

import { LANGUAGES, type Language } from '../features/auth/login-language';

export function LoginLanguageSwitch({ value, label, onChange }: {
  value: Language;
  /** Подпись группы на текущем языке: «Язык интерфейса», «Interfeys tili». */
  label: string;
  onChange: (next: Language) => void;
}) {
  const name = useId();
  return (
    <div className="login-lang" role="radiogroup" aria-label={label}>
      {LANGUAGES.map((one) => (
        <label key={one.code} className="login-lang__option" lang={one.code}>
          <input
            type="radio"
            className="visually-hidden"
            name={name}
            value={one.code}
            checked={value === one.code}
            aria-label={one.name}
            onChange={() => onChange(one.code)}
          />
          <span aria-hidden="true">{one.short}</span>
        </label>
      ))}
    </div>
  );
}
