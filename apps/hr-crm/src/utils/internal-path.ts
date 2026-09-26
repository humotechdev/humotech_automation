/**
 * Адрес возврата из параметра страницы — только внутри CRM.
 *
 * `?back=` приходит из адресной строки, то есть из любой присланной
 * ссылки. `<Link to="https://…">` маршрутизатор отдаёт браузеру как есть,
 * и «Назад» уводил бы кадровика на чужой сайт, похожий на CRM. Поэтому
 * принимается только путь от корня этого же приложения: начинается с
 * одной `/`, без `//` и `/\` (браузер читает оба как «другой сайт»).
 */
export function internalPath(value: string | null | undefined, fallback: string): string {
  if (!value) return fallback;
  if (!value.startsWith('/')) return fallback;
  if (value.startsWith('//') || value.startsWith('/\\')) return fallback;
  // Управляющие символы браузер из адреса вырезает, и `/\t/evil` стал бы `//evil`.
  if (/[\u0000-\u001f\u007f\\]/.test(value)) return fallback;
  return value;
}
