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
/*
 * Второй набор — Lucide, и он здесь ровно для двух крупных значков
 * пустых состояний.
 *
 * Правило «один набор на систему» это не отменяет: у Iconoir рисунок
 * рассчитан на строку интерфейса, и увеличенный до сорока пикселей он
 * выглядит пустым внутри. Lucide рисует плотнее, и крупно читается.
 *
 * Смешивать их в одной строке нельзя — разная посадка в квадрате
 * видна сразу. Поэтому имена ниже отмечены как «крупные»: они стоят
 * по одному на экране, рядом с заголовком, и ни с чем не соседствуют.
 */
import { FilterX, UsersRound } from 'lucide-react';
import {
  Archive, ArrowDownRight, Attachment, Check, DoubleCheck, Emoji, MoreHoriz, Suitcase, ArrowLeft, ArrowRight, ArrowUpRight, Bell, Book,
  Building, Calendar, ChatBubble, CheckCircle, CircleSpark, Clock,
  ClockRotateRight, Database, Download, EditPencil, Eye, EyeClosed, Filter, Globe,
  Group, Home, InfoCircle, Key, Lock, LogOut, MailOut, MapPin, NavArrowDown,
  NavArrowRight,
  Page, Plus,
  FilterList, UserPlus,
  Refresh, ReportColumns, Reports, Search, SendDiagonal, Settings, ShieldCheck,
  List, MoreVert, Table, Trash, User, ViewGrid, WarningCircle, Xmark, XmarkCircle,
  UserXmark, DataTransferBoth, GraduationCap, Megaphone, Hourglass, LightBulb, WarningTriangle, Community, UserBadgeCheck,
} from 'iconoir-react';

export type AppIconName =
  | 'home' | 'users' | 'clock' | 'doc' | 'pin' | 'chart' | 'chat'
  | 'report' | 'book' | 'bell' | 'admin' | 'settings'
  | 'search' | 'refresh' | 'calendar' | 'arrow' | 'logout' | 'chevron'
  | 'inbox' | 'alert' | 'database' | 'globe' | 'send' | 'building'
  | 'download' | 'check' | 'cross' | 'lock' | 'late' | 'sheet' | 'filter'
  | 'plus' | 'pencil' | 'archive' | 'half' | 'key' | 'list' | 'grid'
  | 'user' | 'user-plus' | 'filter-list' | 'eye' | 'eye-off'
  /* Крупные значки пустых состояний — единственные из Lucide. */
  | 'blank-people' | 'blank-search'
  | 'close' | 'back' | 'next' | 'trend-up' | 'trend-down'
  | 'info' | 'trash' | 'more'
  | 'attach' | 'tick' | 'ticks' | 'bag' | 'dots' | 'smile'
  | 'user-x' | 'transfer' | 'grad' | 'megaphone' | 'hourglass' | 'bulb' | 'warning' | 'team' | 'user-ok';

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
  // Подсказка, а не предупреждение: у них разный вес, и значок
  // тревоги там, где просто объясняют порядок, торопит зря.
  info: InfoCircle,
  trash: Trash,
  /* Три точки: второстепенные действия строки под ними. */
  more: MoreVert,
  // Переписка: скрепка у поля ответа, галочки доставки у сообщения.
  attach: Attachment,
  tick: Check,
  ticks: DoubleCheck,
  // Отдел в карточке человека: портфель, как у рабочего места.
  bag: Suitcase,
  // Меню диалога — горизонтальные точки, как у переписки в мессенджерах.
  dots: MoreHoriz,
  smile: Emoji,
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
  'user-plus': UserPlus,
  'filter-list': FilterList,
  // Пустые состояния. Из Lucide — см. пояснение у импорта.
  'blank-people': UsersRound,
  // Воронка с крестиком: пусто не вообще, а под этот отбор. Лупа
  // говорила бы про поиск, а причина — в фильтрах, и они видны выше.
  'blank-search': FilterX,
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
  'user-x': UserXmark,
  transfer: DataTransferBoth,
  grad: GraduationCap,
  megaphone: Megaphone,
  hourglass: Hourglass,
  bulb: LightBulb,
  warning: WarningTriangle,
  team: Community,
  'user-ok': UserBadgeCheck,
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
