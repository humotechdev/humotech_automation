// @vitest-environment jsdom
/**
 * Экраны кабинета.
 *
 * Окружение jsdom объявлено в этом файле, а не в общей настройке
 * намеренно: `auth.test.ts` проверяет поведение при запрещённом
 * хранилище и отсутствие токена в localStorage, а под jsdom эти
 * хранилища существуют, и общий переключатель поменял бы смысл
 * тех проверок.
 *
 * Проверяется не вёрстка, а то, из-за чего интерфейс врёт или ломается:
 * выдуманное время выхода, чужие данные, потерянная безопасная зона,
 * повторная отправка заявки.
 */

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DayChart } from '../src/screens/Stats';
import { DayCard } from '../src/screens/History';
import { Home, shiftProgress } from '../src/screens/Home';
import { Profile } from '../src/screens/Profile';
import { forgetToken, rememberToken } from '../src/auth';
import { AbsenceForm, RequestCard } from '../src/screens/Requests';
import { ScanResult } from '../src/screens/Scan';
import { AppHeader, greeting, initials } from '../src/ui/AppHeader';
import { BottomNavigation } from '../src/ui/BottomNavigation';
import { StatusCard } from '../src/ui/StatusCard';
import { EmptyState, ErrorState, LoadingScreen, OfflineBanner } from '../src/ui/states';
import { ConfirmationDialog } from '../src/ui/overlays';
import { FileUploadField } from '../src/ui/fields';
import { PrimaryButton } from '../src/ui/primitives';
import {
  TZ,
  day,
  fakeFetch,
  options,
  profile,
  request,
  session,
  status,
  summary,
} from './fixtures';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  forgetToken();
});

const noop = () => undefined;

// --- 1. нижняя навигация ----------------------------------------------------

describe('нижняя навигация', () => {
  it('переключает пять разделов', () => {
    const seen: string[] = [];
    const { rerender } = render(
      <BottomNavigation active="home" onChange={(tab) => seen.push(tab)} />,
    );

    for (const label of ['Главная', 'Статистика', 'Отметка', 'История', 'Заявки']) {
      fireEvent.click(screen.getByRole('button', { name: label }));
    }
    expect(seen).toEqual(['home', 'stats', 'scan', 'history', 'requests']);

    // Активный раздел объявляется диктором, а не только красится.
    rerender(<BottomNavigation active="stats" onChange={noop} />);
    expect(screen.getByRole('button', { name: 'Статистика' })).toHaveProperty(
      'ariaCurrent',
      'page',
    );
  });

  it('кнопка QR открывает отметку и приподнята', () => {
    const seen: string[] = [];
    render(<BottomNavigation active="home" onChange={(tab) => seen.push(tab)} />);

    const scan = screen.getByRole('button', { name: 'Отметка' });
    fireEvent.click(scan);

    expect(seen).toEqual(['scan']);
    expect(scan.className).toContain('nav-item-scan');
  });

  it('нажимается вся кнопка, а не только иконка', () => {
    // Подпись внутри кнопки, а не рядом: иначе половина площади
    // пункта не реагирует на касание.
    render(<BottomNavigation active="home" onChange={noop} />);
    const item = screen.getByRole('button', { name: 'История' });
    expect(within(item).getByText('История')).toBeTruthy();
    expect(item.querySelector('svg')).toBeTruthy();
  });
});

// --- 3. безопасная зона -----------------------------------------------------

