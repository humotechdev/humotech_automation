/**
 * Съёмка справки прямо в приложении.
 *
 * Зачем не `input[type=file] capture`. На iPhone он открывает камеру и
 * всё работает. На Android Telegram перехватывает системный выбор файла
 * и показывает свой — а `capture` при этом не смотрит вовсе, поэтому
 * вместо камеры открывается галерея. Со стороны страницы на это влияния
 * нет: атрибут выставлен верно, его просто игнорируют.
 *
 * Поэтому камера здесь своя: `getUserMedia`, кадр на `canvas`, готовый
 * файл. Одинаково на обеих платформах, и от чужого выбора не зависит.
 *
 * Если камеры нет или в доступе отказали — не тупик: возвращаем отказ,
 * и поле откатывается на прежний путь через системный выбор файла. Хуже,
 * чем было, не станет ни на одном устройстве.
 *
 * Поток останавливается всегда: и при съёмке, и при закрытии, и при
 * размонтировании. Оставленный включённым поток — это горящий индикатор
 * камеры у человека в кармане.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { CameraIcon, CloseIcon } from './icons';
import { IconButton, SecondaryButton } from './primitives';

type Phase =
  | { kind: 'starting' }
  | { kind: 'live' }
  | { kind: 'denied' }
  | { kind: 'unavailable' };

export function supportsCamera(source: Window = window): boolean {
  return typeof source.navigator?.mediaDevices?.getUserMedia === 'function';
}

export function CameraCapture({
  onShot,
  onCancel,
  onUnavailable,
}: {
  onShot: (file: File) => void;
  onCancel: () => void;
  /** Камеры нет или доступ не дали: поле откатится на выбор файла. */
  onUnavailable: () => void;
}) {
  const video = useRef<HTMLVideoElement | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: 'starting' });
  const [busy, setBusy] = useState(false);

  const stop = useCallback(() => {
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function start() {
      if (!supportsCamera()) {
        setPhase({ kind: 'unavailable' });
        return;
      }
      try {
        // Задняя камера: справку фотографируют, а не себя.
        const media = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' } },
          audio: false,
        });
        if (cancelled) {
          media.getTracks().forEach((track) => track.stop());
          return;
        }
        stream.current = media;
        if (video.current) {
          video.current.srcObject = media;
          // Промис вернут не все: в старых вебвью `play()` отдаёт
          // undefined. И сам отказ здесь не значит «камеры нет» — поток
          // уже получен, разрешение дано, снимок снимется. Автопуск
          // просто не сработал, `autoPlay` в разметке доиграет своё.
          try {
            await Promise.resolve(video.current.play());
          } catch {
            // Ничего: см. выше.
          }
        }
        setPhase({ kind: 'live' });
      } catch (error) {
        if (cancelled) return;
        const name = (error as { name?: string })?.name;
        setPhase(
          name === 'NotAllowedError' || name === 'SecurityError'
            ? { kind: 'denied' }
            : { kind: 'unavailable' },
        );
      }
    }

    void start();
    return () => {
      cancelled = true;
      stop();
    };
  }, [stop]);

  async function shoot() {
    const source = video.current;
    if (!source || busy) return;
    setBusy(true);

    const canvas = document.createElement('canvas');
    canvas.width = source.videoWidth || 1280;
    canvas.height = source.videoHeight || 960;
    const context = canvas.getContext('2d');
    if (!context) {
      setBusy(false);
      onUnavailable();
      return;
    }
    context.drawImage(source, 0, 0, canvas.width, canvas.height);

    const blob = await new Promise<Blob | null>((resolve) => {
      // JPEG, а не PNG: снимок бумажной справки в PNG весит в разы
      // больше при том же виде, а сервер ограничивает размер файла.
      canvas.toBlob((result) => resolve(result), 'image/jpeg', 0.9);
    });

    stop();
    setBusy(false);
    if (!blob) {
      onUnavailable();
      return;
    }
    onShot(
      new File([blob], `справка-${stamp()}.jpg`, { type: 'image/jpeg' }),
    );
  }

  if (phase.kind === 'unavailable' || phase.kind === 'denied') {
    return (
      <div className="overlay">
        <div className="dialog" role="dialog" aria-modal="true" aria-label="Камера">
          <h2>{phase.kind === 'denied' ? 'Камера закрыта' : 'Камеры нет'}</h2>
          <p className="muted">
            {phase.kind === 'denied'
              ? 'Telegram не пустил к камере. Справку можно приложить готовым файлом.'
              : 'На этом устройстве снять справку не получится. Приложите готовый файл.'}
          </p>
          <div className="dialog-actions">
            <SecondaryButton onClick={onCancel} wide>
              Закрыть
            </SecondaryButton>
            <SecondaryButton onClick={onUnavailable} wide>
              Выбрать файл
            </SecondaryButton>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="overlay camera-overlay">
      <div
        className="camera"
        role="dialog"
        aria-modal="true"
        aria-label="Съёмка справки"
      >
        <div className="camera-head">
          <span>Сфотографируйте справку целиком</span>
          <IconButton
            label="Закрыть камеру"
            icon={<CloseIcon size={20} />}
            onClick={() => {
              stop();
              onCancel();
            }}
          />
        </div>

        <div className="camera-view">
          <video ref={video} playsInline muted autoPlay />
          {phase.kind === 'starting' && (
            <p className="camera-hint">Включаем камеру…</p>
          )}
        </div>

        <button
          type="button"
          className="camera-shutter"
          aria-label="Снять"
          disabled={phase.kind !== 'live' || busy}
          onClick={() => void shoot()}
        >
          <CameraIcon size={26} />
        </button>
      </div>
    </div>
  );
}

/** Метка времени в имени файла: `2026-09-04-14-21`. */
function stamp(): string {
  const now = new Date();
  const two = (value: number) => String(value).padStart(2, '0');
  return [
    now.getFullYear(),
    two(now.getMonth() + 1),
    two(now.getDate()),
    two(now.getHours()),
    two(now.getMinutes()),
  ].join('-');
}
