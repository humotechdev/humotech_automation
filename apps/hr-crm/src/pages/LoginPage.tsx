/**
 * Вход в HR-систему.
 *
 * Регистрации здесь нет и не будет: учётные записи заводит администратор.
 * Поэтому на странице нет ни «создать аккаунт», ни входа через соцсети,
 * ни восстановления пароля — последнее особенно: обещать письмо, которого
 * система не отправляет, хуже, чем не обещать ничего.
 */

import { useEffect, useId, useRef, useState, type FormEvent } from 'react';

import { currentUser } from '../api/auth';
import { ApiFailure } from '../api/errors';
import { BrandPanel } from '../components/BrandPanel';
import { AppIcon, ICON_SIZE } from '../components/AppIcon';
import { LoginLanguageSwitch } from '../components/LoginLanguageSwitch';
import { LOGIN_TEXT, useLoginLanguage, type LoginFailure } from '../features/auth/login-language';
import { forgetLogin, rememberLogin, rememberedLogin } from '../features/auth/remembered';
import { useSession } from '../features/auth/session';

/** Сколько ждать ответа на вход. Дольше — человек решит, что всё зависло. */
const LOGIN_TIMEOUT_MS = 20_000;

function failureOf(error: unknown, timedOut: boolean): LoginFailure {
  if (timedOut) return { reason: 'timeout' };
  if (error instanceof ApiFailure) return { reason: 'refused', error };
  return { reason: 'unknown' };
}

