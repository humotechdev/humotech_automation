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

import { AppIcon, ICON_SIZE, type AppIconName } from './AppIcon';
import { BrandLockup } from './Logo';

const STEPS: ReadonlyArray<{ icon: AppIconName; title: string; hint: string }> = [
  { icon: 'user', title: 'Сотрудник', hint: 'Отметки, заявки и обращения' },
  { icon: 'bag', title: 'HR', hint: 'Рассмотрение и работа с персоналом' },
  { icon: 'chart', title: 'Руководитель', hint: 'Посещаемость и отчёты' },
];

export function BrandPanel() {
  return (
    <aside className="brand">
      {/* Тот же фирменный файл, что в боковом меню CRM: знак с надписью
          для тёмного фона. Собирать надпись шрифтом отдельно значило бы
          иметь два разных логотипа в одном продукте. */}
      <div className="brand__logo">
        <BrandLockup />
      </div>

      <div className="brand__text">
        <p className="brand__title">Управление персоналом</p>
        {/* Неразрывный пробел держит «в» при «одной»: предлог не
            остаётся висеть в конце строки. */}
        <p className="brand__tagline">Сотрудники, посещаемость и заявки — в{' '}одной системе.</p>
      </div>

      {/* Кто чем занят в системе — справка, а не меню: пункты не
          нажимаются. Иконки декоративные, смысл несёт текст. */}
      <section className="brand__flow" aria-labelledby="brand-flow-title">
        <p className="brand__flow-title" id="brand-flow-title">Единый рабочий процесс</p>
        <ol className="brand__steps">
          {STEPS.map((step) => (
            <li key={step.title} className="brand__step">
              <span className="brand__step-icon" aria-hidden="true">
                <AppIcon name={step.icon} size={ICON_SIZE.title} />
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
