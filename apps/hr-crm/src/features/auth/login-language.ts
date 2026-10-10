/**
 * Язык страницы входа: русский и узбекский (латиница).
 *
 * Перевод — только этой страницы: человек выбирает язык ещё до входа,
 * и форма должна быть понятна ему сразу. Кабинет после входа пока
 * русский; общего перевода CRM здесь нет, и выдавать словарь входа за
 * него не нужно.
 *
 * В браузере хранится только код выбранного языка — настройка вида, а
 * не данные доступа.
 */

import { useCallback, useEffect, useState } from 'react';

import { ApiFailure, messageFor, retryAfterOf } from '../../api/errors';

export const LANGUAGES = [
  { code: 'ru', short: 'RU', name: 'Русский' },
  { code: 'uz', short: 'UZ', name: 'Oʻzbekcha' },
] as const;

export type Language = (typeof LANGUAGES)[number]['code'];

const KEY = 'humotech.crm.language';

/** Почему вход не удался — без текста: текст берётся на выбранном языке. */
export type LoginFailure =
  | { reason: 'timeout' }
  | { reason: 'unknown' }
  | { reason: 'refused'; error: ApiFailure };

export type LoginText = {
  languageGroup: string;
  brandTitle: string;
  /** Последний пробел — неразрывный: короткое слово не висит в конце строки. */
  brandTagline: string;
  flowTitle: string;
  steps: readonly [
    { title: string; hint: string },
    { title: string; hint: string },
    { title: string; hint: string },
  ];
  title: string;
  subtitle: string;
  login: string;
  loginPlaceholder: string;
  password: string;
  passwordPlaceholder: string;
  showPassword: string;
  hidePassword: string;
  remember: string;
  submit: string;
  sending: string;
  sendingStatus: string;
  footerAccess: string;
  footerHelp: string;
  emptyLogin: string;
  emptyPassword: string;
  failure: (failure: LoginFailure) => string;
};

/**
 * Отказ — словами, по ответу сервера. Неверные данные называются
 * одинаково для любой причины: по ответу нельзя узнать, есть ли такая
 * учётная запись. Сетевой сбой — не «неверный пароль».
 */
function failureText(
  words: Record<'timeout' | 'unknown' | 'credentials' | 'offline' | 'csrf' | 'session'
    | 'validation' | 'server', string>,
  throttled: (error: ApiFailure) => string,
) {
  return (failure: LoginFailure): string => {
    if (failure.reason !== 'refused') return words[failure.reason];
    const { error } = failure;
    switch (error.kind) {
      case 'credentials':
      case 'offline':
      case 'csrf':
      case 'session':
      case 'validation':
        return words[error.kind];
      case 'throttled':
        return throttled(error);
      default:
        return words.server;
    }
  };
}

/** Сколько минут ждать после «слишком много попыток»; `null` — сервер не сказал. */
function minutesOf(error: ApiFailure): number | null {
  const wait = retryAfterOf(error);
  return wait === null ? null : Math.max(1, Math.ceil(wait / 60));
}

const RU: LoginText = {
  languageGroup: 'Язык интерфейса',
  brandTitle: 'Управление персоналом',
  brandTagline: 'Сотрудники, посещаемость и заявки — в одной системе.',
  flowTitle: 'Единый рабочий процесс',
  steps: [
    { title: 'Сотрудник', hint: 'Отметки, заявки и обращения' },
    { title: 'HR', hint: 'Рассмотрение и работа с персоналом' },
    { title: 'Руководитель', hint: 'Посещаемость и отчёты' },
  ],
  title: 'Вход в систему',
  subtitle: 'Введите данные вашей учётной записи',
  login: 'Логин',
  loginPlaceholder: 'Введите логин',
  password: 'Пароль',
  passwordPlaceholder: 'Введите пароль',
  showPassword: 'Показать пароль',
  hidePassword: 'Скрыть пароль',
  remember: 'Запомнить логин',
  submit: 'Войти',
  sending: 'Проверяем…',
  sendingStatus: 'Проверяем учётные данные',
  footerAccess: 'Доступ предоставляется администратором',
  footerHelp: 'Нет доступа? Обратитесь к администратору',
  emptyLogin: 'Введите логин',
  emptyPassword: 'Введите пароль',
  failure: failureText({
    timeout: 'Сервер не ответил вовремя. Попробуйте ещё раз.',
    unknown: 'Не удалось войти. Попробуйте ещё раз.',
    credentials: 'Неверный логин или пароль',
    offline: 'Нет связи с сервером. Проверьте подключение и попробуйте ещё раз.',
    csrf: 'Страница устарела. Обновите её и войдите снова.',
    session: 'Доступ к системе закрыт. Обратитесь к администратору.',
    validation: 'Проверьте логин и пароль.',
    server: 'Сервис временно недоступен. Попробуйте позже.',
    // Тот же текст, что во всей CRM: склонение минут уже написано там.
  }, messageFor),
};

