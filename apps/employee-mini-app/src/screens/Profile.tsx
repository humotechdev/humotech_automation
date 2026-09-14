/**
 * Профиль и помощь. Открывается аватаром в шапке, вкладки для него нет:
 * сюда заходят раз в месяц, а вкладка забрала бы место у того, чем
 * пользуются каждый день.
 *
 * Внутренних идентификаторов здесь нет ни одного, хотя в ответе API они
 * есть. Показывать человеку UUID его записи незачем: помочь он с ним
 * никому не сможет, а в скриншот, отправленный в общий чат, тот попадёт.
 * Для обращения в отдел кадров есть табельный номер.
 *
 * Раздел помощи — статический текст. Ни одного обращения к ИИ: на вопрос
 * «почему не засчитан вчерашний день» отвечает человек, у которого есть
 * доступ к данным и право их исправить.
 */

import type { Profile as ProfileData, Status } from '../api';
import { PRESENCE } from '../format';
import { initials } from '../ui/TopBar';
import { ClockIcon, OfficeIcon } from '../ui/icons';
import {
  Card,
  ListItem,
  SecondaryButton,
  SectionHeader,
  StatusBadge,
} from '../ui/primitives';

const TELEGRAM_STATUS: Record<string, { text: string; tone: 'success' | 'warning' | 'danger' }> =
  {
    ACTIVE: { text: 'привязан', tone: 'success' },
    PENDING: { text: 'ждёт подтверждения кадров', tone: 'warning' },
    REVOKED: { text: 'привязка отозвана', tone: 'danger' },
    BLOCKED: { text: 'привязка заблокирована', tone: 'danger' },
  };

const EMPLOYMENT: Record<string, string> = {
  FULL_TIME: 'полная занятость',
  PART_TIME: 'частичная занятость',
  CONTRACT: 'договор',
  INTERN: 'стажировка',
};

const WORK_MODE: Record<string, string> = {
  ONSITE: 'в офисе',
  REMOTE: 'удалённо',
  HYBRID: 'смешанный',
};

export function Profile({
  profile,
  status,
  version,
  onSignOut,
}: {
  profile: ProfileData;
  status: Status | null;
  version: string;
  onSignOut: () => void;
}) {
  const telegram = profile.telegram
    ? TELEGRAM_STATUS[profile.telegram.status]
    : undefined;

  return (
    <>
      <div className="app-header">
        <div className="app-header-text">
          <p className="app-header-name">{profile.employee.full_name}</p>
          <p className="app-header-place">
            <OfficeIcon size={14} />
            <span>
              {[profile.position?.name, profile.department?.name]
                .filter(Boolean)
                .join(' · ') || 'Должность не указана'}
            </span>
          </p>
        </div>
        <span className="avatar" aria-hidden="true">
          {initials(profile.employee.full_name)}
        </span>
      </div>

      <Card>
        <SectionHeader title="Место работы" />
        <ListItem title="Офис" trailing={profile.office.name} />
        <ListItem title="Часовой пояс" trailing={profile.office.timezone} />
        <ListItem
          title="Табельный номер"
          trailing={profile.employee.employee_number}
        />
        {profile.assignment && (
          <>
            <ListItem
              title="Занятость"
              trailing={
                EMPLOYMENT[profile.assignment.employment_type] ??
                profile.assignment.employment_type
              }
            />
            <ListItem
              title="Формат работы"
              trailing={
                WORK_MODE[profile.assignment.work_mode] ??
                profile.assignment.work_mode
              }
            />
          </>
        )}
      </Card>

      {status && (
        <Card>
          <SectionHeader title="Сегодня" />
          <ListItem
            icon={<ClockIcon size={18} />}
            title="Рабочий график"
            trailing={
              status.scheduled_start && status.scheduled_end
                ? `${status.scheduled_start.slice(0, 5)}–${status.scheduled_end.slice(0, 5)}`
                : 'не назначен'
            }
          />
          <ListItem
            title="Состояние"
            trailing={PRESENCE[status.state] ?? status.state}
          />
        </Card>
      )}

      <Card>
        <SectionHeader title="Telegram" />
        <ListItem
          title="Привязка"
          trailing={
            telegram ? (
              <StatusBadge tone={telegram.tone} dot>
                {telegram.text}
              </StatusBadge>
            ) : (
              '—'
            )
          }
        />
        {profile.telegram?.username && (
          <ListItem title="Аккаунт" trailing={`@${profile.telegram.username}`} />
        )}
      </Card>

      <Card>
        <SectionHeader title="Помощь" />
        <p className="muted">
          Отметка не прошла, время неверное, заявка потерялась — это
          к отделу кадров. Приложение показывает данные, но не исправляет их.
        </p>
        <ListItem
          title="Отдел кадров"
          subtitle="Обращайтесь в рабочее время, назовите табельный номер"
          trailing={profile.employee.employee_number}
        />
        <ListItem
          title="Забыли отметиться"
          subtitle="Задним числом отметку не поставить — нужна правка от кадров"
        />
        <ListItem
          title="Код не сканируется"
          subtitle="Код на экране меняется каждые 30 секунд, попробуйте новый"
        />
      </Card>

      <Card>
        <SectionHeader title="Приложение" />
        <ListItem title="Версия" trailing={version} />
        <SecondaryButton onClick={onSignOut} wide>
          Выйти и войти заново
        </SecondaryButton>
        <p className="muted">
          Сессия закроется на этом устройстве. Привязка Telegram останется —
          отключает её только отдел кадров.
        </p>
      </Card>
    </>
  );
}
