/**
 * Вход в HR-систему.
 *
 * Регистрации здесь нет и не будет: учётные записи заводит администратор.
 * Поэтому на странице нет ни «создать аккаунт», ни входа через соцсети,
 * ни восстановления пароля — последнее особенно: обещать письмо, которого
 * система не отправляет, хуже, чем не обещать ничего.
 */

import { useId, useRef, useState, type FormEvent } from 'react';

import { messageFor } from '../api/errors';
import { BrandPanel } from '../components/BrandPanel';
import { LanguageSwitch } from '../components/LanguageSwitch';
import { Wordmark } from '../components/Logo';
import { EyeIcon, EyeOffIcon, LockIcon, ShieldIcon, UserIcon } from '../components/icons';
import { forgetLogin, rememberLogin, rememberedLogin } from '../features/auth/remembered';
import { useSession } from '../features/auth/session';

export function LoginPage() {
  const session = useSession();
  const saved = useRef(rememberedLogin());

  const [login, setLogin] = useState(saved.current);
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(saved.current !== '');
  const [visible, setVisible] = useState(false);
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState<{ login?: string; password?: string; common?: string }>({});

  const loginId = useId();
  const passwordId = useId();
  const loginErrorId = `${loginId}-error`;
  const passwordErrorId = `${passwordId}-error`;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (sending) return; // вторая отправка той же формы ничего не добавит

    const problems: typeof failed = {};
    if (login.trim() === '') problems.login = 'Введите логин';
    if (password === '') problems.password = 'Введите пароль';
    if (problems.login || problems.password) {
      setFailed(problems);
      return;
    }

    setFailed({});
    setSending(true);
    try {
      await session.signIn(login.trim(), password);
      if (remember) rememberLogin(login.trim());
      else forgetLogin();
    } catch (error) {
      setFailed({ common: messageFor(error) });
      setPassword('');
    } finally {
      setSending(false);
    }
  }

  return (
    <main className="page">
      <div className="card">
        <BrandPanel />

        <section className="form-side">
          <div className="form-side__top">
            <LanguageSwitch />
          </div>

          <div className="form-side__body">
            <div className="form-side__brand">
              <Wordmark size={48} />
            </div>

            <h1 className="form-side__title">Добро пожаловать</h1>
            <p className="form-side__subtitle">Войдите в HR-систему HUMOTECH</p>

            <form className="form" onSubmit={submit} noValidate>
              <div className="field">
                <label className="field__label" htmlFor={loginId}>
                  Логин
                </label>
                <div className="field__box">
                  <UserIcon className="field__icon" />
                  <input
                    id={loginId}
                    className="field__input"
                    type="text"
                    inputMode="email"
                    autoComplete="username"
                    autoCapitalize="off"
                    autoCorrect="off"
                    placeholder="Введите логин"
                    value={login}
                    disabled={sending}
                    aria-invalid={failed.login !== undefined}
                    aria-describedby={failed.login ? loginErrorId : undefined}
                    onChange={(event) => setLogin(event.target.value)}
                  />
                </div>
                {failed.login && (
                  <p className="field__error" id={loginErrorId}>
                    {failed.login}
                  </p>
                )}
              </div>

              <div className="field">
                <label className="field__label" htmlFor={passwordId}>
                  Пароль
                </label>
                <div className="field__box">
                  <LockIcon className="field__icon" />
                  <input
                    id={passwordId}
                    className="field__input"
                    type={visible ? 'text' : 'password'}
                    autoComplete="current-password"
                    placeholder="Введите пароль"
                    value={password}
                    disabled={sending}
                    aria-invalid={failed.password !== undefined}
                    aria-describedby={failed.password ? passwordErrorId : undefined}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                  <button
                    type="button"
                    className="field__reveal"
                    aria-label={visible ? 'Скрыть пароль' : 'Показать пароль'}
                    aria-pressed={visible}
                    onClick={() => setVisible((was) => !was)}
                  >
                    {visible ? <EyeOffIcon /> : <EyeIcon />}
                  </button>
                </div>
                {failed.password && (
                  <p className="field__error" id={passwordErrorId}>
                    {failed.password}
                  </p>
                )}
              </div>

              <label className="remember">
                <input
                  type="checkbox"
                  checked={remember}
                  disabled={sending}
                  onChange={(event) => setRemember(event.target.checked)}
                />
                <span>Запомнить логин</span>
              </label>

              {failed.common && (
                <p className="form__alert" role="alert">
                  {failed.common}
                </p>
              )}

              <button type="submit" className="submit" disabled={sending} aria-busy={sending}>
                {sending ? 'Проверяем…' : 'Войти'}
              </button>

              {/* Для читалки экрана состояние отправки — текстом, а не
                  только сменой подписи на кнопке. */}
              <span className="visually-hidden" role="status">
                {sending ? 'Проверяем учётные данные' : ''}
              </span>
            </form>

            <footer className="form-side__footer">
              <ShieldIcon className="form-side__shield" />
              <div>
                <p>Доступ только для авторизованных сотрудников</p>
                <p>Нет доступа? Обратитесь к администратору</p>
              </div>
            </footer>
          </div>
        </section>
      </div>
    </main>
  );
}
