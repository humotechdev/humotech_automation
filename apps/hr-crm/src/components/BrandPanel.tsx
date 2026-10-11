/**
 * Левая, тёмно-синяя половина страницы входа.
 *
 * Знак, две строки о том, что это за система, и справка «кто чем
 * занят»: сотрудник, HR, руководитель. Чисел здесь нет намеренно —
 * «показатели» до входа взять неоткуда, человек ещё не вошёл.
 *
 * На узком экране панель сжимается до полосы со знаком: справка
 * скрывается, и форма входа не уезжает за первый экран телефона.
 */

import { LOGIN_TEXT, type LoginText } from '../features/auth/login-language';
import { AppIcon, ICON_SIZE, type AppIconName } from './AppIcon';
import { BrandLockup } from './Logo';

/** Значки шагов — по порядку шагов в словаре: сотрудник, HR, руководитель. */
const STEP_ICONS: readonly AppIconName[] = ['user', 'bag', 'chart'];

type BrandText = Pick<LoginText, 'brandTitle' | 'brandTagline' | 'flowTitle' | 'steps'>;

export function BrandPanel({ text = LOGIN_TEXT.ru }: { text?: BrandText }) {
  return (
    <aside className="brand">
      {/* Тот же фирменный файл, что в боковом меню CRM: знак с надписью
          для тёмного фона. Собирать надпись шрифтом отдельно значило бы
          иметь два разных логотипа в одном продукте. */}
      <div className="brand__logo">
        <BrandLockup />
      </div>

      <div className="brand__text">
        <p className="brand__title">{text.brandTitle}</p>
        <p className="brand__tagline">{text.brandTagline}</p>
      </div>

      {/* Кто чем занят в системе — справка, а не меню: пункты не
          нажимаются. Иконки декоративные, смысл несёт текст. */}
      <section className="brand__flow" aria-labelledby="brand-flow-title">
        <p className="brand__flow-title" id="brand-flow-title">{text.flowTitle}</p>
        <ol className="brand__steps">
          {text.steps.map((step, index) => (
            <li key={STEP_ICONS[index]} className="brand__step">
              <span className="brand__step-icon" aria-hidden="true">
                <AppIcon name={STEP_ICONS[index] ?? 'user'} size={ICON_SIZE.title} />
              </span>
              <span className="brand__step-text">
                <span className="brand__step-title">{step.title}</span>
                <span className="brand__step-hint">{step.hint}</span>
              </span>
            </li>
          ))}
        </ol>
      </section>

      <p className="brand__foot">HUMOTECH HR</p>
    </aside>
  );
}
