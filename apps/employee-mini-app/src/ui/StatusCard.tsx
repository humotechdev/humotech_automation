/**
 * Главная статусная карточка — единственный крупный тёмно-синий блок
 * во всём приложении.
 *
 * Тёмной она становится только когда человек в офисе: это состояние,
 * ради которого экран открывают, и оно должно читаться с вытянутой руки.
 * Во всех остальных случаях карточка светлая с тёмно-синим акцентом —
 * иначе «тёмно-синий = важное» перестаёт что-либо значить.
 *
 * Открытой сессии не назначается выдуманное время выхода. Показывается
 * длительность на текущий момент и прямо говорится, что она ещё вырастет:
 * выдуманный конец рабочего дня в документе, по которому считают
 * зарплату, — это ложь, а не удобство.
 */

import type { Status } from '../api';
import { duration, time } from '../format';
import { ClockIcon } from './icons';

/** Состояния, у которых свой вид карточки. */
const ABSENCE_STATES = ['SICK_LEAVE', 'VACATION', 'OTHER_ABSENCE'];

export function StatusCard({ status }: { status: Status }) {
  const tz = status.timezone;
  const inside = status.state === 'IN_OFFICE';
  const open = status.open_session;

  if (inside && open) {
    return (
      <section className="status-card status-card-inside">
        <p className="status-line">
          <span className="status-dot" aria-hidden="true" />
          <span>Сейчас в офисе</span>
        </p>
        <p className="status-duration">{duration(open.seconds)}</p>
        <p className="status-preliminary">
          <ClockIcon size={14} />
          <span>По состоянию на сейчас — время ещё идёт</span>
        </p>
        <dl className="status-facts">
          <div>
            <dt>Вход</dt>
            <dd>
              {time(open.started_at, tz)}
              {open.day !== status.day && ' (вчера)'}
            </dd>
          </div>
          <div>
            <dt>Офис</dt>
            <dd>{open.office_name ?? '—'}</dd>
          </div>
        </dl>
      </section>
    );
  }

  if (ABSENCE_STATES.includes(status.state)) {
    return (
      <section className="status-card status-card-light status-card-absence">
        <p className="status-line">
          <span className="status-dot status-dot-warning" aria-hidden="true" />
          <span>{absenceTitle(status.state)}</span>
        </p>
        {status.absence_name && (
          <p className="status-note">{status.absence_name}</p>
        )}
        <p className="status-note">Отметки в эти дни не нужны.</p>
      </section>
    );
  }

  if (status.state === 'DAY_OFF') {
    return (
      <section className="status-card status-card-light">
        <p className="status-line">
          <span className="status-dot status-dot-muted" aria-hidden="true" />
          <span>Сегодня выходной</span>
        </p>
        <p className="status-note">По графику это нерабочий день.</p>
      </section>
    );
  }

  const missed = status.state === 'WORKDAY_MISSED';

  return (
    <section className="status-card status-card-light">
      <p className="status-line">
        <span
          className={`status-dot ${missed ? 'status-dot-danger' : 'status-dot-muted'}`}
          aria-hidden="true"
        />
        <span>{missed ? 'Рабочий день без отметок' : 'Вне офиса'}</span>
      </p>

      {status.seconds_today > 0 && (
        <p className="status-duration status-duration-light">
          {duration(status.seconds_today)}
        </p>
      )}

      <dl className="status-facts status-facts-light">
        <div>
          <dt>Последний вход</dt>
          <dd>{time(status.last_entry_at, tz)}</dd>
        </div>
        <div>
          <dt>Последний выход</dt>
          <dd>{time(status.last_exit_at, tz)}</dd>
        </div>
      </dl>
    </section>
  );
}

function absenceTitle(state: string): string {
  if (state === 'SICK_LEAVE') return 'Больничный';
  if (state === 'VACATION') return 'Отпуск';
  return 'Отсутствие';
}
