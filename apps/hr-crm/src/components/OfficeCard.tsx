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
 *
 * Карточка плотная: обзор целиком помещается в правую колонку без
 * прокрутки. Факты идут строками и колонками, а не отдельной плашкой
 * на каждый, — так в той же высоте видно в несколько раз больше.
 * Классы — свои, с префиксом `ofc-`: общие `.queue`, `.facts`, `.trio`
 * живут и на главной, и сжимать их здесь значило бы менять её.
 */

import { useState } from 'react';
import { Link } from 'react-router-dom';

import * as api from '../api/crm';
import { AppIcon } from './AppIcon';
import { AppSelectField } from './AppSelect';
import { initials } from './AppShell';
import { messageFor } from '../api/errors';
import { formatTime, longDate, today, useBlock, type Block } from '../features/dashboard/data';
import { needsSetup, type OfficeStats } from '../pages/OfficesPage';

const TABS = ['Обзор', 'Сотрудники', 'QR и геозона'] as const;

export const DIRECTION: Record<string, string> = {
  ENTRY: 'Только вход',
  EXIT: 'Только выход',
  BOTH: 'Вход и выход',
};

type Props = {
  row: OfficeStats;
  canManage: boolean;
  updated?: Date | null;
  onClose: () => void;
};

export function OfficeCard({ row, canManage, updated = null, onClose }: Props) {
  const [tab, setTab] = useState<(typeof TABS)[number]>('Обзор');
  const office = row.office;
  const active = office.status === 'ACTIVE';

  return (
    <aside className="ofc" aria-label="Карточка офиса">
      <header className="ofc-head">
        <span className="ofc-head__icon" aria-hidden="true">
          <AppIcon name="building" size={18} />
        </span>
        <div className="ofc-head__text">
          {/* Название и состояние — в одной строке. Состояний у офиса
              два: включён он или выключен. «Требует внимания» — это про
              настройку, и ему место в «Контроле доступа», а не рядом с
              названием, где его читали как третий статус. */}
          <p className="ofc-head__name" title={office.name}>
            {office.name}
            <span className={`ofc-state ofc-state--${active ? 'ok' : 'off'}`}>
              {active ? 'Активен' : 'Неактивен'}
            </span>
          </p>
          <p className="ofc-head__meta">
            <span className="ofc-head__id">{office.region_name ?? ''}</span>
          </p>
        </div>
        <button type="button" className="ofc-close" aria-label="Закрыть" onClick={onClose}>
          <AppIcon name="close" size={16} />
        </button>
      </header>

      <div className="ofc-tabs" role="tablist" aria-label="Разделы карточки офиса">
        {TABS.map((item) => (
          <button key={item} type="button" role="tab" aria-selected={item === tab}
                  className={item === tab ? 'ofc-tab ofc-tab--on' : 'ofc-tab'}
                  onClick={() => setTab(item)}>
            {item}
          </button>
        ))}
      </div>

      <div className="ofc-body">
        {tab === 'Обзор' && <Overview row={row} canManage={canManage} updated={updated} />}
        {tab === 'Сотрудники' && <Staff officeId={office.id} />}
        {tab === 'QR и геозона' && <Points row={row} canManage={canManage} />}
      </div>
    </aside>
  );
}

// --- обзор -----------------------------------------------------------------

