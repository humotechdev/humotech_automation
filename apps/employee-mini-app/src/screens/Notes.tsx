/**
 * Лента уведомлений сотрудника.
 *
 * Экран появился не ради макета, а потому что без него колокольчик
 * в шапке и карточка объявления на главной вели в «Профиль», где ни
 * уведомлений, ни объявления нет. Кнопка, открывающая не то, что
 * обещает, хуже отсутствующей.
 *
 * Запрос здесь не свой: лента уже загружена секцией на главной и
 * передаётся сюда готовой. Второй запрос за теми же строками означал бы
 * два счётчика непрочитанного, расходящихся между шапкой и списком.
 *
 * Отметка о прочтении уходит на сервер, а не гасится на месте:
 * счётчик обязан погаснуть и на втором устройстве тоже.
 *
 * Объявлений для офиса, региона или всей организации в проекте нет —
 * нет ни модели, ни адресации. Здесь только личные уведомления
 * сотрудника, и называются они уведомлениями.
 */

import type { Note } from '../api';
import type { Section } from '../sections';
import { dayLabel, time } from '../format';
import { BellIcon } from '../ui/icons';
import { SectionHeader } from '../ui/primitives';
import { EmptyState, ErrorState, LoadingScreen } from '../ui/states';

export function Notes({
  section,
  timeZone,
  onRead,
}: {
  section: Section<{ unread: number; items: Note[] }>;
  timeZone: string;
  onRead: (note: Note) => void;
}) {
  const items = section.data?.items ?? [];

  return (
    <>
      <SectionHeader title="Уведомления" />

      {section.loading && !section.data && <LoadingScreen label="Загружаем" />}

      {section.error && !section.data && (
        <ErrorState message={section.error} onRetry={section.reload} />
      )}

      {section.data && !items.length && (
        <EmptyState
          icon={<BellIcon size={28} />}
          title="Уведомлений нет"
          description="Здесь появятся сообщения о заявках и отметках."
        />
      )}

      {items.length > 0 && (
        <ul className="note-feed">
          {items.map((note) => (
            <li key={note.id}>
              {/* Кнопка — только у непрочитанного: нажатие гасит точку,
                  и это всё, что здесь можно сделать. У прочитанного
                  действия нет, и кнопкой оно притворяться не должно —
                  нажатие без отклика читается как поломка. */}
              {note.is_read ? (
                <div className="note-card">
                  <span className="note-card-head">
                    <span className="note-card-title">
                      {note.title ?? 'Уведомление'}
                    </span>
                  </span>
                  <span className="note-card-body">{note.body}</span>
                  <span className="note-card-when">
                    {stamp(note.sent_at, timeZone)}
                  </span>
                </div>
              ) : (
                <button
                  type="button"
                  className="note-card note-card-new"
                  onClick={() => onRead(note)}
                  aria-label={`Отметить прочитанным: ${note.title ?? 'уведомление'}`}
                >
                  <span className="note-card-head">
                    <span className="note-card-title">
                      {note.title ?? 'Уведомление'}
                    </span>
                    <span className="note-card-dot" aria-hidden="true" />
                  </span>
                  <span className="note-card-body">{note.body}</span>
                  <span className="note-card-when">
                    {stamp(note.sent_at, timeZone)}
                  </span>
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

/** «4 сентября, 13:24». Без года: лента живёт неделями, а не годами. */
function stamp(iso: string | null, timeZone: string): string {
  if (!iso) return '';
  const day = new Date(iso).toLocaleDateString('sv-SE', { timeZone });
  return `${dayLabel(day)}, ${time(iso, timeZone)}`;
}
