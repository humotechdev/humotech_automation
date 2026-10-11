/**
 * Раскрывающаяся часть строки.
 *
 * Высота идёт к настоящей высоте содержимого через
 * `grid-template-rows: 0fr → 1fr`: ни замеров в пикселях, ни
 * `max-height: 9999px`, при котором закрытие начинается с паузы.
 * Строки ниже сдвигаются вместе с ней, потому что это обычный поток
 * документа, а не наложение.
 *
 * Содержимое появляется в разметке только на время показа: закрытая
 * строка не держит в документе скрытые поля и тексты, которые находил
 * бы поиск по странице и средства чтения. При закрытии оно остаётся до
 * конца сворачивания — иначе пропадало бы раньше, чем сжалась высота.
 *
 * Без движения (просьба системы, тестовая среда) открытие и закрытие
 * мгновенные.
 */

import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';

import { ROW_MS, motionAllowed } from '../../features/admin/motion';

export function AnimatedCollapse({ open, id, className, children }: {
  open: boolean;
  id: string;
  className?: string;
  children: ReactNode;
}) {
  const [mounted, setMounted] = useState(open);
  const [expanded, setExpanded] = useState(open);
  const box = useRef<HTMLDivElement>(null);

  if (open && !mounted) setMounted(true);

  useLayoutEffect(() => {
    if (open) {
      if (!expanded) {
        // Стиль закрытого состояния должен быть посчитан до смены
        // класса, иначе браузер сразу нарисует открытое — без перехода.
        if (motionAllowed()) void box.current?.offsetHeight;
        setExpanded(true);
      }
      return;
    }
    if (!mounted) return;
    setExpanded(false);
    if (!motionAllowed()) {
      setMounted(false);
      return;
    }
    const timer = window.setTimeout(() => setMounted(false), ROW_MS + 40);
    return () => window.clearTimeout(timer);
    // Решение принимается только при смене `open`: промежуточные
    // состояния выставляет сам эффект.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const classes = ['adp-collapse'];
  if (expanded) classes.push('adp-collapse--open');
  if (className) classes.push(className);

  return (
    <div ref={box} id={id} className={classes.join(' ')}>
      <div className="adp-collapse__inner">
        {mounted && <div className="adp-collapse__content">{children}</div>}
      </div>
    </div>
  );
}
