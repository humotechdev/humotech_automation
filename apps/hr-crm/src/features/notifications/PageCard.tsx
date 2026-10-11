/**
 * Каркас страницы «Уведомления»: одна белая карточка с заголовком,
 * числом, подзаголовком, действием справа и вкладками под ними.
 *
 * Обе вкладки рисуются в одном и том же каркасе — меняется только
 * число, действие и содержимое. Число приходит с сервера; пока его
 * нет, места под него нет вовсе (не ноль и не прочерк).
 */

import type { ReactNode } from 'react';

export type PageView = 'feed' | 'delivery';

export function PageCard({
  view,
  count,
  action,
  onView,
  children,
}: {
  view: PageView;
  count: number | null;
  action?: ReactNode;
  onView: (view: PageView) => void;
  children: ReactNode;
}) {
  return (
    <section className="ntf" aria-labelledby="ntf-title">
      <header className="ntf__head">
        <div className="ntf__heading">
          <h1 className="ntf__title" id="ntf-title">
            Уведомления
            {count !== null && (
              <span className="ntf__count" aria-label={`всего ${count}`}>{count}</span>
            )}
          </h1>
          <p className="ntf__sub">События по сотрудникам и заявкам</p>
        </div>
        {action && <div className="ntf__headActions">{action}</div>}
      </header>

      <div className="ntf__tabs" role="tablist" aria-label="Раздел уведомлений">
        {([['feed', 'Лента событий'], ['delivery', 'Очередь отправки']] as const).map(([key, title]) => (
          <button key={key} type="button" role="tab" aria-selected={view === key}
                  className={view === key ? 'ntf__tab ntf__tab--on' : 'ntf__tab'}
                  onClick={() => { if (view !== key) onView(key); }}>
            {title}
          </button>
        ))}
      </div>

      <div className="ntf__body" key={view}>
        {children}
      </div>
    </section>
  );
}
