/**
 * Карточка офиса: обзор, сотрудники, QR-точки и геозона.
 *
 * Три вещи, которые здесь намеренно НЕ смешиваются:
 *
 * 1. Статус офиса и состояние его настройки. Активный офис может
 *    требовать геозоны.
 * 2. Настроенная геозона офиса и обязательность геолокации у QR-точки.
 *    Это независимые настройки, и заполнение координат само по себе
 *    ничего не включает.
 * 3. «Экран привязан» и «экран доступен». Привязка — запись в базе,
 *    доступность — свежий сигнал от самого экрана. Называть первое
 *    вторым значит показывать работающим то, что могло погаснуть месяц
 *    назад.
 */

import { useState } from 'react';

import * as api from '../api/crm';
import { Icon } from './nav-icons';
import { initials } from './AppShell';
import { messageFor } from '../api/errors';
import { longDate, useBlock, type Block } from '../features/dashboard/data';
import { needsSetup, type OfficeStats } from '../pages/OfficesPage';

const TABS = ['Обзор', 'Сотрудники', 'QR и геозона'] as const;

const DIRECTION: Record<string, string> = {
  ENTRY: 'Только вход',
  EXIT: 'Только выход',
  BOTH: 'Вход и выход',
};

type Props = {
  row: OfficeStats;
  canManage: boolean;
  onClose: () => void;
  onChanged: () => void;
};

export function OfficeCard({ row, canManage, onClose, onChanged }: Props) {
  const [tab, setTab] = useState<(typeof TABS)[number]>('Обзор');
  const office = row.office;

  return (
    <aside className="panel side-panel" aria-label="Карточка офиса">
      <header className="side-panel__head">
        <span className="avatar avatar--square">
          <Icon name="building" size={18} />
        </span>
        <span className="side-panel__title side-panel__title--stack">
          <span>{office.name}</span>
          <span className="who__id">{office.region_name ?? '—'}</span>
        </span>
        <button type="button" className="tool" aria-label="Закрыть" onClick={onClose}>✕</button>
      </header>

      <div className="side-panel__meta">
        <span className="state">
          <i className="state__dot" />
          {office.status === 'ACTIVE' ? 'Активен' : office.status}
        </span>
        <span className="who__id">{office.code}</span>
      </div>

      <div className="tabs tabs--drawer" role="tablist">
        {TABS.map((item) => (
          <button key={item} type="button" role="tab" aria-selected={item === tab}
                  className={item === tab ? 'tab tab--on' : 'tab'}
                  onClick={() => setTab(item)}>
            {item}
          </button>
        ))}
      </div>

      <div className="side-panel__body">
        {tab === 'Обзор' && (
          <Overview row={row} canManage={canManage} onChanged={onChanged} />
        )}
        {tab === 'Сотрудники' && <Staff officeId={office.id} />}
        {tab === 'QR и геозона' && <Points row={row} />}
      </div>
    </aside>
  );
}

// --- обзор -----------------------------------------------------------------

