/**
 * Отрисовка QR на canvas.
 *
 * Библиотека здесь оправдана: кодирование QR — это Рид-Соломон, маски
 * и таблицы версий. Писать его самому ради экономии сорока килобайт
 * значит завести собственный источник ошибок в том месте, где ошибка
 * выглядит как «сканер не читает».
 *
 * Уровень коррекции ошибок средний: код висит на экране, а не печатается
 * на мятой наклейке, — грязь и заломы ему не грозят. Высокий уровень
 * только уплотнил бы модули и ухудшил чтение с расстояния.
 */

import { useEffect, useRef } from 'react';
import QRCode from 'qrcode';

export function QrCanvas({ value }: { value: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!canvas.current) return;
    void QRCode.toCanvas(canvas.current, value, {
      errorCorrectionLevel: 'M',
      margin: 2,
      width: 720,
      color: { dark: '#0f172a', light: '#ffffff' },
    });
  }, [value]);

  return <canvas ref={canvas} className="qr" aria-label="QR-код для отметки" />;
}
