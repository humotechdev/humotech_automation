/**
 * Завести офис: регион и название.
 *
 * Больше здесь ничего не спрашивают. Адрес, точка на карте, радиус
 * геозоны и QR-точки — это настройка, и она открывается сразу после
 * создания. Требовать их в момент заведения значит не дать завести офис
 * тому, кто ещё не знает координат.
 *
 * Название необязательно: пока офис один на регион, «Ташкентская
 * область» — достаточное имя, и сервер подставит его сам.
 *
 * Регион выбирают из готового списка. Часового пояса и кода нет вовсе:
 * пояс в стране один, код придумывает сервер.
 *
 * Окно закрывается только щелчком по самому затемнению. Проверять
 * «щелчок вне рамки» нельзя: список регионов рисуется поверх окна
 * отдельным слоем, и выбор области считался бы щелчком снаружи — окно
 * закрывалось бы ровно в тот момент, когда человек выбрал регион.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import * as api from '../api/crm';
import { AppIcon } from './AppIcon';
import { AppSelectField } from './AppSelect';
import { ApiFailure, messageFor } from '../api/errors';

export function NewOfficeDialog({ regions, onClose, onCreated }: {
  regions: api.RegionFull[];
  onClose: () => void;
  /** Список на странице обновляется, даже если переход не случился. */
  onCreated: () => void;
}) {
  const navigate = useNavigate();
  const [regionId, setRegionId] = useState('');
  const [name, setName] = useState('');
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const problem = useMemo(
    () => (regionId ? null : 'Выберите регион'),
    [regionId],
  );

  const chosen = regions.find((one) => one.id === regionId);

  const submit = async () => {
    setTouched(true);
    if (problem || busy) return;
    setBusy(true);
    setRefusal(null);
    try {
      const made = await api.createOffice({
        region_id: regionId,
        ...(name.trim() ? { name: name.trim() } : {}),
      });
      onCreated();
      // Сразу к настройке: офис без геозоны и QR-точек ещё не работает,
      // и возвращать человека в список значит просить его найти только
      // что созданное и открыть заново.
      navigate(`/offices/${made.id}/setup`);
    } catch (error) {
      setRefusal(
        error instanceof ApiFailure
          ? error.detail ?? messageFor(error)
          : messageFor(error),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="of-ask" role="dialog" aria-modal="true" aria-label="Новый офис"
         onMouseDown={(event) => {
           if (event.target === event.currentTarget) onClose();
         }}>
      <div className="of-ask__box" ref={box}>
        <header className="of-ask__head">
          <h2 className="of-ask__title">Новый офис</h2>
          <button type="button" className="of-btn--icon" aria-label="Закрыть"
                  onClick={onClose}>
            <AppIcon name="close" size={18} />
          </button>
        </header>

        <form className="of-form"
              onSubmit={(event) => { event.preventDefault(); void submit(); }}>
          <label className="of-field">
            <span className="of-field__label">Регион</span>
            <AppSelectField label="Регион" value={regionId} searchable
                            onChange={(value) => { setRegionId(value); setTouched(false); }}>
              <option value="">Выберите регион</option>
              {regions.map((one) => (
                <option key={one.id} value={one.id}>{one.name}</option>
              ))}
            </AppSelectField>
            {touched && problem && (
              <span className="of-field__error" role="alert">{problem}</span>
            )}
          </label>

          <label className="of-field">
            <span className="of-field__label">Название офиса</span>
            <input className="input" value={name} maxLength={255}
                   placeholder={chosen ? chosen.name : 'Необязательно'}
                   onChange={(event) => setName(event.target.value)} />
            <span className="of-field__hint">
              {chosen
                ? `Можно не заполнять — офис будет называться «${chosen.name}»`
                : 'Можно не заполнять — офис назовётся по региону'}
            </span>
          </label>

          <p className="of-note">
            После создания откроется настройка офиса: адрес, точка на карте,
            радиус геозоны и QR-точки. Пока они не заданы, отметки в этом
            офисе приниматься не будут.
          </p>

          {refusal && (
            <p className="of-refusal" role="alert">
              <AppIcon name="alert" size={16} /> {refusal}
            </p>
          )}

          <div className="of-ask__tools">
            <button type="button" className="of-btn of-btn--light" onClick={onClose}
                    disabled={busy}>
              Отмена
            </button>
            <button type="submit" className="of-btn of-btn--blue" disabled={busy}>
              {busy ? 'Создаём…' : 'Создать офис'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
