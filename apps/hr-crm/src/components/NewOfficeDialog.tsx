/**
 * Новый офис, шаг первый: регион и название.
 *
 * Офис здесь ещё не создаётся. Без местоположения офис не заводится
 * вовсе — на нём не работала бы ни одна проверка присутствия, — поэтому
 * следующий шаг — карта (`/offices/new`), и уже там офис создаётся одним
 * запросом вместе с точкой и радиусом или контуром.
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

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import * as api from '../api/crm';
import { AppIcon } from './AppIcon';
import { AppSelectField } from './AppSelect';

export function NewOfficeDialog({ regions, region, onClose }: {
  regions: api.RegionFull[];
  /**
   * Регион, выбранный заранее. Приходит с карты: человек ткнул в
   * область и нажал «добавить офис» — спрашивать его о том же ещё раз
   * значит не поверить тому, что он уже сказал.
   */
  region?: string | undefined;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [regionId, setRegionId] = useState(region ?? '');
  const [name, setName] = useState('');
  const [touched, setTouched] = useState(false);

  const box = useRef<HTMLDivElement | null>(null);
  const [leaving, setLeaving] = useState(false);
  const left = useRef(false);
  // Кнопка, открывшая окно: фокус вернётся к ней.
  const [opener] = useState(() => document.activeElement as HTMLElement | null);

  /** Закрытие с коротким обратным движением; повторное — без эффекта. */
  const close = useCallback(() => {
    if (left.current) return;
    left.current = true;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      onClose();
      return;
    }
    setLeaving(true);
    window.setTimeout(onClose, 160);
  }, [onClose]);

  useEffect(() => {
    box.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // Открытый список региона закрывается своим Escape — окно остаётся.
      if (document.querySelector('.app-select__menu')) return;
      close();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [close]);
  useEffect(() => () => {
    if (opener && opener.isConnected) opener.focus();
  }, [opener]);

  const problem = useMemo(
    () => (regionId ? null : 'Выберите регион'),
    [regionId],
  );

  const chosen = regions.find((one) => one.id === regionId);

  const next = () => {
    setTouched(true);
    if (problem) return;
    const query = new URLSearchParams({ region: regionId });
    if (name.trim()) query.set('name', name.trim());
    navigate(`/offices/new?${query.toString()}`);
  };

  return (
    <div className={leaving ? 'of-ask of-ask--leaving' : 'of-ask'} role="dialog" aria-modal="true" aria-label="Новый офис"
         onMouseDown={(event) => {
           if (event.target === event.currentTarget) close();
         }}>
      <div className="of-ask__box" ref={box} tabIndex={-1}>
        <header className="of-ask__head">
          <h2 className="of-ask__title">Новый офис</h2>
          <button type="button" className="of-btn--icon" aria-label="Закрыть"
                  onClick={close}>
            <AppIcon name="close" size={18} />
          </button>
        </header>

        <form className="of-form"
              onSubmit={(event) => { event.preventDefault(); next(); }}>
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
            Дальше — место на карте: точка входа с радиусом или контур
            территории. Без места офис не создаётся.
          </p>

          <div className="of-ask__tools">
            <button type="button" className="of-btn of-btn--light" onClick={close}>
              Отмена
            </button>
            <button type="submit" className="of-btn of-btn--blue">
              Далее: место на карте
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