describe('безопасная зона', () => {
  it('панель отводит место под системную область телефона', () => {
    // Отступ — часть самой панели, иначе между ней и краем экрана
    // видна полоса фона, а на айфоне туда попадает системная черта.
    const css = readStyles();
    expect(css).toContain('--safe-bottom');
    expect(css).toMatch(/\.bottom-nav\s*\{[^}]*padding-bottom:\s*var\(--safe-bottom\)/);
  });

  it('содержимое отступает снизу на высоту навигации', () => {
    const css = readStyles();
    expect(css).toMatch(/\.page\s*\{[^}]*padding-bottom:\s*calc\(var\(--nav-total\)/);
  });

  it('запас считается от безопасной зоны, а не от числа', () => {
    const css = readStyles();
    expect(css).toMatch(
      /--nav-total:\s*calc\(var\(--nav-height\)\s*\+\s*var\(--safe-bottom\)\)/,
    );
  });

  it('учтены обе зоны Telegram и откат на env()', () => {
    // Зон две, и они складываются: `safe-area-inset` — вырез и полоса
    // жеста телефона, `content-safe-area-inset` — шапка самого Telegram
    // в полноэкранном режиме. Максимум из них спрятал бы заголовок под
    // шапку. На клиенте до Bot API 7.7 переменных нет вовсе, и всё
    // держится на env() — поэтому он обязан остаться в формуле.
    const css = readStyles();

    for (const side of ['top', 'bottom']) {
      const rule = css.slice(css.indexOf(`--safe-${side}:`));
      const body = rule.slice(0, rule.indexOf(';'));
      expect(body).toContain(`var(--tg-safe-area-inset-${side}, 0px)`);
      expect(body).toContain(`var(--tg-content-safe-area-inset-${side}, 0px)`);
      expect(body).toContain(`env(safe-area-inset-${side}, 0px)`);
      // Складываются, а не выбираются.
      expect(body).toContain('+');
    }
  });
});

// --- 4-5. главный экран и статусы -------------------------------------------

describe('главный экран', () => {
  it('в офисе: зелёная точка, длительность и время входа', () => {
    render(
      <StatusCard
        status={status({
          state: 'IN_OFFICE',
          open_session: session({ is_open: true, ended_at: null, seconds: 13_320 }),
        })}
      />,
    );

    expect(screen.getByText('Сейчас в офисе')).toBeTruthy();
    expect(screen.getByText('3 ч 42 мин')).toBeTruthy();
    expect(screen.getByText('Вход')).toBeTruthy();
  });

  it('открытой сессии не назначается выдуманное время выхода', () => {
    const { container } = render(
      <StatusCard
        status={status({
          state: 'IN_OFFICE',
          open_session: session({ is_open: true, ended_at: null, seconds: 13_320 }),
        })}
      />,
    );

    expect(screen.getByText(/По состоянию на сейчас/)).toBeTruthy();
    // Слова «выход» на карточке открытой сессии быть не должно вовсе.
    expect(container.textContent?.toLowerCase()).not.toContain('выход');
  });

  it.each([
    ['OUTSIDE', 'Вне офиса'],
    ['SICK_LEAVE', 'Больничный'],
    ['VACATION', 'Отпуск'],
    ['DAY_OFF', 'Сегодня выходной'],
    ['WORKDAY_MISSED', 'Рабочий день без отметок'],
  ])('состояние %s показано как «%s»', (state, text) => {
    render(<StatusCard status={status({ state })} />);
    expect(screen.getByText(text)).toBeTruthy();
  });

  it('плана и остатка нет: backend не отдаёт плановых минут', () => {
    // Смена 09:00–18:00 — это девять часов, а норма короче на обед,
    // которого в API нет. Показать «осталось 5 ч 18 мин» значило бы
    // поставить выдуманный знаменатель под рабочее время.
    const { container } = render(
      <Home
        profile={profile}
        status={status({ seconds_today: 13_320 })}
        today={summary()}
        onScan={noop}
        onHistory={noop}
        onSickLeave={noop}
        onVacation={noop}
      />,
    );

    expect(container.textContent).not.toContain('Осталось');
    expect(container.textContent).not.toContain('План');
    expect(screen.getByText('Отработано')).toBeTruthy();
  });

  it('полоса смены говорит о времени суток, а не о человеке', () => {
    const at = new Date('2026-09-04T08:30:00Z'); // 13:30 в Душанбе
    const shift = shiftProgress(status(), at);
    expect(shift).toEqual({ elapsed: 270, total: 540 });
  });

  it('без графика полосы нет вовсе', () => {
    expect(
      shiftProgress(status({ scheduled_start: null, scheduled_end: null })),
    ).toBeNull();
  });
});

// --- 6. статистика ----------------------------------------------------------

describe('статистика', () => {
  it('столбцы рисуются по фактическим секундам', () => {
    const { container } = render(
      <DayChart
        days={[
          day({ day: '2026-09-01', seconds: 28_800 }),
          day({ day: '2026-09-02', seconds: 14_400 }),
          day({ day: '2026-09-03', seconds: 0, missed: true, attended: false }),
        ]}
      />,
    );

    const bars = container.querySelectorAll('.chart-bar');
    expect(bars).toHaveLength(3);
    // Самый длинный день — 100 %, вдвое короче — 50 %.
    expect((bars[0] as HTMLElement).style.height).toBe('100%');
    expect((bars[1] as HTMLElement).style.height).toBe('50%');
    expect(bars[2].className).toContain('chart-bar-missed');
  });

  it('у графика есть текстовая замена для диктора', () => {
    render(<DayChart days={[day({ seconds: 28_800 })]} />);
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('8 ч');
  });

  it('пустой период не рисует пустой график', () => {
    render(<DayChart days={[day({ seconds: 0, attended: false })]} />);
    expect(screen.getByText('За этот период отметок нет.')).toBeTruthy();
  });
});

// --- 7. история -------------------------------------------------------------

describe('история', () => {
  it('группируется по дням: вход, выход и итог', () => {
    render(
      <DayCard
        day={day({
          sessions: [
            session({ id: 'a', started_at: '2026-09-04T03:54:00Z',
                      ended_at: '2026-09-04T08:02:00Z' }),
            session({ id: 'b', started_at: '2026-09-04T09:01:00Z',
                      ended_at: '2026-09-04T13:07:00Z' }),
          ],
        })}
        timeZone={TZ}
      />,
    );

    expect(screen.getByText('4 сентября')).toBeTruthy();
    expect(screen.getAllByText('вход')).toHaveLength(2);
    expect(screen.getAllByText('выход')).toHaveLength(2);
    expect(screen.getByText('08:54')).toBeTruthy();
    expect(screen.getByText('8 ч 14 мин')).toBeTruthy();
  });

  it('открытая сессия помечена, а выход — прочерк', () => {
    render(
      <DayCard
        day={day({
          has_open_session: true,
          sessions: [session({ is_open: true, ended_at: null })],
        })}
        timeZone={TZ}
      />,
    );

    expect(screen.getByText('ещё в офисе')).toBeTruthy();
    expect(screen.getByText('—')).toBeTruthy();
    expect(screen.getByText(/Сессия не закрыта/)).toBeTruthy();
  });

  it('рабочий день без отметок назван словом, а не только цветом', () => {
    render(
      <DayCard
        day={day({ seconds: 0, attended: false, missed: true, sessions: [] })}
        timeZone={TZ}
      />,
    );
    expect(screen.getByText('Рабочий день без отметок')).toBeTruthy();
  });
});

// --- 8-9. заявки ------------------------------------------------------------

describe('заявки', () => {
  it.each([
    ['SUBMITTED', 'ожидает решения'],
    ['APPROVED', 'подтверждена'],
    ['REJECTED', 'отклонена'],
    ['CANCELLED', 'отменена'],
  ])('статус %s подписан словом «%s»', (state, text) => {
    render(<RequestCard request={request({ status: state })} onCancel={noop} />);
    expect(screen.getByText(text)).toBeTruthy();
  });

  it('карточка не заливается цветом статуса целиком', () => {
    const { container } = render(
      <RequestCard request={request({ status: 'REJECTED' })} onCancel={noop} />,
    );
    const card = container.querySelector('.card');
    expect(card?.className).toBe('card');
  });

  it('требование справки видно до отправки', () => {
    render(
      <RequestCard
        request={request({
          absence_type: {
            code: 'SICK_LEAVE',
            name: 'Больничный',
            requires_document: true,
            deducts_leave_balance: false,
          },
          documents: 0,
        })}
        onCancel={noop}
      />,
    );
    expect(screen.getByText('Нужна справка')).toBeTruthy();
  });

  it('подтверждение блокирует повторную отправку', () => {
    // Заявка на отпуск, поданная дважды, — это два вычета из остатка.
    const send = vi.fn();
    render(
      <ConfirmationDialog
        open
        title="Отправить заявку?"
        busy
        onConfirm={send}
        onCancel={noop}
      />,
    );

    const confirm = screen.getByRole('button', { name: 'Отправляем…' });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(confirm);
    expect(send).not.toHaveBeenCalled();
  });

  it('сама форма не отправляет заявку дважды', async () => {
    // Отключённая кнопка — это про вид. Настоящий замок стоит в форме,
    // и проверять надо его: `disabled` появляется только со следующей
    // отрисовкой, а два быстрых касания успевают попасть в один кадр.
    // Две ушедшие заявки на отпуск — два резерва дней из одного остатка.
    const { impl, calls } = fakeFetch({ '/me/absences': request() });
    vi.stubGlobal('fetch', impl);
    rememberToken('токен-сеанса');

    render(
      <AbsenceForm
        kind="ANNUAL_LEAVE"
        options={options}
        balance={14}
        onClose={noop}
        onCreated={noop}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Отправить заявку' }));
    const confirm = await screen.findByRole('button', { name: 'Отправить' });

    // Два касания внутри одного кадра. Через `fireEvent` так не выйдет:
    // он прогоняет отрисовку между вызовами, и второе нажатие приходит
    // уже на отключённую кнопку — то есть проверяло бы `disabled`, а не
    // замок. Здесь оба события уходят до того, как React перерисовал.
    await act(async () => {
      confirm.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      confirm.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    await waitFor(() =>
      expect(posts(calls, '/me/absences')).toHaveLength(1),
    );
    // И после завершения запроса тоже ровно одна.
    expect(posts(calls, '/me/absences')).toHaveLength(1);
  });
});

// --- справка ----------------------------------------------------------------

describe('загрузка справки', () => {
  it('даёт и камеру, и выбор файла', () => {
    // Панели вложений Telegram у мини-приложения нет: в WebApp API есть
    // только downloadFile. Родное системное окно — единственный путь,
    // и камера вынесена отдельно, потому что справку фотографируют.
    const { container } = render(
      <FileUploadField file={null} onFile={noop} accept="application/pdf" />,
    );

    const inputs = container.querySelectorAll('input[type="file"]');
    expect(inputs).toHaveLength(2);
    expect(inputs[0].getAttribute('capture')).toBe('environment');
    expect(inputs[0].getAttribute('accept')).toBe('image/*');
    expect(inputs[1].getAttribute('accept')).toBe('application/pdf');
    expect(screen.getByText('Сфотографировать')).toBeTruthy();
  });

  it('выбранный файл показан по имени и снимается', () => {
    const onFile = vi.fn();
    render(
      <FileUploadField
        file={new File(['%PDF-1.4'], 'spravka.pdf', { type: 'application/pdf' })}
        onFile={onFile}
      />,
    );

    expect(screen.getByText('spravka.pdf')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Убрать' }));
    expect(onFile).toHaveBeenCalledWith(null);
  });

  it('у поля есть подпись, связанная с элементом', () => {
    const { container } = render(<FileUploadField file={null} onFile={noop} />);
    const label = container.querySelector('label[for]');
    expect(label?.textContent).toContain('Справка');
  });
});

// --- 10-13. состояния -------------------------------------------------------

describe('состояния экрана', () => {
  it('скелет повторяет форму содержимого, а не крутит колесо', () => {
    const { container } = render(<LoadingScreen cards={3} />);
    expect(container.querySelectorAll('.skeleton-card')).toHaveLength(3);
    expect(screen.getByRole('status').textContent).toBe('Загружаем данные');
  });

  it('пустое состояние объясняет, что делать', () => {
    render(
      <EmptyState title="Отметок нет" description="За период записей нет." />,
    );
    expect(screen.getByText('Отметок нет')).toBeTruthy();
    expect(screen.getByText('За период записей нет.')).toBeTruthy();
  });

  it('ошибка даёт кнопку повтора, а не тупик', () => {
    const retry = vi.fn();
    render(<ErrorState message="Сервер недоступен" onRetry={retry} />);

    expect(screen.getByRole('alert')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Обновить/ }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it('офлайн — узкая полоса, а не экран-заглушка', () => {
    const retry = vi.fn();
    const { container } = render(<OfflineBanner onRetry={retry} />);

    expect(screen.getByRole('status').textContent).toContain('Нет связи');
    expect(container.querySelector('.offline-banner')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Ещё раз' }));
    expect(retry).toHaveBeenCalledOnce();
  });
});

// --- 17. чужие данные -------------------------------------------------------

describe('только свои данные', () => {
  it('экраны рисуют то, что пришло, и ничего не подставляют', () => {
    const { container } = render(
      <Home
        profile={{
          ...profile,
          employee: { ...profile.employee, full_name: 'Назарова Сабина' },
          office: { ...profile.office, name: 'Филиал Худжанд' },
        }}
        status={status()}
        today={null}
        onScan={noop}
        onHistory={noop}
        onSickLeave={noop}
        onVacation={noop}
      />,
    );

    expect(container.textContent).toContain('Филиал Худжанд');
    expect(container.textContent).not.toContain('Рахимов');
    expect(container.textContent).not.toContain('Головной офис');
  });

  it('профиль не показывает внутренних идентификаторов', () => {
    const { container } = render(
      <Profile profile={profile} status={status()} version="1.0" onSignOut={noop} />,
    );

    expect(container.textContent).not.toContain(profile.employee.id);
    expect(container.textContent).not.toContain(profile.office.id);
    expect(container.textContent).toContain('DEMO-001');
  });
});

// --- 19. доступность --------------------------------------------------------

describe('доступность', () => {
  it('кнопка из одной иконки подписана', () => {
    render(
      <AppHeader
        fullName="Рахимов Далер"
        office="Головной офис"
        position="Инженер"
        timeZone={TZ}
        onProfile={noop}
      />,
    );
    expect(screen.getByRole('button', { name: 'Профиль и помощь' })).toBeTruthy();
  });

  it('итог отметки объявляется словом, а не только значком', () => {
    render(
      <ScanResult
        result={{
          status: 'ENTERED',
          accepted: true,
          office_name: 'Головной офис',
          point_name: 'Главный вход',
          occurred_at: '2026-09-04T03:54:00Z',
          session: null,
        }}
        timeZone={TZ}
        onHome={noop}
        onAgain={noop}
      />,
    );

    expect(screen.getByText('Вход отмечен')).toBeTruthy();
    // Время — серверное, в поясе офиса.
    expect(screen.getByText('08:54')).toBeTruthy();
  });

  it('главная кнопка экрана — настоящая кнопка с текстом', () => {
    render(<PrimaryButton onClick={noop}>Отметиться</PrimaryButton>);
    expect(screen.getByRole('button', { name: 'Отметиться' })).toBeTruthy();
  });

  it('инициалы и приветствие берутся из данных, а не из Telegram', () => {
    expect(initials('Рахимов Далер')).toBe('РД');
    expect(initials('Cher')).toBe('C');
    expect(greeting(9)).toBe('Доброе утро');
    expect(greeting(20)).toBe('Добрый вечер');
  });
});

// --- вспомогательное --------------------------------------------------------

/** Ушедшие на сервер записи по пути и методу. */
function posts(
  calls: Array<{ url: string; init?: RequestInit }>,
  path: string,
): Array<{ url: string; init?: RequestInit }> {
  return calls.filter(
    (call) => call.url.includes(path) && call.init?.method === 'POST',
  );
}

/** Читает собранный CSS: проверки безопасной зоны смотрят именно в него. */
function readStyles(): string {
  const fs = require('node:fs') as typeof import('node:fs');
  const path = require('node:path') as typeof import('node:path');
  const root = path.join(__dirname, '..', 'src', 'styles');
  return (
    fs.readFileSync(path.join(root, 'tokens.css'), 'utf8') +
    fs.readFileSync(path.join(root, 'app.css'), 'utf8')
  );
}
