/*
 * Заглушки вкладок карточки сотрудника — по форме того, что придёт:
 * строки журнала и истории, блоки графика. Пока данные в пути, вкладка
 * уже стоит на своём месте и не прыгает, когда ответ приходит.
 * Для средств чтения — одна строка «Загружаем…» вместо серых полос.
 */

export function TabSkeleton({ kind, label }: {
  kind: 'rows' | 'schedule' | 'card'; label: string;
}) {
  return (
    <>
      <span className="visually-hidden" role="status">{label}</span>
      {kind === 'rows' && (
        <div className="pf-tsk" aria-hidden="true">
          <i className="pf-sk pf-tsk__head" />
          {[0, 1, 2, 3, 4, 5].map((n) => (
            <span key={n} className="pf-tsk__row">
              <i className="pf-sk pf-tsk__a" />
              <i className="pf-sk pf-tsk__b" />
              <i className="pf-sk pf-tsk__c" />
              <i className="pf-sk pf-tsk__d" />
            </span>
          ))}
        </div>
      )}
      {kind === 'schedule' && (
        <div className="pf-tsk" aria-hidden="true">
          <i className="pf-sk pf-tsk__title" />
          <span className="pf-tsk__week">
            {[0, 1, 2, 3, 4, 5, 6].map((n) => <i key={n} className="pf-sk pf-tsk__day" />)}
          </span>
          <i className="pf-sk pf-tsk__title" />
          {[0, 1, 2].map((n) => (
            <span key={n} className="pf-tsk__row">
              <i className="pf-sk pf-tsk__a" />
              <i className="pf-sk pf-tsk__b" />
              <i className="pf-sk pf-tsk__c" />
            </span>
          ))}
        </div>
      )}
      {kind === 'card' && (
        <div className="pf-sheet pf-tsk--card" aria-hidden="true">
          <div className="pf-aside pf-tsk__aside">
            <i className="pf-sk pf-tsk__face" />
            <i className="pf-sk pf-tsk__name" />
            <i className="pf-sk pf-tsk__role" />
            <span className="pf-tsk__btns"><i className="pf-sk" /><i className="pf-sk" /></span>
            {[0, 1, 2, 3, 4, 5].map((n) => <i key={n} className="pf-sk pf-tsk__fact" />)}
          </div>
          <div className="pf-main pf-tsk__main">
            <i className="pf-sk pf-tsk__tabs" />
            <i className="pf-sk pf-tsk__plate" />
            <span className="pf-tsk__stats"><i className="pf-sk" /><i className="pf-sk" /><i className="pf-sk" /></span>
            {[0, 1, 2, 3].map((n) => <i key={n} className="pf-sk pf-tsk__line" />)}
          </div>
        </div>
      )}
    </>
  );
}
