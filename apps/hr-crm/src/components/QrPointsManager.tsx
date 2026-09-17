/**
 * QR-точки офиса: список, добавление, печатный код, перевыпуск.
 *
 * Про печатный код одно правило, и оно пришло с сервера: **секрет
 * показывается один раз** — в ответе на создание и на перевыпуск. В
 * базе лежит только его хеш, и получить код повторно нельзя ни этой
 * страницей, ни API. Поэтому QR, «Скачать PNG», «Распечатать» и
 * «Скопировать ссылку» доступны сразу после выпуска, пока окно открыто.
 * Потеряли код — «Перевыпустить QR»: прежняя наклейка перестанет
 * действовать в тот же миг, и это ровно то, что нужно, если её
 * сфотографировали.
 *
 * Картинка QR собирается в браузере из ссылки: на сервер секрет второй
 * раз не уходит, а в журнал — вовсе.
 */

import { useEffect, useState } from 'react';
import QRCode from 'qrcode';

import * as api from '../api/crm';
import { messageFor } from '../api/errors';
import { useBlock } from '../features/dashboard/data';
import { AppIcon } from './AppIcon';

const MODES = [
  { value: 'ENTRY', title: 'Вход' },
  { value: 'EXIT', title: 'Выход' },
  { value: 'BOTH', title: 'Вход и выход' },
] as const;

const MODE_TITLE: Record<string, string> = {
  ENTRY: 'Вход',
  EXIT: 'Выход',
  BOTH: 'Вход и выход',
};

type Issued = { point: api.QrPoint; link: string | null };