function Overview({ row, canManage, updated }: {
  row: OfficeStats; canManage: boolean; updated: Date | null;
}) {
  const office = row.office;
  const count = (key: string) => row.counts[key] ?? 0;
  const inOffice = count('IN_OFFICE');
  // Ушедшие входят в «по графику»: день у них уже случился.
  const left = count('LEFT');
  const missing = count('NOT_COME');
  const staff = Object.values(row.counts).reduce((sum, value) => sum + value, 0);
  const placed = office.latitude !== null && office.longitude !== null;

  return (
    <>
      <p className="ofc-date">
        {longDate(today())} · по данным отметок
        {updated && <> · обновлено в {formatTime(updated)}</>}
      </p>

      <ul className="ofc-metrics">
        <Metric label="В штате" value={staff} />
        <Metric label="По графику" value={inOffice + left + missing} />
        <Metric label="В офисе" value={inOffice} tone="ok" />
        <Metric label="Нет отметки" value={missing} tone={missing > 0 ? 'warn' : undefined} />
      </ul>

      <p className="ofc-line">
        Опоздали: <b>{row.late ?? '—'}</b>
        <i aria-hidden="true">·</i>
        В отпуске: <b>{count('VACATION')}</b>
        <i aria-hidden="true">·</i>
        На больничном: <b>{count('SICK_LEAVE')}</b>
      </p>

      <section className="ofc-block" aria-label="Офис">
        <h3 className="ofc-block__title">Офис</h3>
        {/* Пояс в стране один, и строка про него ничего не решала.
            Адрес виден в настройке офиса, где его и меняют. */}
        <dl className="ofc-facts">
          <div><dt>Регион</dt><dd>{office.region_name ?? '—'}</dd></div>
          <div><dt>Название</dt><dd title={office.name}>{office.name}</dd></div>
        </dl>
      </section>

      <section className="ofc-block" aria-label="Контроль доступа">
        <h3 className="ofc-block__title">Контроль доступа</h3>
        <dl className="ofc-facts ofc-facts--three">
          <div>
            <dt>QR-точки</dt>
            <dd>{row.pointsKnown === false ? 'нет доступа' : row.points.length}</dd>
          </div>
          <div>
            <dt>Геозона</dt>
            <dd>{office.geofence_radius_m ? `${office.geofence_radius_m} м` : 'не задана'}</dd>
          </div>
          <div>
            <dt>Координаты</dt>
            <dd className={placed ? undefined : 'ofc-warn'}>{placed ? 'настроены' : 'не настроены'}</dd>
          </div>
        </dl>
        <p className={needsSetup(row) ? 'ofc-note ofc-note--warn' : 'ofc-note'}>
          Геолокация: {geoNote(row)}
        </p>
      </section>

      <div className="ofc-actions">
        <Link className="ofc-btn" to={`/offices/${office.id}/setup`}>
          <AppIcon name="building" size={16} />
          Открыть офис
        </Link>
        {canManage && (
          <Link className="ofc-btn ofc-btn--blue" to={`/offices/${office.id}/setup?tab=geo`}>
            <AppIcon name="settings" size={16} />
            Настроить
          </Link>
        )}
      </div>
    </>
  );
}

function Metric({ label, value, tone }: { label: string; value: number; tone?: 'ok' | 'warn' | undefined }) {
  return (
    <li className={tone ? `ofc-metric ofc-metric--${tone}` : 'ofc-metric'}>
      <b>{value}</b>
      <span>{label}</span>
    </li>
  );
}

/** Что именно требует геолокацию — по фактическим точкам, а не «обеим». */
function geoNote(row: OfficeStats): string {
  if (row.pointsKnown === false) return 'точки недоступны';
  const wants = row.points.filter((point) => point.require_geolocation);
  if (row.points.length === 0) return 'QR-точек нет';
  if (wants.length === 0) return 'ни одна точка её не требует';
  if (wants.length === row.points.length) {
    return `Обязательна для всех ${row.points.length} точек`;
  }
  return `обязательна для ${wants.length} из ${row.points.length} точек`;
}

// --- основное: правка офиса ------------------------------------------------

/**
 * Название, адрес и часовой пояс офиса. Расположение и радиус правятся
 * на карте во вкладке «Геолокация» — вводить координаты числами HR не
 * должен.
 */