function Overview({ row, canManage, onChanged }: {
  row: OfficeStats; canManage: boolean; onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const office = row.office;
  const inOffice = row.counts['IN_OFFICE'] ?? 0;
  const left = row.counts['LEFT'] ?? 0;
  const missing = row.counts['NOT_COME'] ?? 0;
  const staff = Object.values(row.counts).reduce((sum, value) => sum + value, 0);

  if (editing) {
    return (
      <OfficeForm
        office={office}
        onCancel={() => setEditing(false)}
        onDone={() => {
          setEditing(false);
          onChanged();
        }}
      />
    );
  }

  return (
    <>
      <p className="side-panel__label">
        {longDate(new Date().toISOString().slice(0, 10))} · По данным отметок
      </p>
      <ul className="trio">
        <li><span className="trio__label">В штате</span><span className="trio__value">{staff}</span></li>
        <li><span className="trio__label">По графику</span>
            <span className="trio__value">{inOffice + left + missing}</span></li>
        <li><span className="trio__label">В офисе</span><span className="trio__value">{inOffice}</span></li>
      </ul>
      <p className="side-panel__text muted">
        Уже ушли: {left} <span className="dot">·</span> Нет отметки: {missing}
      </p>

      <dl className="facts">
        <div className="facts__row"><dt>Регион</dt><dd>{office.region_name ?? '—'}</dd></div>
        <div className="facts__row">
          <dt>Адрес</dt>
          <dd>{office.address || <span className="muted">Не указан</span>}</dd>
        </div>
        <div className="facts__row"><dt>Часовой пояс</dt><dd>{office.timezone}</dd></div>
      </dl>

      <div className="geo">
        <Icon name="pin" size={18} />
        <span className="geo__text">
          <span className="geo__head">
            Геозона
            <span className="pill">
              {needsSetup(row)
                ? 'Требует настройки'
                : office.geofence_radius_m
                  ? 'Настроена'
                  : 'Не задана'}
            </span>
          </span>
          <span className="geo__note">
            {office.geofence_radius_m
              ? `Радиус ${office.geofence_radius_m} м`
              : 'Координаты и радиус не заданы'}
          </span>
          <span className="geo__note">{geoNote(row)}</span>
        </span>
      </div>

      <p className="side-panel__label">QR-точки <span className="chip">{row.points.length}</span></p>
      {row.points.length === 0 ? (
        <p className="empty">Точек нет.</p>
      ) : (
        <ul className="queue">
          {row.points.slice(0, 3).map((point) => (
            <li key={point.id} className="queue__row">
              <Icon name="database" size={16} />
              <span className="queue__text">
                <span className="queue__title">{point.name}</span>
                <span className="queue__note">
                  {DIRECTION[point.direction_mode] ?? point.direction_mode}
                </span>
              </span>
              <span className="queue__count">{point.is_active ? 'Активна' : 'Выкл.'}</span>
            </li>
          ))}
        </ul>
      )}

      {canManage && (
        <button type="button" className="btn btn--dark" onClick={() => setEditing(true)}>
          <Icon name="settings" size={16} />
          Редактировать офис
        </button>
      )}
      <a className="linky" href={`/attendance?office_id=${office.id}`}>
        Открыть посещаемость →
      </a>
    </>
  );
}

/** Что именно требует геолокацию — по фактическим точкам, а не «обеим». */
function geoNote(row: OfficeStats): string {
  const wants = row.points.filter((point) => point.require_geolocation);
  if (row.points.length === 0) return 'QR-точек нет';
  if (wants.length === 0) return 'Ни одна точка геолокацию не требует';
  if (wants.length === row.points.length) {
    return `Обязательна для всех ${row.points.length} точек`;
  }
  return `Обязательна для ${wants.length} из ${row.points.length} точек`;
}

// --- форма офиса -----------------------------------------------------------

/**
 * Правка офиса. Поля — те, что есть у модели; новых обязательных здесь
 * не заводится.
 *
 * Координаты и радиус можно задать, но обязательность геолокации у
 * QR-точки этим НЕ включается: это отдельная настройка точки, и
 * включать её молча было бы подменой чужого решения.
 */
function OfficeForm({ office, onCancel, onDone }: {
  office: api.OfficeFull; onCancel: () => void; onDone: () => void;
}) {
  const [form, setForm] = useState({
    name: office.name,
    address: office.address ?? '',
    timezone: office.timezone,
    latitude: office.latitude === null ? '' : String(office.latitude),
    longitude: office.longitude === null ? '' : String(office.longitude),
    geofence_radius_m: office.geofence_radius_m === null ? '' : String(office.geofence_radius_m),
  });
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [fields, setFields] = useState<Record<string, string[]>>({});

  const set = (key: keyof typeof form) => (value: string) =>
    setForm((was) => ({ ...was, [key]: value }));

  async function save() {
    if (sending) return;
    setSending(true);
    setFailed(null);
    setFields({});
    try {
      await api.updateOffice(office.id, {
        name: form.name,
        address: form.address || null,
        timezone: form.timezone,
        latitude: form.latitude === '' ? null : Number(form.latitude),
        longitude: form.longitude === '' ? null : Number(form.longitude),
        geofence_radius_m:
          form.geofence_radius_m === '' ? null : Number(form.geofence_radius_m),
      });
      onDone();
    } catch (error) {
      // Введённое остаётся в форме: набирать координаты заново из-за
      // отказа сервера — худшее, что можно предложить.
      setFailed(messageFor(error));
      const details = (error as { fields?: Record<string, string[]> }).fields;
      if (details) setFields(details);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="form-grid">
      <Field label="Название" value={form.name} onChange={set('name')} errors={fields['name']} />
      <Field label="Адрес" value={form.address} onChange={set('address')}
             errors={fields['address']} placeholder="Не указан" />
      <Field label="Часовой пояс" value={form.timezone} onChange={set('timezone')}
             errors={fields['timezone']} />

      <p className="side-panel__label">Геозона</p>
      <p className="side-panel__text muted">
        Координаты и радиус нужны для проверки присутствия. Обязательность
        геолокации включается у самой QR-точки — здесь она не меняется.
      </p>
      <Field label="Широта" value={form.latitude} onChange={set('latitude')}
             errors={fields['latitude']} placeholder="от −90 до 90" />
      <Field label="Долгота" value={form.longitude} onChange={set('longitude')}
             errors={fields['longitude']} placeholder="от −180 до 180" />
      <Field label="Радиус, м" value={form.geofence_radius_m}
             onChange={set('geofence_radius_m')} errors={fields['geofence_radius_m']} />

      {failed && <p className="empty empty--bad" role="alert">{failed}</p>}

      <div className="side-panel__actions">
        <button type="button" className="btn btn--dark" disabled={sending}
                onClick={() => void save()}>
          {sending ? 'Сохраняем…' : 'Сохранить'}
        </button>
        <button type="button" className="btn" disabled={sending} onClick={onCancel}>
          Отмена
        </button>
      </div>
    </div>
  );
}

function Field({ label, value, onChange, errors, placeholder }: {
  label: string; value: string; onChange: (value: string) => void;
  errors?: string[] | undefined; placeholder?: string | undefined;
}) {
  return (
    <label className="form-grid__field">
      <span className="form-grid__label">{label}</span>
      <input
        className="form-grid__input"
        value={value}
        placeholder={placeholder ?? ''}
        aria-label={label}
        aria-invalid={errors ? true : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {errors && <span className="form-grid__error">{errors.join(' ')}</span>}
    </label>
  );
}

// --- сотрудники ------------------------------------------------------------

function Staff({ officeId }: { officeId: string }) {
  const [search, setSearch] = useState('');
  const [cursor, setCursor] = useState('');

  const [list] = useBlock(
    (signal) =>
      api.employees(
        { office_id: officeId, limit: '10', ...(search ? { search } : {}), ...(cursor ? { cursor } : {}) },
        signal,
      ),
    `staff|${officeId}|${search}|${cursor}`,
  );

  return (
    <>
      <label className="find find--wide">
        <Icon name="search" size={16} />
        <input type="search" value={search} placeholder="Поиск сотрудника"
               aria-label="Поиск сотрудника офиса"
               onChange={(event) => { setSearch(event.target.value); setCursor(''); }} />
      </label>

      <Body block={list} name="сотрудников">
        {(data) =>
          data.items.length === 0 ? (
            <p className="empty">В этом офисе никого не назначено.</p>
          ) : (
            <ul className="queue">
              {data.items.map((person) => (
                <li key={person.id} className="queue__row">
                  <span className="avatar avatar--sm">{initials(person.full_name)}</span>
                  <span className="queue__text">
                    <span className="queue__title">{person.full_name}</span>
                    <span className="queue__note">
                      {person.current_assignment?.position_name ?? '—'}
                    </span>
                  </span>
                  <a className="linky" href={`/employees?employee=${person.id}`}>Открыть</a>
                </li>
              ))}
            </ul>
          )
        }
      </Body>

      <div className="side-panel__actions">
        <button type="button" className="btn" disabled={!cursor} onClick={() => setCursor('')}>
          Назад
        </button>
        <button type="button" className="btn btn--dark"
                disabled={list.state !== 'ready' || !list.data.has_more}
                onClick={() => list.state === 'ready' && setCursor(list.data.next_cursor ?? '')}>
          Далее
        </button>
      </div>
      <p className="side-panel__text muted">
        Перевод в другой офис выполняется сменой назначения — там сохраняется история.
      </p>
    </>
  );
}

// --- QR и геозона ----------------------------------------------------------

function Points({ row }: { row: OfficeStats }) {
  const [devices] = useBlock((signal) => api.qrDevices(signal), `devices|${row.office.id}`);

  return (
    <>
      <div className="geo">
        <Icon name="pin" size={18} />
        <span className="geo__text">
          <span className="geo__head">
            Геозона
            <span className="pill">
              {row.office.geofence_radius_m ? 'Настроена' : 'Не задана'}
            </span>
          </span>
          <span className="geo__note">
            {row.office.latitude !== null && row.office.longitude !== null
              ? `${row.office.latitude}, ${row.office.longitude}`
              : 'Координаты не заданы'}
          </span>
          <span className="geo__note">{geoNote(row)}</span>
        </span>
      </div>

      <p className="side-panel__label">QR-точки</p>
      {row.points.length === 0 ? (
        <p className="empty">Точек нет.</p>
      ) : (
        <ul className="queue">
          {row.points.map((point) => {
            const bound =
              devices.state === 'ready'
                ? devices.data.find((device) => device.qr_point_id === point.id)
                : undefined;
            return (
              <li key={point.id} className="queue__row">
                <Icon name="database" size={16} />
                <span className="queue__text">
                  <span className="queue__title">{point.name}</span>
                  <span className="queue__note">
                    {DIRECTION[point.direction_mode] ?? point.direction_mode}
                    {point.require_geolocation ? ' · геолокация обязательна' : ''}
                  </span>
                  <span className="queue__note">
                    {bound
                      ? `Экран привязан${bound.last_seen_at ? `, был на связи ${bound.last_seen_at.slice(11, 16)}` : ', на связи не был'}`
                      : 'Экран не привязан'}
                  </span>
                </span>
                <span className="queue__count">{point.is_active ? 'Активна' : 'Выкл.'}</span>
              </li>
            );
          })}
        </ul>
      )}
      <p className="side-panel__text muted">
        Создание точек, перевыпуск токена и сопряжение экрана в API CRM пока не
        открыты — это делается командой на сервере.
      </p>
    </>
  );
}

function Body<T>({ block, name, children }: {
  block: Block<T>; name: string; children: (data: T) => React.ReactNode;
}) {
  if (block.state === 'loading') return <p className="empty">Загружаем {name}…</p>;
  if (block.state === 'denied') return <p className="empty">Нет доступа.</p>;
  if (block.state === 'error') return <p className="empty empty--bad">Не удалось загрузить {name}.</p>;
  return <>{children(block.data)}</>;
}