const UZ: LoginText = {
  languageGroup: 'Interfeys tili',
  brandTitle: 'Xodimlarni boshqarish',
  brandTagline: 'Xodimlar, davomat va arizalar — yagona tizimda.',
  flowTitle: 'Yagona ish jarayoni',
  steps: [
    { title: 'Xodim', hint: 'Davomat belgilari, arizalar va murojaatlar' },
    { title: 'HR', hint: 'Arizalarni koʻrib chiqish va xodimlar bilan ishlash' },
    { title: 'Rahbar', hint: 'Davomat va hisobotlar' },
  ],
  title: 'Tizimga kirish',
  subtitle: 'Hisob maʼlumotlaringizni kiriting',
  login: 'Login',
  loginPlaceholder: 'Loginni kiriting',
  password: 'Parol',
  passwordPlaceholder: 'Parolni kiriting',
  showPassword: 'Parolni koʻrsatish',
  hidePassword: 'Parolni yashirish',
  remember: 'Loginni eslab qolish',
  submit: 'Kirish',
  sending: 'Tekshirilmoqda…',
  sendingStatus: 'Maʼlumotlar tekshirilmoqda',
  footerAccess: 'Kirish huquqini administrator beradi',
  footerHelp: 'Kirish imkoni yoʻqmi? Administratorga murojaat qiling',
  emptyLogin: 'Loginni kiriting',
  emptyPassword: 'Parolni kiriting',
  failure: failureText({
    timeout: 'Server oʻz vaqtida javob bermadi. Qaytadan urinib koʻring.',
    unknown: 'Kirib boʻlmadi. Qaytadan urinib koʻring.',
    credentials: 'Login yoki parol notoʻgʻri',
    offline: 'Server bilan aloqa yoʻq. Ulanishni tekshirib, qaytadan urinib koʻring.',
    csrf: 'Sahifa eskirgan. Uni yangilab, qaytadan kiring.',
    session: 'Tizimga kirish yopilgan. Administratorga murojaat qiling.',
    validation: 'Login va parolni tekshiring.',
    server: 'Xizmat vaqtincha ishlamayapti. Keyinroq urinib koʻring.',
  }, (error) => {
    const minutes = minutesOf(error);
    return minutes === null
      ? 'Urinishlar juda koʻp. Keyinroq urinib koʻring.'
      : `Urinishlar juda koʻp. ${minutes} daqiqadan soʻng urinib koʻring.`;
  }),
};

export const LOGIN_TEXT: Record<Language, LoginText> = { ru: RU, uz: UZ };

function isLanguage(value: unknown): value is Language {
  return LANGUAGES.some((one) => one.code === value);
}

function storedLanguage(): Language {
  try {
    const value = localStorage.getItem(KEY);
    return isLanguage(value) ? value : 'ru';
  } catch {
    // Приватное окно или запрещённое хранилище — просто русский.
    return 'ru';
  }
}

/**
 * Выбранный язык и смена его. Атрибут `lang` документа меняется вместе
 * с ним — по нему читалка экрана выбирает произношение. Уходя со
 * страницы входа, возвращаем `ru`: кабинет пока только русский.
 */
export function useLoginLanguage(): [Language, (next: Language) => void] {
  const [language, setLanguage] = useState<Language>(storedLanguage);

  useEffect(() => {
    document.documentElement.lang = language;
    return () => { document.documentElement.lang = 'ru'; };
  }, [language]);

  const choose = useCallback((next: Language) => {
    setLanguage(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* без памяти между визитами можно жить */
    }
  }, []);

  return [language, choose];
}