export function OfficeForm({ office, onCancel, onDone }: {
  office: api.OfficeFull; onCancel?: () => void; onDone: (office: api.OfficeFull) => void;
}) {
  // Ни адреса, ни часового пояса. Адрес подставляется сам, когда точку
  // ставят на карту, — набирать его руками значит держать два ответа на
  // вопрос «где офис». Пояс в Узбекистане один, и выбирать тут нечего.
  const [form, setForm] = useState({
    name: office.name,
    // Пустая строка, а не null: `AppSelectField` работает со строкой, и
    // «регион не выбран» здесь — это не значение, а его отсутствие.
    region_id: office.region_id ?? '',
  });
  const [regions] = useBlock((signal) => api.regions(signal), 'office-regions');
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
      const saved = await api.updateOffice(office.id, {
        name: form.name,
        region_id: form.region_id,
      });
      onDone(saved);
    } catch (error) {
      // Введённое остаётся в форме: набирать заново из-за отказа сервера
      // — худшее, что можно предложить.
      setFailed(messageFor(error));
      const details = (error as { fields?: Record<string, string[]> }).fields;
      if (details) setFields(details);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="ofs-form">
      <Field label="Название офиса" value={form.name} onChange={set('name')} errors={fields['name']} />

      <label className="ofs-field">
        <span className="ofs-field__label">Регион</span>
        <AppSelectField label="Регион" value={form.region_id} searchable
                        onChange={set('region_id')}>
          {regions.state === 'ready'
            ? regions.data.items.map((one) => (
                <option key={one.id} value={one.id}>{one.name}</option>
              ))
            : <option value={form.region_id}>{office.region_name ?? '—'}</option>}
        </AppSelectField>
        {fields['region_id'] && (
          <span className="ofs-field__error">{fields['region_id'].join(' ')}</span>
        )}
      </label>

      {failed && <p className="ofs-alert" role="alert">{failed}</p>}

      <div className="ofs-actions">
        <button type="button" className="ofs-btn ofs-btn--blue" disabled={sending}
                onClick={() => void save()}>
          {sending ? 'Сохраняем…' : 'Сохранить изменения'}
        </button>
        {onCancel && (
          <button type="button" className="ofs-btn" disabled={sending} onClick={onCancel}>
            Отменить изменения
          </button>
        )}
      </div>
    </div>
  );
}

export function Field({ label, value, onChange, errors, placeholder, inputMode }: {
  label: string; value: string; onChange: (value: string) => void;
  errors?: string[] | undefined; placeholder?: string | undefined;
  inputMode?: 'decimal' | 'numeric' | undefined;
}) {
  return (
    <label className="ofs-field">
      <span className="ofs-field__label">{label}</span>
      <input
        className="ofs-field__input"
        value={value}
        placeholder={placeholder ?? ''}
        aria-label={label}
        aria-invalid={errors ? true : undefined}
        {...(inputMode ? { inputMode } : {})}
        onChange={(event) => onChange(event.target.value)}
      />
      {errors && <span className="ofs-field__error">{errors.join(' ')}</span>}
    </label>
  );
}

// --- сотрудники ------------------------------------------------------------

export function Staff({ officeId }: { officeId: string }) {
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
      <label className="ofc-find">
        <AppIcon name="search" size={16} />
        <input type="search" value={search} placeholder="Поиск сотрудника"
               aria-label="Поиск сотрудника офиса"
               onChange={(event) => { setSearch(event.target.value); setCursor(''); }} />
      </label>

      <Body block={list} name="сотрудников">
        {(data) =>
          data.items.length === 0 ? (
            <p className="ofc-empty">В этом офисе никого не назначено.</p>
          ) : (
            <ul className="ofc-rows">
              {data.items.map((person) => (
                <li key={person.id} className="ofc-row">
                  <span className="ofc-row__face" aria-hidden="true">{initials(person.full_name)}</span>
                  <span className="ofc-row__text">
                    <b title={person.full_name}>{person.full_name}</b>
                    <small>{person.current_assignment?.position_name ?? '—'}</small>
                  </span>
                  <Link className="ofc-link" to={`/employees/${person.id}`}>Открыть</Link>
                </li>
              ))}
            </ul>
          )
        }
      </Body>

      <div className="ofc-pager">
        <button type="button" className="ofc-btn" disabled={!cursor} onClick={() => setCursor('')}>
          Назад
        </button>
        <button type="button" className="ofc-btn"
                disabled={list.state !== 'ready' || !list.data.has_more}
                onClick={() => list.state === 'ready' && setCursor(list.data.next_cursor ?? '')}>
          Далее
        </button>
      </div>
      <p className="ofc-note">
        Перевод в другой офис выполняется сменой назначения — там сохраняется история.
      </p>
    </>
  );
}