export function LoginPage() {
  const session = useSession();
  const [language, setLanguage] = useLoginLanguage();
  const t = LOGIN_TEXT[language];
  const saved = useRef(rememberedLogin());
  // Повторная отправка блокируется синхронно: `sending` обновится только
  // после перерисовки, а двойной клик быстрее.
  const busy = useRef(false);
  const alive = useRef(true);
  // Флаг поднимается при каждом монтировании: StrictMode монтирует
  // страницу дважды, и без этого после первой «размонтировки» форма
  // считала себя закрытой и навсегда оставалась в «Проверяем…».
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const [login, setLogin] = useState(saved.current);
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(saved.current !== '');
  const [visible, setVisible] = useState(false);
  const [sending, setSending] = useState(false);
  // Причина, а не готовый текст: сменили язык после отказа — сообщение
  // переводится вместе со страницей.
  const [failed, setFailed] = useState<{ login?: true; password?: true; common?: LoginFailure }>({});

  const loginId = useId();
  const passwordId = useId();
  const loginErrorId = `${loginId}-error`;
  const passwordErrorId = `${passwordId}-error`;

  function toggleRemember(next: boolean) {
    setRemember(next);
    // Снятая галочка — «не помнить»: сохранённый логин убирается сразу,
    // а не после следующего входа.
    if (!next) forgetLogin();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy.current) return; // вторая отправка той же формы ничего не добавит

    const problems: typeof failed = {};
    if (login.trim() === '') problems.login = true;
    if (password === '') problems.password = true;
    if (problems.login || problems.password) {
      setFailed(problems);
      return;
    }

    busy.current = true;
    setFailed({});
    setSending(true);
    const stop = new AbortController();
    let timedOut = false;
    const timer = window.setTimeout(() => { timedOut = true; stop.abort(); }, LOGIN_TIMEOUT_MS);
    try {
      await session.signIn(login.trim(), password, stop.signal);
      // Хранится только логин — пароль и сессия в браузерное хранилище
      // не попадают.
      if (remember) rememberLogin(login.trim());
      else forgetLogin();
    } catch (error) {
      if (!alive.current) return;
      setFailed({ common: failureOf(error, timedOut) });
      // Пароль стирается только при неверных данных: после сетевого
      // сбоя человек просто нажимает «Войти» ещё раз.
      if (error instanceof ApiFailure && error.kind === 'credentials') setPassword('');
      // Ответ мог потеряться уже после того, как сервер открыл сессию:
      // тихо спрашиваем сервер и, если вход прошёл, ведём дальше. Общую
      // перепроверку сразу не зовём: на время она показывает «проверяем»
      // вместо формы, и введённое вместе с сообщением пропало бы.
      if (timedOut || (error instanceof ApiFailure && error.kind === 'offline')) {
        currentUser().then(() => session.recheck()).catch(() => undefined);
      }
    } finally {
      window.clearTimeout(timer);
      busy.current = false;
      if (alive.current) setSending(false);
    }
  }

  return (
    <main className="login">
      <BrandPanel text={t} />

      <section className="login__side">
        <div className="login__lang">
          <LoginLanguageSwitch value={language} label={t.languageGroup} onChange={setLanguage} />
        </div>

        <div className="login__card">
          <div className="login__main">
            <p className="login__eyebrow">HUMOTECH HR</p>
            <h1 className="login__title">{t.title}</h1>
            <p className="login__subtitle">{t.subtitle}</p>

            <form className="form" onSubmit={submit} noValidate>
              <div className="field">
                <label className="field__label" htmlFor={loginId}>
                  {t.login}
                </label>
                <div className="field__box">
                  <AppIcon name="user" size={ICON_SIZE.nav} className="field__icon" />
                  <input
                    id={loginId}
                    className="field__input"
                    type="text"
                    inputMode="email"
                    autoComplete="username"
                    autoCapitalize="off"
                    autoCorrect="off"
                    placeholder={t.loginPlaceholder}
                    value={login}
                    readOnly={sending}
                    aria-invalid={failed.login !== undefined}
                    aria-describedby={failed.login ? loginErrorId : undefined}
                    onChange={(event) => setLogin(event.target.value)}
                  />
                </div>
                {failed.login && (
                  <p className="field__error" id={loginErrorId}>
                    {t.emptyLogin}
                  </p>
                )}
              </div>

              <div className="field">
                <label className="field__label" htmlFor={passwordId}>
                  {t.password}
                </label>
                <div className="field__box">
                  <AppIcon name="lock" size={ICON_SIZE.nav} className="field__icon" />
                  <input
                    id={passwordId}
                    className="field__input"
                    type={visible ? 'text' : 'password'}
                    autoComplete="current-password"
                    placeholder={t.passwordPlaceholder}
                    value={password}
                    readOnly={sending}
                    aria-invalid={failed.password !== undefined}
                    aria-describedby={failed.password ? passwordErrorId : undefined}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                  <button
                    type="button"
                    className="field__reveal"
                    aria-label={visible ? t.hidePassword : t.showPassword}
                    aria-pressed={visible}
                    onClick={() => setVisible((was) => !was)}
                  >
                    {visible ? <AppIcon name="eye-off" size={ICON_SIZE.nav} />
                             : <AppIcon name="eye" size={ICON_SIZE.nav} />}
                  </button>
                </div>
                {failed.password && (
                  <p className="field__error" id={passwordErrorId}>
                    {t.emptyPassword}
                  </p>
                )}
              </div>

              <label className="remember">
                <input
                  type="checkbox"
                  checked={remember}
                  disabled={sending}
                  onChange={(event) => toggleRemember(event.target.checked)}
                />
                <span>{t.remember}</span>
              </label>

              {failed.common && (
                <p className="form__alert" role="alert">
                  {t.failure(failed.common)}
                </p>
              )}

              <button type="submit" className="submit" disabled={sending} aria-busy={sending}>
                {sending ? t.sending : t.submit}
              </button>

              {/* Для читалки экрана состояние отправки — текстом, а не
                  только сменой подписи на кнопке. */}
              <span className="visually-hidden" role="status">
                {sending ? t.sendingStatus : ''}
              </span>
            </form>
          </div>

          <footer className="login__footer">
            <AppIcon name="admin" size={ICON_SIZE.card} className="login__shield" />
            <div>
              <p>{t.footerAccess}</p>
              <p>{t.footerHelp}</p>
            </div>
          </footer>
        </div>
      </section>
    </main>
  );
}
