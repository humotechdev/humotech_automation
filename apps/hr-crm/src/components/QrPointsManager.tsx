/**
 * QR-точки офиса: список, добавление, печать кода и его замена.
 *
 * Точка — место отметки: главный вход, служебный выход. Её код печатают
 * и вешают у двери; сотрудник сканирует его телефоном.
 *
 * Код точки хранится на сервере и открывается по требованию: «Посмотреть
 * QR», «Печать QR» и «Скачать QR в PNG» работают в любой день, а не
 * только в минуту выпуска. Раньше секрет показывался один раз, и
 * потерянная картинка означала замену кода и поход переклеивать
 * наклейки. Точки, выпущенные до этого, кода не хранят — их заменяют.
 *
 * Картинка QR собирается в браузере из ссылки: на сервер она второй раз
 * не уходит, а в журнал — вовсе.
 *
 * «Печать QR» отдаёт файл, а не открывает окно печати. Окно печати жило
 * во всплывающей вкладке, а её браузер закрывал: код приходит с сервера,
 * и к моменту `window.open` жест мыши уже «остыл». Нажатие не делало
 * ничего. Скачанный PNG печатается из любой программы просмотра.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
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

export function QrPointsManager({ office, canManage, located, onChanged, onGeo }: {
  office: api.OfficeFull;
  canManage: boolean;
  /** Есть ли у офиса точка на карте и радиус: без них печатный код не работает. */
  located: boolean;
  /** Точки изменились — сводке слева пора пересчитать готовность. */
  onChanged?: () => void;
  /** Уйти к геолокации: без неё коды не принимают отметки. */
  onGeo?: () => void;
}) {
  const [attempt, setAttempt] = useState(0);
  const [list] = useBlock(
    (signal) => api.qrPoints({ office_id: office.id }, signal),
    `office-qr|${office.id}|${attempt}`,
  );
  const [adding, setAdding] = useState(false);
  const [shown, setShown] = useState<api.QrPoint | null>(null);
  // Ссылки, уже полученные в этой вкладке: второй раз за ними не ходим.
  const [links, setLinks] = useState<Record<string, string | null>>({});

  const reload = () => {
    setAttempt((n) => n + 1);
    onChanged?.();
  };
  const points = list.state === 'ready' ? list.data.items : [];
  const active = points.filter((point) => point.is_active);
  const entry = active.some((point) => point.direction_mode !== 'EXIT');
  const leave = active.some((point) => point.direction_mode !== 'ENTRY');

  /** Ссылка наклейки: из памяти или с сервера. */
  async function linkOf(point: api.QrPoint): Promise<string | null> {
    if (point.id in links) return links[point.id] ?? null;
    const answer = await api.qrPointSticker(point.id);
    setLinks((was) => ({ ...was, [point.id]: answer.sticker_link }));
    return answer.sticker_link;
  }

  // Пока точек нет, кнопка стоит в самом пустом состоянии — рядом с
  // объяснением, зачем она. Вторая такая же в шапке была бы тем же
  // действием, повторённым в двух местах одного экрана.
  const blank = list.state === 'ready' && points.length === 0 && !adding;

  return (
    <div className="ofs-qr">
      <div className="ofs-qr__main">
        <div className="ofs-qr__head">
          <h2 className="ofs-title">
            QR-точки <span className="ofs-qr__count">· {points.length}</span>
          </h2>
          {canManage && !adding && !blank && (
            <button type="button" className="ofs-btn ofs-btn--blue"
                    onClick={() => setAdding(true)}>
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
              setLinks((was) => ({ ...was, [result.point.id]: result.sticker_link }));
              setShown(result.point);
              reload();
            }}
          />
        )}

        {list.state === 'loading' && <p className="ofs-empty">Загружаем QR-точки…</p>}
        {list.state === 'denied' && <p className="ofs-empty">Нет доступа к QR-точкам.</p>}
        {list.state === 'error' && <p className="ofs-empty ofs-empty--bad">Не удалось загрузить QR-точки.</p>}
        {blank && (
          <div className="ofs-blank">
            <AppIcon name="grid" size={20} />
            <p className="ofs-blank__title">QR-точек пока нет</p>
            <p className="ofs-blank__text">
              {canManage
                ? 'Точка — это место, где сотрудник отмечается: дверь, проходная, вход на склад. Заведите первую, распечатайте код и повесьте его на стену.'
                : 'Точки заводит администратор офиса. Пока их нет, отметиться по QR в этом офисе нельзя.'}
            </p>
            {canManage && (
              <>
                <button type="button" className="ofs-btn ofs-btn--blue"
                        onClick={() => setAdding(true)}>
                  <AppIcon name="plus" size={20} />
                  Добавить QR-точку
                </button>
                <p className="ofs-blank__hint">
                  Например, «Главный вход»
                </p>
              </>
            )}
          </div>
        )}

        {points.length > 0 && (
          <div className="ofs-table-wrap">
            <table className="ofs-table">
              <thead>
                <tr>
                  <th>Точка</th>
                  <th>Назначение</th>
                  <th>Состояние</th>
                  <th>Последнее сканирование</th>
                  <th className="ofs-table__actions">Действия</th>
                </tr>
              </thead>
              <tbody>
                {points.map((point) => (
                  <PointRow
                    key={point.id}
                    office={office}
                    point={point}
                    canManage={canManage}
                    onChanged={reload}
                    onShow={() => setShown(point)}
                    linkOf={linkOf}
                    onIssued={(result) => {
                      setLinks((was) => ({ ...was, [result.point.id]: result.sticker_link }));
                      setShown(result.point);
                      reload();
                    }}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {points.length > 0 && (
          <p className="ofs-qr__note">
            Замените QR, если распечатанный код потерян или повреждён.
          </p>
        )}
      </div>

      {/* Готовность: три условия, при которых отметка у двери вообще
          состоится. Без любого из них код на стене бесполезен. */}
      <aside className="ofs-ready" aria-label="Готовность офиса">
        <h3 className="ofs-ready__title">Готовность</h3>
        <ul className="ofs-ready__list">
          <ReadyItem done={located} title="Геолокация настроена" />
          <ReadyItem done={entry} title="Точка для входа" />
          <ReadyItem done={leave} title="Точка для выхода" />
        </ul>
        {onGeo && (
          <button type="button" className="ofs-ready__link" onClick={onGeo}>
            Открыть геолокацию
            <AppIcon name="next" size={16} />
          </button>
        )}
      </aside>

      {shown && (
        <QrDialog
          office={office}
          point={shown}
          linkOf={linkOf}
          canManage={canManage}
          onClose={() => setShown(null)}
          onIssued={(result) => {
            setLinks((was) => ({ ...was, [result.point.id]: result.sticker_link }));
            setShown(result.point);
            reload();
          }}
        />
      )}
    </div>
  );
}

function ReadyItem({ done, title }: { done: boolean; title: string }) {
  return (
    <li className={done ? 'ofs-ready__item ofs-ready__item--on' : 'ofs-ready__item'}>
      <AppIcon name={done ? 'check' : 'alert'} size={20} />
      {title}
    </li>
  );
}

// --- строка точки --------------------------------------------------------------

function PointRow({ office, point, canManage, onChanged, onShow, onIssued, linkOf }: {
  office: api.OfficeFull;
  point: api.QrPoint;
  canManage: boolean;
  onChanged: () => void;
  onShow: () => void;
  onIssued: (issued: api.IssuedQrPoint) => void;
  linkOf: (point: api.QrPoint) => Promise<string | null>;
}) {
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<'reissue' | 'off' | 'delete' | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  /** Код в руки: и «печать», и «скачать» идут одной дорогой. */
  async function withImage(use: (image: string, link: string) => void) {
    setBusy(true);
    setFailed(null);
    try {
      const link = await linkOf(point);
      if (!link) {
        setFailed('Ссылку кода собрать не удалось: на сервере не задано имя бота Telegram.');
        return;
      }
      use(await QRCode.toDataURL(link, { width: 720, margin: 2, errorCorrectionLevel: 'M' }), link);
    } catch (error) {
      setFailed(messageFor(error));
    } finally {
      setBusy(false);
    }
  }

  async function act(run: () => Promise<unknown>) {
    setBusy(true);
    setFailed(null);
    try {
      await run();
      setConfirm(null);
      onChanged();
    } catch (error) {
      setFailed(messageFor(error));
      setConfirm(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <tr className={point.is_active ? undefined : 'ofs-row--off'}>
        <td>
          {renaming === null ? (
            <b className="ofs-row__name" title={point.name}>{point.name}</b>
          ) : (
            <span className="ofs-row__rename">
              <input className="ofs-field__input" value={renaming} autoFocus maxLength={255}
                     aria-label="Название точки"
                     onChange={(event) => setRenaming(event.target.value)} />
              <button type="button" className="ofs-btn ofs-btn--blue"
                      disabled={busy || !renaming.trim()}
                      onClick={() => void act(async () => {
                        await api.updateQrPoint(point.id, { name: renaming.trim() });
                        setRenaming(null);
                      })}>
                Сохранить
              </button>
              <button type="button" className="ofs-btn" onClick={() => setRenaming(null)}>Отмена</button>
            </span>
          )}
          {point.description && <i className="ofs-row__desc">{point.description}</i>}
        </td>
        <td>{MODE_TITLE[point.direction_mode] ?? point.direction_mode}</td>
        <td>
          <span className={point.is_active ? 'ofs-live ofs-live--ok' : 'ofs-live ofs-live--off'}>
            <b aria-hidden="true" />
            {point.is_active ? 'Работает' : 'Выключена'}
          </span>
        </td>
        <td>{whenScanned(point.last_scan_at ?? null)}</td>
        <td className="ofs-table__actions">
          {canManage && (
            <div className="ofs-row__tools">
              <button type="button" className="ofs-btn" disabled={busy}
                      onClick={() => void withImage((image) => download(office, point, image))}>
                <AppIcon name="doc" size={16} />
                Печать QR
              </button>
              <button type="button" className="ofs-btn" disabled={busy}
                      onClick={() => setConfirm('reissue')}>
                <AppIcon name="refresh" size={16} />
                Заменить
              </button>
              <RowMenu disabled={busy}>
                {(close) => (
                  <>
                    <button type="button" role="menuitem"
                            onClick={() => { close(); onShow(); }}>
                      Посмотреть QR
                    </button>
                    <button type="button" role="menuitem"
                            onClick={() => { close(); setRenaming(point.name); }}>
                      Переименовать точку
                    </button>
                    <button type="button" role="menuitem"
                            onClick={() => { close(); void withImage((image) => download(office, point, image)); }}>
                      Скачать QR в PNG
                    </button>
                    {point.is_active ? (
                      <button type="button" role="menuitem"
                              onClick={() => { close(); setConfirm('off'); }}>
                        Отключить точку
                      </button>
                    ) : (
                      <button type="button" role="menuitem"
                              onClick={() => { close(); void act(() => api.setQrPointActive(point.id, true)); }}>
                        Включить точку
                      </button>
                    )}
                    <button type="button" role="menuitem" className="ofs-more__danger"
                            onClick={() => { close(); setConfirm('delete'); }}>
                      Удалить точку
                    </button>
                  </>
                )}
              </RowMenu>
            </div>
          )}
        </td>
      </tr>

      {(confirm || failed) && (
        <tr className="ofs-row__aside">
          <td colSpan={5}>
            {confirm === 'reissue' && (
              <div className="ofs-confirm" role="alertdialog" aria-label="Заменить QR">
                <span>Прежний код перестанет работать сразу — наклейку у двери придётся заменить. Продолжить?</span>
                <button type="button" className="ofs-btn ofs-btn--blue" disabled={busy}
                        onClick={() => void act(async () => onIssued(await api.reissueQrPoint(point.id)))}>
                  Заменить QR
                </button>
                <button type="button" className="ofs-btn" onClick={() => setConfirm(null)}>Отмена</button>
              </div>
            )}
            {confirm === 'off' && (
              <div className="ofs-confirm" role="alertdialog" aria-label="Отключить точку">
                <span>По этому коду перестанут отмечаться. Отключить?</span>
                <button type="button" className="ofs-btn ofs-btn--danger" disabled={busy}
                        onClick={() => void act(() => api.setQrPointActive(point.id, false))}>
                  Отключить
                </button>
                <button type="button" className="ofs-btn" onClick={() => setConfirm(null)}>Отмена</button>
              </div>
            )}
            {confirm === 'delete' && (
              <div className="ofs-confirm" role="alertdialog" aria-label="Удалить точку">
                <span>Точка исчезнет совсем. Удалить?</span>
                <button type="button" className="ofs-btn ofs-btn--danger" disabled={busy}
                        onClick={() => void act(() => api.deleteQrPoint(point.id))}>
                  Удалить
                </button>
                <button type="button" className="ofs-btn" onClick={() => setConfirm(null)}>Отмена</button>
              </div>
            )}
            {failed && <p className="ofs-alert" role="alert">{failed}</p>}
          </td>
        </tr>
      )}
    </>
  );
}

// --- меню «•••» -----------------------------------------------------------------

/**
 * Меню строки таблицы.
 *
 * Список лежит в `<body>`, а не рядом с кнопкой. У обёртки таблицы
 * включена прокрутка по горизонтали, а прокрутка обрезает всё, что
 * вылезает за край: меню было видно наполовину, и нижние пункты
 * доставались только скроллом таблицы. Из `<body>` его не обрежет никто.
 *
 * Место считается по кнопке при каждом открытии и пересчитывается,
 * пока страница едет под пальцем, — иначе меню отстаёт от своей строки.
 * Если снизу не хватает высоты, список раскрывается вверх.
 */
function RowMenu({ disabled, children }: {
  disabled: boolean;
  children: (close: () => void) => ReactNode;
}) {
  const anchor = useRef<HTMLButtonElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);

  const place = useCallback(() => {
    const button = anchor.current;
    if (!button) return;
    const rect = button.getBoundingClientRect();
    const height = box.current?.offsetHeight ?? 0;
    const below = rect.bottom + 6;
    // Вверх — только когда снизу действительно некуда: раскрытие вниз
    // привычнее, и дёргать список без нужды не стоит.
    const up = height > 0 && below + height > window.innerHeight - 8;
    setAt({ top: up ? Math.max(8, rect.top - 6 - height) : below, left: rect.right });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    place();
    // Второй заход — уже по настоящей высоте списка: до первой отрисовки
    // её неоткуда взять.
    const again = requestAnimationFrame(place);
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    return () => {
      cancelAnimationFrame(again);
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
    };
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  const close = () => setOpen(false);

  return (
    <>
      <button type="button" className="ofs-btn ofs-btn--dots" disabled={disabled} ref={anchor}
              aria-label="Ещё действия" aria-expanded={open} aria-haspopup="menu"
              onClick={() => setOpen((was) => !was)}>
        •••
      </button>
      {open && at && createPortal(
        <>
          <button type="button" className="ofs-more__shade" aria-label="Закрыть меню" onClick={close} />
          <div className="ofs-more__menu" role="menu" ref={box}
               style={{ top: `${at.top}px`, left: `${at.left}px` }}>
            {children(close)}
          </div>
        </>,
        document.body,
      )}
    </>
  );
}

// --- окно с кодом ---------------------------------------------------------------

/**
 * Окно с QR. Картинка собирается здесь же, из ссылки.
 *
 * Если у точки нет сохранённого кода — она выпущена до того, как коды
 * стали храниться, — окно объясняет это и предлагает замену: вернуть
 * прежний код нельзя, и молчать об этом хуже, чем сказать.
 */
function QrDialog({ office, point, canManage, linkOf, onClose, onIssued }: {
  office: api.OfficeFull;
  point: api.QrPoint;
  canManage: boolean;
  linkOf: (point: api.QrPoint) => Promise<string | null>;
  onClose: () => void;
  onIssued: (issued: api.IssuedQrPoint) => void;
}) {
  const [image, setImage] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    setImage(null);
    setFailed(null);
    linkOf(point)
      .then(async (link) => {
        if (!alive) return;
        if (!link) {
          setFailed('Ссылку кода собрать не удалось: на сервере не задано имя бота Telegram.');
          return;
        }
        const url = await QRCode.toDataURL(link, { width: 720, margin: 2, errorCorrectionLevel: 'M' });
        if (alive) setImage(url);
      })
      .catch((error) => { if (alive) setFailed(messageFor(error)); });
    return () => { alive = false; };
  }, [point, linkOf]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  async function replace() {
    setBusy(true);
    setFailed(null);
    try {
      onIssued(await api.reissueQrPoint(point.id));
    } catch (error) {
      setFailed(messageFor(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ofs-modal" role="dialog" aria-modal="true" aria-label={`QR-код: ${point.name}`}
         onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="ofs-modal__box">
        <header className="ofs-modal__head">
          <div>
            <h2 className="ofs-modal__title">{point.name}</h2>
            <p className="ofs-modal__sub">
              {office.name} · {MODE_TITLE[point.direction_mode] ?? point.direction_mode}
            </p>
          </div>
          <button type="button" className="of-btn--icon" aria-label="Закрыть" onClick={onClose}>
            <AppIcon name="close" size={20} />
          </button>
        </header>

        <div className="ofs-modal__qr">
          {image ? (
            <img src={image} alt={`QR-код точки «${point.name}»`} />
          ) : failed ? (
            <p className="ofs-modal__none">{failed}</p>
          ) : (
            <p className="ofs-modal__none">Готовим код…</p>
          )}
        </div>

        <div className="ofs-modal__tools">
          {image ? (
            <a className="ofs-btn ofs-btn--blue" href={image} download={fileName(office, point)}>
              <AppIcon name="download" size={16} />
              Скачать QR в PNG
            </a>
          ) : failed && canManage ? (
            <button type="button" className="ofs-btn ofs-btn--blue" disabled={busy}
                    onClick={() => void replace()}>
              {busy ? 'Заменяем…' : 'Заменить QR'}
            </button>
          ) : null}
        </div>
      </div>
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
    setSending(true);
    setFailed(null);
    try {
      const result = await api.createQrPoint({
        office_id: officeId,
        // Пустое имя не отправляем: сервер назовёт точку по её типу.
        ...(name.trim() ? { name: name.trim() } : {}),
        direction_mode: mode,
        // Печатная точка: лист с кодом висит на стене.
        qr_mode: 'STATIC',
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
    <div className="ofs-new" aria-label="Новая QR-точка">
      <h3 className="ofs-card__title">Новая QR-точка</h3>
      <div className="ofs-new__grid">
        <label className="ofs-field">
          <span className="ofs-field__label">Название — необязательно</span>
          <input className="ofs-field__input" value={name} maxLength={255}
                 placeholder={DEFAULT_NAME[mode]} aria-label="Название точки"
                 onChange={(event) => setName(event.target.value)} />
        </label>
        <div className="ofs-field">
          <span className="ofs-field__label">Назначение</span>
          <div className="ofs-modes" role="radiogroup" aria-label="Назначение точки">
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
          {sending ? 'Создаём…' : 'Создать и показать QR'}
        </button>
        <button type="button" className="ofs-btn" disabled={sending} onClick={onCancel}>Отмена</button>
      </div>
    </div>
  );
}

// --- мелочи ---------------------------------------------------------------------

/** Как назовётся точка, которую не назвали. Тот же выбор на сервере. */
const DEFAULT_NAME: Record<'ENTRY' | 'EXIT' | 'BOTH', string> = {
  ENTRY: 'Вход',
  EXIT: 'Выход',
  BOTH: 'Вход и выход',
};

/** «Сегодня, 09:14», «Вчера, 18:07» или дата. `null` — ни разу. */
function whenScanned(iso: string | null): string {
  if (!iso) return 'Ни разу';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '—';
  const time = at.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  const today = new Date();
  const sameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  if (sameDay(at, today)) return `Сегодня, ${time}`;
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (sameDay(at, yesterday)) return `Вчера, ${time}`;
  return `${at.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' })}, ${time}`;
}

function fileName(office: api.OfficeFull, point: api.QrPoint): string {
  return `qr-${slug(office.name)}-${slug(point.name)}.png`;
}

function download(office: api.OfficeFull, point: api.QrPoint, image: string) {
  const link = document.createElement('a');
  link.href = image;
  link.download = fileName(office, point);
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function slug(value: string): string {
  const cleaned = value.toLowerCase().replace(/[^a-z0-9а-яё]+/gi, '-').replace(/^-+|-+$/g, '');
  return cleaned || 'office';
}