export function QrPointsManager({ office, canManage, located, onChanged }: {
  office: api.OfficeFull;
  canManage: boolean;
  /** Есть ли у офиса точка на карте и радиус: без них печатный код не работает. */
  located: boolean;
  /** Точки изменились — сводке слева пора пересчитать готовность. */
  onChanged?: () => void;
}) {
  const [attempt, setAttempt] = useState(0);
  const [list] = useBlock(
    (signal) => api.qrPoints({ office_id: office.id }, signal),
    `office-qr|${office.id}|${attempt}`,
  );
  const [adding, setAdding] = useState(false);
  const [issued, setIssued] = useState<Issued | null>(null);

  const reload = () => {
    setAttempt((n) => n + 1);
    onChanged?.();
  };
  const points = list.state === 'ready' ? list.data.items : [];

  return (
    <div className="ofs-qr">
      <div className="ofs-section-head">
        <div>
          <h2 className="ofs-title">QR-точки <span className="ofs-count">{points.length}</span></h2>
          <p className="ofs-sub">
            Точка — место отметки: вход, служебный вход, выход со склада. Её код
            печатается и вешается у двери.
          </p>
        </div>
        {canManage && !adding && (
          <button type="button" className="ofs-btn ofs-btn--blue" onClick={() => { setAdding(true); setIssued(null); }}>
            <AppIcon name="plus" size={16} />
            Добавить QR-точку
          </button>
        )}
      </div>

      {!located && (
        <p className="ofs-warn" role="status">
          У офиса ещё нет точки на карте. Печатный QR-код начнёт принимать отметки
          только после того, как расположение и радиус будут сохранены во вкладке
          «Геолокация».
        </p>
      )}

      {adding && (
        <NewPoint
          officeId={office.id}
          onCancel={() => setAdding(false)}
          onCreated={(result) => {
            setAdding(false);
            setIssued({ point: result.point, link: result.sticker_link });
            reload();
          }}
        />
      )}

      {issued && (
        <IssuedCode office={office} issued={issued} onClose={() => setIssued(null)} />
      )}

      {list.state === 'loading' && <p className="ofs-empty">Загружаем QR-точки…</p>}
      {list.state === 'denied' && <p className="ofs-empty">Нет доступа к QR-точкам.</p>}
      {list.state === 'error' && <p className="ofs-empty ofs-empty--bad">Не удалось загрузить QR-точки.</p>}
      {list.state === 'ready' && points.length === 0 && !adding && (
        <p className="ofs-empty">
          Точек пока нет. {canManage ? 'Добавьте первую — например, «Главный вход».' : ''}
        </p>
      )}

      {points.length > 0 && (
        <ul className="ofs-points">
          {points.map((point) => (
            <PointCard
              key={point.id}
              point={point}
              canManage={canManage}
              onChanged={reload}
              onIssued={(result) => {
                setIssued({ point: result.point, link: result.sticker_link });
                reload();
              }}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

// --- новая точка ---------------------------------------------------------------

function NewPoint({ officeId, onCancel, onCreated }: {
  officeId: string;
  onCancel: () => void;
  onCreated: (issued: api.IssuedQrPoint) => void;
}) {
  const [name, setName] = useState('');
  const [mode, setMode] = useState<'ENTRY' | 'EXIT' | 'BOTH'>('ENTRY');
  const [description, setDescription] = useState('');
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  async function create() {
    if (sending) return;
    if (!name.trim()) {
      setFailed('Назовите точку — например, «Главный вход».');
      return;
    }
    setSending(true);
    setFailed(null);
    try {
      const result = await api.createQrPoint({
        office_id: officeId,
        name: name.trim(),
        direction_mode: mode,
        ...(description.trim() ? { description: description.trim() } : {}),
      });
      onCreated(result);
    } catch (error) {
      setFailed(messageFor(error));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="ofs-card ofs-new" aria-label="Новая QR-точка">
      <h3 className="ofs-card__title">Новая QR-точка</h3>
      <div className="ofs-new__grid">
        <label className="ofs-field">
          <span className="ofs-field__label">Название</span>
          <input className="ofs-field__input" value={name} maxLength={255}
                 placeholder="Главный вход" aria-label="Название точки"
                 onChange={(event) => setName(event.target.value)} />
        </label>
        <div className="ofs-field">
          <span className="ofs-field__label">Тип</span>
          <div className="ofs-modes" role="radiogroup" aria-label="Тип точки">
            {MODES.map((item) => (
              <button key={item.value} type="button" role="radio" aria-checked={mode === item.value}
                      className={mode === item.value ? 'ofs-mode ofs-mode--on' : 'ofs-mode'}
                      onClick={() => setMode(item.value)}>
                {item.title}
              </button>
            ))}
          </div>
        </div>
        <label className="ofs-field ofs-new__wide">
          <span className="ofs-field__label">Описание — необязательно</span>
          <input className="ofs-field__input" value={description} maxLength={500}
                 placeholder="Где висит код: слева от ресепшен, у турникета"
                 aria-label="Описание точки"
                 onChange={(event) => setDescription(event.target.value)} />
        </label>
      </div>
      <p className="ofs-hint">
        {mode === 'ENTRY' && 'По этому коду отмечают только приход.'}
        {mode === 'EXIT' && 'По этому коду отмечают только уход.'}
        {mode === 'BOTH' && 'Первый скан за день — приход, следующий — уход.'}
      </p>
      {failed && <p className="ofs-alert" role="alert">{failed}</p>}
      <div className="ofs-actions">
        <button type="button" className="ofs-btn ofs-btn--blue" disabled={sending} onClick={() => void create()}>
          {sending ? 'Создаём…' : 'Создать и получить QR'}
        </button>
        <button type="button" className="ofs-btn" disabled={sending} onClick={onCancel}>Отмена</button>
      </div>
    </div>
  );
}

// --- выпущенный код ------------------------------------------------------------

function IssuedCode({ office, issued, onClose }: {
  office: api.OfficeFull;
  issued: Issued;
  onClose: () => void;
}) {
  const [image, setImage] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const { point, link } = issued;

  useEffect(() => {
    if (!link) return;
    let alive = true;
    QRCode.toDataURL(link, { width: 720, margin: 2, errorCorrectionLevel: 'M' })
      .then((url) => { if (alive) setImage(url); })
      .catch(() => { if (alive) setImage(null); });
    return () => { alive = false; };
  }, [link]);

  const fileName = `qr-${slug(office.name)}-${slug(point.name)}.png`;

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  function print() {
    if (!image) return;
    const sheet = window.open('', '_blank', 'width=720,height=900');
    if (!sheet) return;
    const title = escapeHtml(`${office.name} — ${point.name}`);
    sheet.document.write(`<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>${title}</title>
      <style>
        body{font-family:'Segoe UI',system-ui,sans-serif;margin:0;display:flex;justify-content:center}
        .sheet{width:170mm;padding:18mm 0;text-align:center;color:#13295a}
        h1{font-size:26pt;margin:0 0 4mm} h2{font-size:18pt;margin:0 0 8mm;font-weight:600}
        img{width:120mm;height:120mm} p{font-size:13pt;margin:6mm 0 0;color:#34507f}
        .mode{display:inline-block;margin-top:4mm;padding:2mm 6mm;border:1px solid #13295a;border-radius:20mm;font-size:13pt}
      </style></head><body><div class="sheet">
      <h1>${escapeHtml(point.name)}</h1><h2>${escapeHtml(office.name)}</h2>
      <img src="${image}" alt="QR-код">
      <div class="mode">${escapeHtml(MODE_TITLE[point.direction_mode] ?? point.direction_mode)}</div>
      <p>Отсканируйте камерой телефона или в боте HUMOTECH кнопкой «📷 Отметиться».<br>Отметка работает только рядом с офисом.</p>
      </div><script>window.onload=function(){window.focus();window.print();}</script></body></html>`);
    sheet.document.close();
  }

  return (
    <div className="ofs-card ofs-issued" role="region" aria-label="Выпущенный QR-код">
      <div className="ofs-issued__qr">
        {link && image ? (
          <img src={image} alt={`QR-код точки «${point.name}»`} />
        ) : (
          <span className="ofs-issued__none">{link ? 'Готовим код…' : 'Нет ссылки'}</span>
        )}
      </div>
      <div className="ofs-issued__text">
        <h3 className="ofs-card__title">
          QR-код готов: {point.name}
          <span className="ofs-chip">{MODE_TITLE[point.direction_mode] ?? point.direction_mode}</span>
        </h3>
        {link ? (
          <p className="ofs-warn">
            Сохраните или распечатайте код сейчас. Повторно он не показывается —
            если потеряете, перевыпустите: прежний перестанет действовать.
          </p>
        ) : (
          <p className="ofs-alert">
            Точка создана, но ссылку для кода собрать не удалось: на сервере не задано
            имя бота Telegram. Сообщите администратору и перевыпустите код после настройки.
          </p>
        )}
        <div className="ofs-actions ofs-actions--wrap">
          <a className={image ? 'ofs-btn ofs-btn--blue' : 'ofs-btn ofs-btn--blue ofs-btn--off'}
             href={image ?? undefined} download={fileName} aria-disabled={image ? undefined : true}>
            <AppIcon name="download" size={16} />
            Скачать PNG
          </a>
          <button type="button" className="ofs-btn" disabled={!image} onClick={print}>
            <AppIcon name="doc" size={16} />
            Распечатать
          </button>
          <button type="button" className="ofs-btn" disabled={!link} onClick={() => void copy()}>
            <AppIcon name="list" size={16} />
            {copied ? 'Ссылка скопирована' : 'Скопировать ссылку'}
          </button>
          <button type="button" className="ofs-btn ofs-btn--ghost" onClick={onClose}>Готово</button>
        </div>
      </div>
    </div>
  );
}

// --- карточка точки ------------------------------------------------------------

function PointCard({ point, canManage, onChanged, onIssued }: {
  point: api.QrPoint;
  canManage: boolean;
  onChanged: () => void;
  onIssued: (issued: api.IssuedQrPoint) => void;
}) {
  const [busy, setBusy] = useState<'reissue' | 'toggle' | null>(null);
  const [confirm, setConfirm] = useState<'reissue' | 'off' | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const printable = point.qr_mode === 'STATIC';

  async function reissue() {
    setBusy('reissue');
    setFailed(null);
    try {
      onIssued(await api.reissueQrPoint(point.id));
      setConfirm(null);
    } catch (error) {
      setFailed(messageFor(error));
    } finally {
      setBusy(null);
    }
  }

  async function toggle(active: boolean) {
    setBusy('toggle');
    setFailed(null);
    try {
      await api.setQrPointActive(point.id, active);
      setConfirm(null);
      onChanged();
    } catch (error) {
      setFailed(messageFor(error));
    } finally {
      setBusy(null);
    }
  }

  return (
    <li className={point.is_active ? 'ofs-point' : 'ofs-point ofs-point--off'}>
      <div className="ofs-point__main">
        <div className="ofs-point__title">
          <b title={point.name}>{point.name}</b>
          <span className="ofs-chip">{MODE_TITLE[point.direction_mode] ?? point.direction_mode}</span>
          <span className={point.is_active ? 'ofs-state ofs-state--ok' : 'ofs-state ofs-state--off'}>
            <i aria-hidden="true" />
            {point.is_active ? 'Активна' : 'Выключена'}
          </span>
        </div>
        {point.description && <p className="ofs-point__desc" title={point.description}>{point.description}</p>}
        <p className="ofs-point__meta">
          {point.created_at && <span>Создана {shortDate(point.created_at)}</span>}
          {point.created_by_name && <span>{point.created_by_name}</span>}
          <span>Сканирований сегодня: <b>{point.scans_today ?? '—'}</b></span>
          {point.rotated_at && <span>Код перевыпущен {shortDate(point.rotated_at)}</span>}
          {!printable && <span>Код на экране меняется сам</span>}
        </p>
        {failed && <p className="ofs-alert" role="alert">{failed}</p>}
      </div>

      {canManage && (
        <div className="ofs-point__actions">
          {confirm === 'reissue' ? (
            <div className="ofs-confirm" role="alertdialog" aria-label="Перевыпустить QR">
              <span>Старый код сразу перестанет работать. Перевыпустить?</span>
              <button type="button" className="ofs-btn ofs-btn--blue" disabled={busy !== null} onClick={() => void reissue()}>
                {busy === 'reissue' ? 'Выпускаем…' : 'Перевыпустить'}
              </button>
              <button type="button" className="ofs-btn" onClick={() => setConfirm(null)}>Отмена</button>
            </div>
          ) : confirm === 'off' ? (
            <div className="ofs-confirm" role="alertdialog" aria-label="Отключить точку">
              <span>По этому коду перестанут отмечаться. Отключить?</span>
              <button type="button" className="ofs-btn ofs-btn--danger" disabled={busy !== null} onClick={() => void toggle(false)}>
                {busy === 'toggle' ? 'Отключаем…' : 'Отключить'}
              </button>
              <button type="button" className="ofs-btn" onClick={() => setConfirm(null)}>Отмена</button>
            </div>
          ) : (
            <>
              {printable && (
                <button type="button" className="ofs-btn" disabled={busy !== null} onClick={() => setConfirm('reissue')}>
                  <AppIcon name="refresh" size={16} />
                  Перевыпустить QR
                </button>
              )}
              {point.is_active ? (
                <button type="button" className="ofs-btn" disabled={busy !== null} onClick={() => setConfirm('off')}>
                  Отключить
                </button>
              ) : (
                <button type="button" className="ofs-btn" disabled={busy !== null} onClick={() => void toggle(true)}>
                  {busy === 'toggle' ? 'Включаем…' : 'Включить'}
                </button>
              )}
            </>
          )}
        </div>
      )}
    </li>
  );
}

function shortDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(0, 10);
  return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function slug(value: string): string {
  const cleaned = value.toLowerCase().replace(/[^a-z0-9а-яё]+/gi, '-').replace(/^-+|-+$/g, '');
  return cleaned || 'office';
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
