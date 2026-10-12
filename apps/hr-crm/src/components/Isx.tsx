/*
 * Иконки Iconsax для карточки сотрудника. Те же имена, что у `AppIcon`,
 * плюс несколько своих — чтобы страница переходила на Iconsax заменой
 * одного импорта. В React 19 `defaultProps` у функций не работают, поэтому
 * размер, цвет и вариант передаются явно всегда.
 */
import {
  ArrowDown2, ArrowLeft, ArrowRight2, Briefcase, Building, Cake, Calendar, Call, Chart2,
  Clock, Danger, DocumentDownload, DocumentText, Edit2, Hashtag, Lock, Login, Logout, Personalcard,
  Profile2User, Send2, Sms, TaskSquare, Teacher, TickCircle, Timer1, User,
  type Icon, type IconProps,
} from 'iconsax-react';

const ICONS = {
  back: ArrowLeft,
  next: ArrowRight2,
  chevron: ArrowDown2,
  bag: Briefcase,
  building: Building,
  calendar: Calendar,
  pencil: Edit2,
  doc: DocumentText,
  clock: Clock,
  chart: Chart2,
  download: DocumentDownload,
  check: TickCircle,
  alert: Danger,
  users: Login,
  logout: Logout,
  phone: Call,
  mail: Sms,
  user: User,
  cake: Cake,
  hash: Hashtag,
  send: Send2,
  teach: Teacher,
  card: Personalcard,
  team: Profile2User,
  task: TaskSquare,
  timer: Timer1,
  lock: Lock,
} satisfies Record<string, Icon>;

export type IsxName = keyof typeof ICONS;

export function Isx({ name, size = 20, variant = 'Linear', className }: {
  name: IsxName; size?: number; variant?: IconProps['variant']; className?: string;
}) {
  const Glyph = ICONS[name];
  return (
    <Glyph size={size} color="currentColor" variant={variant}
           className={className} aria-hidden="true" focusable="false" />
  );
}
