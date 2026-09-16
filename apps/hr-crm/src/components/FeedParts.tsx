/**
 * Строка ленты и карточка события — общие для колокольчика и страницы.
 *
 * Два места показывают одни и те же данные, и написать их дважды значило
 * бы получить два разных ответа на один вопрос: в окне «На рассмотрении»,
 * на странице «Ожидает решения». Поэтому разметка одна, а различается
 * только размер области вокруг.
 */

import type { FeedDetail, FeedEvent } from '../api/crm';
import { AppIcon, ICON_SIZE } from './AppIcon';
import { FEED_ICON, facts, since, statusTone } from '../features/notifications/feed-model';

export function FeedRow({
  event,
  active,
  onPick,
}: {
  event: FeedEvent;
  active: boolean;
  onPick: (event: FeedEvent) => void;
}) {
  return (
    <button
      type="button"
      className={active ? 'nf__row nf__row--on' : 'nf__row'}
      onClick={() => onPick(event)}
    >
      <span className={`nf__icon nf__icon--${statusTone(event)}`}>
        <AppIcon name={FEED_ICON[event.type]} size={ICON_SIZE.card} />
      </span>
      <span className="nf__text">
        <span className="nf__rowTitle">{event.title}</span>
        {/* Имя сотрудника, а у системных событий — их собственное
            описание. Короткая строка, без описания целиком. */}
        <span className="nf__rowWho">{event.employee_name || event.short_text}</span>
        <span className="nf__rowWhen">{since(event.created_at)}</span>
      </span>
      {event.priority === 'CRITICAL' ? (
        <span className="nf__dot nf__dot--red" title="Критичное событие" />
      ) : (
        !event.read_at && <span className="nf__dot" title="Не прочитано" />
      )}
    </button>
  );
}

/**
 * Правая область: подробности одного события.
 *
 * Решения здесь не принимают — ни «одобрить», ни «отклонить». Кнопка
 * ведёт на страницу записи, где у кадровика есть вся заявка целиком:
 * одобрять по трём строкам в окне нельзя.
 */
export function FeedCard({
  card,
  onFollow,
}: {
  card: FeedDetail;
  onFollow: (url: string) => void;
}) {
  const place = [
    card.employee?.position_name,
    card.employee?.office_name ?? card.office_name,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <>
      <div className="nf__cardHead">
        <span className={`nf__icon nf__icon--${statusTone(card)}`}>
          <AppIcon name={FEED_ICON[card.type]} size={ICON_SIZE.title} />
        </span>
        <div className="nf__cardWho">
          <h3 className="nf__cardTitle">{card.title}</h3>
          {card.employee_name && <p className="nf__cardName">{card.employee_name}</p>}
          {place && <p className="nf__cardPlace">{place}</p>}
        </div>
      </div>

      <dl className="nf__facts">
        {facts(card).map((fact) => (
          <div className="nf__fact" key={fact.key}>
            <span className="nf__factIcon">
              <AppIcon name={fact.icon} size={ICON_SIZE.card} />
            </span>
            <div>
              <dt>{fact.label}</dt>
              <dd>{fact.value}</dd>
            </div>
          </div>
        ))}
        <div className="nf__fact">
          <span className="nf__factIcon">
            <AppIcon name="check" size={ICON_SIZE.card} />
          </span>
          <div>
            <dt>Статус</dt>
            <dd>
              <span className={`nf__state nf__state--${statusTone(card)}`}>
                {card.status_label}
              </span>
            </dd>
          </div>
        </div>
      </dl>

      {card.comment && (
        <div className="nf__comment">
          <p className="nf__commentTitle">Комментарий</p>
          <p className="nf__commentText">{card.comment}</p>
        </div>
      )}
      {card.author && <p className="nf__author">Автор действия: {card.author.name}</p>}

      <button type="button" className="nf__go" onClick={() => onFollow(card.action_url)}>
        {card.action_title}
      </button>
    </>
  );
}