// --- QR и геозона ----------------------------------------------------------

function Points({ row, canManage }: { row: OfficeStats; canManage: boolean }) {
  const [devices] = useBlock((signal) => api.qrDevices(signal), `devices|${row.office.id}`);
  const office = row.office;
  const placed = office.latitude !== null && office.longitude !== null;

  return (
    <>
      <dl className="ofc-facts ofc-facts--three">
        <div>
          <dt>Геозона</dt>
          <dd>{office.geofence_radius_m ? `${office.geofence_radius_m} м` : 'не задана'}</dd>
        </div>
        <div>
          <dt>Координаты</dt>
          <dd className={placed ? undefined : 'ofc-warn'}>
            {placed ? `${Number(office.latitude).toFixed(5)}, ${Number(office.longitude).toFixed(5)}` : 'не заданы'}
          </dd>
        </div>
        <div>
          <dt>QR-точки</dt>
          <dd>{row.pointsKnown === false ? 'нет доступа' : row.points.length}</dd>
        </div>
      </dl>
      <p className={needsSetup(row) ? 'ofc-note ofc-note--warn' : 'ofc-note'}>
        Геолокация: {geoNote(row)}
      </p>

      {canManage && (
        <Link className="ofc-btn ofc-btn--blue ofc-btn--wide" to={`/offices/${office.id}/setup?tab=qr`}>
          <AppIcon name="settings" size={16} />
          Настроить офис
        </Link>
      )}

      {row.points.length === 0 ? (
        <p className="ofc-empty">Точек нет.</p>
      ) : (
        <ul className="ofc-rows ofc-rows--scroll">
          {row.points.map((point) => {
            const bound =
              devices.state === 'ready'
                ? devices.data.find((device) => device.qr_point_id === point.id)
                : undefined;
            return (
              <li key={point.id} className="ofc-row">
                <span className="ofc-row__text">
                  <b title={point.name}>{point.name}</b>
                  <small>
                    {DIRECTION[point.direction_mode] ?? point.direction_mode}
                    {point.qr_mode === 'STATIC' ? ' · печатный код' : ''}
                    {point.require_geolocation ? ' · геолокация' : ''}
                    {point.qr_mode === 'ROTATING' && (
                      <>
                        {' · '}
                        {bound
                          ? `Экран привязан${bound.last_seen_at ? `, был на связи ${bound.last_seen_at.slice(11, 16)}` : ', на связи не был'}`
                          : 'Экран не привязан'}
                      </>
                    )}
                  </small>
                </span>
                <span className={point.is_active ? 'ofc-state ofc-state--ok' : 'ofc-state ofc-state--off'}>
                  <i aria-hidden="true" />
                  {point.is_active ? 'Активна' : 'Выкл.'}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

function Body<T>({ block, name, children }: {
  block: Block<T>; name: string; children: (data: T) => React.ReactNode;
}) {
  if (block.state === 'loading') return <p className="ofc-empty">Загружаем {name}…</p>;
  if (block.state === 'denied') return <p className="ofc-empty">Нет доступа.</p>;
  if (block.state === 'error') return <p className="ofc-empty ofc-empty--bad">Не удалось загрузить {name}.</p>;
  return <>{children(block.data)}</>;
}
