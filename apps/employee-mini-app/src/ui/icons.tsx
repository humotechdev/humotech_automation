/**
 * Иконки — одна библиотека на всё приложение: Iconoir.
 *
 * Раньше здесь лежали шестнадцать нарисованных вручную SVG. Библиотека
 * их заменила целиком, а не встала рядом: два набора линий разной
 * толщины в одном интерфейсе видно сразу, и чинить пришлось бы оба.
 * Сборку это не утяжеляет — в неё попадают только те глифы, которые
 * действительно импортированы.
 *
 * Модуль остался единственным входом: экраны по-прежнему берут иконки
 * отсюда по прежним именам. Поэтому смена библиотеки — это правка
 * одного файла, а не сорока мест.
 *
 * Все иконки одной толщины и рисуются `currentColor` — цвет задаёт то
 * место, куда иконку поставили, а не она сама. Цветных подложек,
 * кружков и квадратов под иконками в приложении нет: цвет несёт линия.
 */

import type { ComponentType, SVGProps } from 'react';
import {
  Attachment,
  Bell,
  Building,
  Calendar,
  Camera,
  ChatBubbleEmpty,
  Check,
  Clock,
  ClockRotateRight,
  EditPencil,
  GraphUp,
  Home,
  List,
  MediaImage,
  Megaphone,
  NavArrowRight,
  Page,
  PagePlus,
  PcNoEntry,
  PercentageCircle,
  Refresh,
  ReportColumns,
  ScanQrCode,
  SendDiagonal,
  Umbrella,
  User,
  WarningCircle,
  Xmark,
} from 'iconoir-react';

interface IconProps {
  /** Размер стороны в пикселях. По умолчанию 20 — под текст 15–16 px. */
  size?: number;
  className?: string;
}

type Glyph = ComponentType<SVGProps<SVGSVGElement> & { strokeWidth?: number }>;

/**
 * Обёртка вокруг глифа: один размер по умолчанию, одна толщина линии.
 *
 * `aria-hidden` стоит на всех: иконка здесь всегда рядом с текстом либо
 * внутри кнопки, у которой есть `aria-label`. Дублировать смысл незачем.
 */
function icon(Shape: Glyph) {
  return function Icon({ size = 20, className }: IconProps) {
    return (
      <Shape
        width={size}
        height={size}
        strokeWidth={1.7}
        color="currentColor"
        className={className}
        aria-hidden="true"
        focusable="false"
      />
    );
  };
}

export const HomeIcon = icon(Home);
export const ChartIcon = icon(GraphUp);
export const QrIcon = icon(ScanQrCode);
export const HistoryIcon = icon(Clock);
export const RequestsIcon = icon(Page);
export const ChevronRightIcon = icon(NavArrowRight);
export const CheckIcon = icon(Check);
export const CloseIcon = icon(Xmark);
export const AlertIcon = icon(WarningCircle);
export const ClockIcon = icon(Clock);
export const OfficeIcon = icon(Building);
export const CalendarIcon = icon(Calendar);
export const MedicalIcon = icon(PagePlus);
export const PlaneIcon = icon(Umbrella);
export const OfflineIcon = icon(PcNoEntry);
export const RefreshIcon = icon(Refresh);
export const CameraIcon = icon(Camera);
export const DocumentIcon = icon(Page);
export const ImageIcon = icon(MediaImage);

// --- главный экран ---------------------------------------------------------

export const BellIcon = icon(Bell);
export const UserIcon = icon(User);
export const MegaphoneIcon = icon(Megaphone);
/** Скрепка: приложить справку. */
export const ClipIcon = icon(Attachment);
/** Бумажный самолётик: что будет после отправки. */
export const SendIcon = icon(SendDiagonal);
export const PencilIcon = icon(EditPencil);
export const ChatIcon = icon(ChatBubbleEmpty);
export const RingsIcon = icon(PercentageCircle);
export const BarsIcon = icon(ReportColumns);
export const ListIcon = icon(List);
export const CorrectionIcon = icon(ClockRotateRight);
