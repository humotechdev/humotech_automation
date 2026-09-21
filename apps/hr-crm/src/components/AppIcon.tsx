/**
 * Единственная точка, через которую в CRM попадают иконки.
 *
 * Рисунки — Iconoir (`iconoir-react`). Своих контуров больше нет: два
 * самодельных набора расходились по стилю (разная толщина, разный отступ
 * от края квадрата), и каждая новая иконка требовала рисовать её руками
 * в тон остальным.
 *
 * Компонент держит три вещи, которые обязаны совпадать у всех иконок
 * сразу: размер, толщину линии и то, что иконка не объявляет себя
 * содержимым для читалки экрана. Меняются они здесь, а не в ста
 * семидесяти местах разметки.
 *
 * Цвет сюда не передаётся. Линии рисуются `currentColor`, то есть берут
 * цвет текста своего места: «зелёная иконка присутствия» — это зелёный
 * `color` у блока присутствия, а не второй список цветов рядом с
 * `crm.css`. Один источник цвета на всю систему остаётся один.
 *
 * Имена — свои, а не имена Iconoir. Разметка говорит, ЧТО значит значок
 * («заявка», «опоздание»), а какой именно рисунок этому соответствует —
 * решается здесь. Смена рисунка тогда стоит одной строки.
 */

import type { ComponentType, SVGProps } from 'react';
import {
  Archive, ArrowDownRight, ArrowLeft, ArrowRight, ArrowUpRight, Bell, Book,
  Building, Calendar, ChatBubble, CheckCircle, CircleSpark, Clock,
  ClockRotateRight, Database, Download, EditPencil, Eye, EyeClosed, Filter, Globe,
  Group, Home, Key, Lock, LogOut, MailOut, MapPin, NavArrowDown, NavArrowRight,
  Page, Plus,
  FilterList, UserPlus,
  Refresh, ReportColumns, Reports, Search, SendDiagonal, Settings, ShieldCheck,
  List, Table, User, ViewGrid, WarningCircle, Xmark, XmarkCircle,
} from 'iconoir-react';

export type AppIconName =
  | 'home' | 'users' | 'clock' | 'doc' | 'pin' | 'chart' | 'chat'
  | 'report' | 'book' | 'bell' | 'admin' | 'settings'
  | 'search' | 'refresh' | 'calendar' | 'arrow' | 'logout' | 'chevron'
  | 'inbox' | 'alert' | 'database' | 'globe' | 'send' | 'building'
  | 'download' | 'check' | 'cross' | 'lock' | 'late' | 'sheet' | 'filter'
  | 'plus' | 'pencil' | 'archive' | 'half' | 'key' | 'list' | 'grid'
  | 'user' | 'user-plus' | 'filter-list' | 'eye' | 'eye-off'
  | 'close' | 'back' | 'next' | 'trend-up' | 'trend-down';

type Glyph = ComponentType<SVGProps<SVGSVGElement>>;

const GLYPHS: Record<AppIconName, Glyph> = {
  home: Home,
  users: Group,
  clock: Clock,
  doc: Page,
  pin: MapPin,
  chart: ReportColumns,
  chat: ChatBubble,
  report: Reports,
  book: Book,
  bell: Bell,
  admin: ShieldCheck,
  settings: Settings,
  search: Search,
  refresh: Refresh,
  calendar: Calendar,
  arrow: ArrowRight,
  logout: LogOut,
  chevron: NavArrowDown,
  // «Журнал отправок»: не входящие, а ушедшие письма.
  inbox: MailOut,
  alert: WarningCircle,
  database: Database,
  globe: Globe,
  send: SendDiagonal,
  building: Building,
  download: Download,
  check: CheckCircle,
  cross: XmarkCircle,
  lock: Lock,
  // Опоздание: часы со стрелкой назад. Отдельного значка для этого в
  // Iconoir нет, а обычные часы не отличили бы «опоздал» от «время».
  late: ClockRotateRight,
  sheet: Table,
  // Отбор: воронка. Нужен там, где отбор ничего не нашёл.
  filter: Filter,
  // Переключатель вида списка: строки против плиток.
  list: List,
  grid: ViewGrid,
  plus: Plus,
  pencil: EditPencil,
  archive: Archive,
  // Метка AI-ответов. Прежний полукруг ничего не означал; искра — общий
  // для отрасли знак «это посчитала машина».
  half: CircleSpark,
  key: Key,
  user: User,
  // «Завести первого сотрудника»: значок называет само действие, а не
  // предмет, которого нет. Пустому списку это подходит больше, чем
  // группа людей, которой там как раз и нет.
  'user-plus': UserPlus,
  // Отбор, который ничего не дал: список с лупой. Одна лупа значила бы
  // «ищите», а искать человек уже пробовал — менять нужно условия.
  'filter-list': FilterList,
  eye: Eye,
  'eye-off': EyeClosed,
  // Закрыть — голый крест. `cross` в круге означает отказ по существу
  // («заявка отклонена»), и путать эти два значка нельзя.
  close: Xmark,
  back: ArrowLeft,
  // Уголок вправо — «здесь можно нажать и перейти». Отличается от
  // `arrow`: та ведёт в раздел, этот открывает конкретную запись.
  next: NavArrowRight,
  'trend-up': ArrowUpRight,
  'trend-down': ArrowDownRight,
};

/**
 * Три размера на всю систему — и тип, который не даёт добавить
 * четвёртый.
 *
 * До этого в разметке стояли 13, 14, 15, 16, 17, 18, 19, 20 и 22, причём
 * часть из них таблица стилей всё равно переопределяла своими. Разница в
 * пиксель на экране не видна, а в коде превращается в вопрос «почему
 * здесь пятнадцать» без ответа. Союз вместо `number` — чтобы ответ не
 * потребовался снова: несовпадающий размер не соберётся.
 */
export type IconSize = 16 | 18 | 20;

export const ICON_SIZE = {
  /** Боковое меню. */
  nav: 20,
  /** Заголовки разделов и панелей. */
  title: 20,
  /** Карточки и строки списков. */
  card: 18,
  /** Мелкие действия: стрелки, крестики, значки внутри кнопок. */
  action: 16,
} as const satisfies Record<string, IconSize>;

type Props = {
  name: AppIconName;
  /** По умолчанию — размер карточки. */
  size?: IconSize;
  className?: string;
};

export function AppIcon({ name, size = ICON_SIZE.card, className }: Props) {
  const Shape = GLYPHS[name];
  return (
    <Shape
      className={className}
      width={size}
      height={size}
      strokeWidth={1.7}
      color="currentColor"
      aria-hidden="true"
      focusable="false"
    />
  );
}
