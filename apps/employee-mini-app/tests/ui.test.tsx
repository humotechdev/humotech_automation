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
import { Home } from '../src/screens/Home';
import { Profile } from '../src/screens/Profile';
import { forgetToken, rememberToken } from '../src/auth';
import type { AbsenceRequest } from '../src/api';
import { AbsenceForm, RequestCard, Requests } from '../src/screens/Requests';
import { ScanResult } from '../src/screens/Scan';
import { greeting } from '../src/screens/Home';
import { BottomNavigation } from '../src/ui/BottomNavigation';
import { TopBar, initials } from '../src/ui/TopBar';
import { EmptyState, ErrorState, LoadingScreen, OfflineBanner } from '../src/ui/states';
import { ConfirmationDialog } from '../src/ui/overlays';
import { FileUploadField } from '../src/ui/fields';
import { PrimaryButton } from '../src/ui/primitives';
import {
  TZ,
  day,
  fakeFetch,
  options,
  pending,
  profile,
  ready,
  request,
  session,
  status,
} from './fixtures';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  forgetToken();
});

const noop = () => undefined;

// --- 1. нижняя навигация ----------------------------------------------------

describe('нижняя навигация', () => {
  it('переключает четыре раздела', () => {
    const seen: string[] = [];
    const { rerender } = render(
      <BottomNavigation active="home" onChange={(tab) => seen.push(tab)} />,
    );

    for (const label of ['Главная', 'Отметки', 'Заявки', 'Профиль']) {
      fireEvent.click(screen.getByRole('button', { name: label }));
    }
    expect(seen).toEqual(['home', 'history', 'requests', 'profile']);

    // Активный раздел объявляется диктором, а не только красится.
    rerender(<BottomNavigation active="history" onChange={noop} />);
    expect(screen.getByRole('button', { name: 'Отметки' })).toHaveProperty(
      'ariaCurrent',
      'page',
    );
  });

  it('сканера среди вкладок нет: он открывается с карточки статуса', () => {
    render(<BottomNavigation active="home" onChange={noop} />);
    expect(screen.queryByRole('button', { name: 'Отметка' })).toBeNull();
    expect(screen.getAllByRole('button')).toHaveLength(4);
  });

  it('активный раздел — синий текст и иконка, без цветной плашки', () => {
    const { container } = render(
      <BottomNavigation active="home" onChange={noop} />,
    );
    const active = container.querySelector('.nav-item-active');
    // Отличается только классом пункта: отдельной подложки под иконкой
    // в разметке нет вовсе.
    expect(active?.querySelector('.nav-icon')?.className).toBe('nav-icon');
  });

  it('нажимается вся кнопка, а не только иконка', () => {
    // Подпись внутри кнопки, а не рядом: иначе половина площади
    // пункта не реагирует на касание.
    render(<BottomNavigation active="home" onChange={noop} />);
    const item = screen.getByRole('button', { name: 'Отметки' });
    expect(within(item).getByText('Отметки')).toBeTruthy();
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

// Главный экран проверяется отдельным файлом: `home.test.tsx`. Здесь
// остались общие части интерфейса, которые он использует.

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

const sick = {
  code: 'SICK_LEAVE',
  name: 'Больничный',
  requires_document: true,
  deducts_leave_balance: false,
};

describe('заявки', () => {
  it.each([
    ['WAITING_DOCUMENTS', 'Ожидаем документы'],
    ['HR_REVIEW', 'На проверке HR'],
    ['NEEDS_FIX', 'Нужны исправления'],
    ['PENDING', 'На согласовании'],
    ['APPROVED', 'Подтверждён'],
    ['REJECTED', 'Отклонён'],
    ['CANCELLED', 'Отменён'],
  ] as Array<[AbsenceRequest['stage'], string]>)(
    'стадия %s подписана словами «%s»',
    (stage, text) => {
    // Стадию считает сервер: подтверждение больничного — это строка в
    // табеле, и второе мнение о нём приложению иметь не положено.
      render(
        <RequestCard
          request={request({ stage })}
          onCancel={noop}
          onChanged={noop}
        />,
      );
      expect(screen.getByText(text)).toBeTruthy();
    },
  );

  it('цвет состояния лежит на кромке карточки, а не на всей заливке', () => {
    // Заявок в списке несколько. Залитая целиком карточка кричит громче
    // соседних, и экран превращается в светофор без главного.
    const { container } = render(
      <RequestCard
        request={request({ status: 'REJECTED', stage: 'REJECTED' })}
        onCancel={noop}
        onChanged={noop}
      />,
    );
    const card = container.querySelector('.rq-card');
    expect(card?.className).toBe('rq-card rq-card--danger');
    expect(container.querySelector('.rq-card__state')).toBeTruthy();
  });

  it('пока нет справки, карточка говорит об этом, а не про решение', () => {
    render(
      <RequestCard
        request={request({
          absence_type: sick,
          stage: 'WAITING_DOCUMENTS',
          certificate_status: null,
        })}
        onCancel={noop}
        onChanged={noop}
      />,
    );
    expect(screen.getByText('Ожидаем документы')).toBeTruthy();
    expect(screen.getByText('Прикрепить справку')).toBeTruthy();
  });

  it('приложенная справка не выдаётся за принятую', () => {
    // Зелёная галочка «принято» на непроверенной бумаге — это обещание
    // от имени кадровика, которого он не давал.
    render(
      <RequestCard
        request={request({
          absence_type: sick,
          stage: 'HR_REVIEW',
          certificate_status: 'PENDING',
        })}
        onCancel={noop}
        onChanged={noop}
      />,
    );
    expect(screen.getByText('На проверке HR')).toBeTruthy();
    expect(screen.getByText('Справка приложена, ждёт проверки')).toBeTruthy();
    expect(screen.queryByText('Справка принята')).toBeNull();
  });

  it('отклонённая справка оставляет дорогу принести другую', () => {
    // Счётчик документов на этом месте закрыл бы человеку выход: бумага
    // есть, но она не годится, а приложить новую уже нечем.
    render(
      <RequestCard
        request={request({
          absence_type: sick,
          stage: 'NEEDS_FIX',
          certificate_status: 'REJECTED',
          certificate_comment: 'Фото нечитаемое',
          documents: 1,
        })}
        onCancel={noop}
        onChanged={noop}
      />,
    );
    expect(screen.getByText('Нужны исправления')).toBeTruthy();
    expect(
      screen.getByText('Справку не приняли. Комментарий HR: Фото нечитаемое'),
    ).toBeTruthy();
    expect(screen.getByText('Приложить другую справку')).toBeTruthy();
  });

  it('принятая справка не просит принести ещё одну', () => {
    render(
      <RequestCard
        request={request({
          absence_type: sick,
          status: 'APPROVED',
          stage: 'APPROVED',
          certificate_status: 'VERIFIED',
          documents: 1,
          can_cancel: false,
        })}
        onCancel={noop}
        onChanged={noop}
      />,
    );
    expect(screen.getByText('Справка принята')).toBeTruthy();
    expect(screen.queryByText('Прикрепить справку')).toBeNull();
  });

  it('у продления справку не просят вовсе', () => {
    // Продление подтверждается по исходной заявке, которая все три
    // пункта уже прошла. Просить у него справку значит просить бумагу,
    // которой никто не ждёт и которая ничего не откроет.
    render(
      <RequestCard
        request={request({
          kind: 'EXTEND',
          absence_type: sick,
          stage: 'PENDING',
          certificate_status: null,
        })}
        onCancel={noop}
        onChanged={noop}
      />,
    );
    expect(screen.getByText('На согласовании')).toBeTruthy();
    expect(screen.queryByText('Прикрепить справку')).toBeNull();
  });

  it('запрет организации доносить справку убирает строку, а не ломает её', () => {
    // Организация может не принимать бумаги после решения. Тогда
    // строки нет — вместо кнопки, которая упрётся в отказ сервера.
    render(
      <RequestCard
        request={request({
          absence_type: sick,
          status: 'APPROVED',
          stage: 'APPROVED',
          certificate_status: 'REJECTED',
          documents: 1,
        })}
        onCancel={noop}
        onChanged={noop}
        lateDocuments={false}
      />,
    );
    expect(screen.queryByText('Прикрепить справку')).toBeNull();
    expect(screen.queryByText('Приложить другую справку')).toBeNull();
  });

  it('подтверждённая заявка уходит в историю', async () => {
    // Решённое дело: приложить нечего, отменить нельзя. Место такому
    // — там, где смотрят прошлое, а не в списке текущих дел.
    vi.stubGlobal(
      'fetch',
      fakeFetch({
        '/me/absences/options': options,
        '/me/leave-balance': { balances: [] },
        '/me/absences': {
          requests: [
            request({
              id: 'r-done',
              absence_type: sick,
              status: 'APPROVED',
              stage: 'APPROVED',
              certificate_status: 'VERIFIED',
              can_cancel: false,
            }),
          ],
          total: 1,
        },
      }).impl,
    );
    rememberToken('токен-сеанса');

    render(<Requests fullName="Иванов Иван" />);

    expect(await screen.findByText('Сейчас нет активных заявок')).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: 'История' }));
    expect(await screen.findByText('Подтверждён')).toBeTruthy();
    // И справка при этом остаётся доступной: бумагу приносил человек.
    expect(screen.getByText('Прислать справку в чат')).toBeTruthy();
  });

  it('незакрытый больничный ведёт к себе, а не к новой форме', async () => {
    // Сервер второго не создаст: незакрытый больничный только один.
    // Показать форму значило бы обещать то, чего не будет.
    vi.stubGlobal(
      'fetch',
      fakeFetch({
        '/me/absences/options': options,
        '/me/leave-balance': { balances: [] },
        '/me/absences': {
          requests: [
            request({
              absence_type: sick,
              stage: 'WAITING_DOCUMENTS',
              certificate_status: null,
            }),
          ],
          total: 1,
        },
      }).impl,
    );
    rememberToken('токен-сеанса');

    render(<Requests fullName="Иванов Иван" />);

    const row = await screen.findByText('Открыть текущий больничный');
    expect(row).toBeTruthy();
    // И открывает саму заявку, а не переключает вкладку: человек уже
    // стоит на «Активных», и переключение выглядит как «ничего не
    // произошло».
    fireEvent.click(row);
    const sheet = await screen.findByRole('dialog');
    // Та же форма, что и при создании: те же поля в том же порядке,
    // только заполненные из заявки и закрытые на правку.
    const first = within(sheet).getByLabelText('С какого дня') as HTMLInputElement;
    const last = within(sheet).getByLabelText('По какой день') as HTMLInputElement;
    expect(first.value).toBe('2026-10-05');
    expect(last.value).toBe('2026-10-16');
    expect(first.disabled).toBe(true);
    expect(last.disabled).toBe(true);
    expect(
      (within(sheet).getByLabelText(/Комментарий/) as HTMLTextAreaElement).disabled,
    ).toBe(true);
    // Согласие и «создать заявку» тут ни при чём — заявка уже подана.
    expect(within(sheet).queryByText('Создать заявку')).toBeNull();
    // А сделать с ней есть что.
    expect(within(sheet).getByText('Прикрепить справку')).toBeTruthy();
    expect(within(sheet).getByText('Прислать заявление в чат')).toBeTruthy();
    fireEvent.click(within(sheet).getByRole('button', { name: 'Закрыть' }));
    // Подпись под строкой говорит, в каком состоянии заявка и что с ней
    // делать — иначе «открыть» ведёт в неизвестность.
    expect(
      screen.getByText(/Ожидаем документы — приложите справку/),
    ).toBeTruthy();
    expect(screen.queryByText('Оформить больничный')).toBeNull();
  });

  it('справка уходит на сервер и список перечитывается', async () => {
    const { impl, calls } = fakeFetch({ '/document': request({ documents: 1 }) });
    vi.stubGlobal('fetch', impl);
    rememberToken('токен-сеанса');
    const changed = vi.fn();

    const { container } = render(
      <RequestCard
        request={request({
          id: 'r7',
          absence_type: sick,
          stage: 'WAITING_DOCUMENTS',
          certificate_status: null,
        })}
        onCancel={noop}
        onChanged={changed}
      />,
    );

    const input = container.querySelector(
      '.rq-act input[type="file"]',
    ) as HTMLInputElement;
    const file = new File(['%PDF-1.4'], 'spravka.pdf', {
      type: 'application/pdf',
    });
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });

    await waitFor(() =>
      expect(posts(calls, '/me/absences/r7/document')).toHaveLength(1),
    );
    expect(changed).toHaveBeenCalled();
  });

  it('у закрытой заявки нет бланка для печати', () => {
    // Подписанное заявление по отклонённой заявке потом всплывёт в
    // переписке как действующее.
    render(
      <RequestCard
        request={request({
          status: 'REJECTED',
          stage: 'REJECTED',
          can_cancel: false,
        })}
        onCancel={noop}
        onChanged={noop}
      />,
    );
    expect(screen.queryByText('Заявление для печати')).toBeNull();
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
        fullName="Иванов Иван"
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
  /**
   * Своей камеры здесь больше нет.
   *
   * Она появилась из-за Android, где «сфотографировать» открывало
   * галерею, и закрыла эту дыру ценой телефона: ни вспышки, ни фокуса,
   * ни пересъёмки. Теперь три родных `input`, по одному на намерение,
   * и открывает их `<label for>` — между касанием и системным окном не
   * должно быть ни строки JavaScript, иначе WebView теряет жест.
   */

  it('камера просит заднюю камеру телефона, а не свой видоискатель', () => {
    const { container } = render(<FileUploadField file={null} onFile={noop} />);
    const camera = container.querySelector(
      'input[capture]',
    ) as HTMLInputElement;

    expect(camera.getAttribute('accept')).toBe('image/*');
    expect(camera.getAttribute('capture')).toBe('environment');
    // Открывается подписью, а не кнопкой с обработчиком: программный
    // click() — первое, что вебвью не считает действием человека.
    expect(container.querySelector(`label[for="${camera.id}"]`)).toBeTruthy();
  });

  it('галерея — тот же тип файлов, но без capture', () => {
    // Иначе телефон снова открыл бы камеру, а человеку нужно готовое фото.
    const { container } = render(<FileUploadField file={null} onFile={noop} />);
    const gallery = inputFor(container, 'Выбрать из галереи');

    expect(gallery.getAttribute('accept')).toBe('image/*');
    expect(gallery.hasAttribute('capture')).toBe(false);
  });

  it('документ принимает PDF и не открывает выбор чего угодно', () => {
    const { container } = render(
      <FileUploadField
        file={null}
        onFile={noop}
        allowedTypes={['application/pdf', 'image/jpeg', 'image/png']}
      />,
    );
    const picker = inputFor(container, 'Выбрать документ');
    const accept = picker.getAttribute('accept') ?? '';

    expect(accept).toContain('.pdf');
    expect(accept).toContain('application/pdf');
    // `*/*` открыл бы выбор чего угодно, а отказ пришёл бы уже после
    // загрузки — на мобильной связи это потерянные мегабайты.
    expect(accept).not.toContain('*/*');
  });

  it('к getUserMedia не обращается вовсе', () => {
    const getUserMedia = vi.fn();
    vi.stubGlobal('navigator', { ...navigator, mediaDevices: { getUserMedia } });

    const { container } = render(<FileUploadField file={null} onFile={noop} />);
    fireEvent.click(screen.getByText('Сфотографировать'));

    expect(getUserMedia).not.toHaveBeenCalled();
    // И никакого своего экрана поверх формы.
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(container.querySelector('video')).toBeNull();
    expect(container.querySelector('canvas')).toBeNull();
  });

  it('снимок показан картинкой, именем и размером', () => {
    const url = stubObjectUrl();
    const shot = image('спраВка.jpg', 2048);

    const { container } = render(<FileUploadField file={shot} onFile={noop} />);

    const preview = container.querySelector('img.file-preview');
    expect(preview?.getAttribute('src')).toBe(url.address);
    expect(screen.getByText('спраВка.jpg')).toBeTruthy();
    expect(screen.getByText('2 КБ')).toBeTruthy();
  });

  it('адрес снимка освобождается, когда справку убрали', () => {
    // createObjectURL держит файл в памяти вкладки, пока адрес не отозван.
    // На Android это мегабайты за каждую пересъёмку, и сами они не уходят.
    const url = stubObjectUrl();
    const { unmount } = render(
      <FileUploadField file={image('снимок.jpg', 1024)} onFile={noop} />,
    );

    expect(url.revoke).not.toHaveBeenCalled();
    unmount();
    expect(url.revoke).toHaveBeenCalledWith(url.address);
  });

  it('PDF показан иконкой и именем, без попытки нарисовать картинку', () => {
    stubObjectUrl();
    const pdf = new File(['%PDF-1.4'], 'spravka.pdf', {
      type: 'application/pdf',
    });

    const { container } = render(<FileUploadField file={pdf} onFile={noop} />);

    expect(container.querySelector('img.file-preview')).toBeNull();
    expect(container.querySelector('.file-badge svg')).toBeTruthy();
    expect(screen.getByText('spravka.pdf')).toBeTruthy();
  });

  it('неподходящий формат отклоняется на телефоне, до отправки', () => {
    // accept — только подсказка системному окну: часть файловых
    // провайдеров Android показывает мимо неё что угодно.
    const onFile = vi.fn();
    const { container } = render(
      <FileUploadField file={null} onFile={onFile} />,
    );

    choose(inputFor(container, 'Выбрать документ'), [
      new File(['PK'], 'справка.docx', {
        type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      }),
    ]);

    expect(onFile).not.toHaveBeenCalled();
    expect(screen.getByRole('alert').textContent).toContain('PDF');
  });

  it('файл без MIME принимается по расширению', () => {
    // Так отдают файлы некоторые провайдеры Android: файл настоящий,
    // а `type` пустой. Отказ по этому признаку сломал бы ровно тот
    // путь, который здесь и чинится.
    const onFile = vi.fn();
    const { container } = render(
      <FileUploadField file={null} onFile={onFile} />,
    );

    choose(inputFor(container, 'Выбрать документ'), [
      new File(['%PDF-1.4'], 'spravka.pdf', { type: '' }),
    ]);

    expect(onFile).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('расширение, спорящее с типом, не проходит', () => {
    const onFile = vi.fn();
    const { container } = render(
      <FileUploadField file={null} onFile={onFile} />,
    );

    choose(inputFor(container, 'Выбрать документ'), [
      new File(['%PDF-1.4'], 'spravka.pdf', { type: 'image/png' }),
    ]);

    expect(onFile).not.toHaveBeenCalled();
    expect(screen.getByRole('alert').textContent).toContain('Расширение');
  });

  it('слишком большой файл отклоняется с понятным размером', () => {
    const onFile = vi.fn();
    const { container } = render(
      <FileUploadField file={null} onFile={onFile} maxBytes={1024 * 1024} />,
    );

    choose(inputFor(container, 'Сфотографировать'), [
      image('огромная.jpg', 5 * 1024 * 1024),
    ]);

    expect(onFile).not.toHaveBeenCalled();
    // Число, а не «ошибка загрузки»: человеку решать, переснимать или нет.
    expect(screen.getByRole('alert').textContent).toContain('1 МБ');
  });

  it('пустой файл отклоняется', () => {
    const onFile = vi.fn();
    const { container } = render(
      <FileUploadField file={null} onFile={onFile} />,
    );

    choose(inputFor(container, 'Выбрать из галереи'), [
      image('пусто.jpg', 0),
    ]);

    expect(onFile).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toBeTruthy();
  });

  it('справку можно убрать и выбрать ту же самую снова', () => {
    // Про `input.value`: без сброса повторный выбор того же файла не даёт
    // события вовсе — значение не изменилось, и человек нажимает в пустоту.
    const onFile = vi.fn();
    const shot = image('одна-и-та-же.jpg', 4096);
    const { container, rerender } = render(
      <FileUploadField file={shot} onFile={onFile} />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Удалить' }));
    expect(onFile).toHaveBeenCalledWith(null);

    rerender(<FileUploadField file={null} onFile={onFile} />);
    const camera = inputFor(container, 'Сфотографировать');
    choose(camera, [shot]);

    expect(camera.value).toBe('');
    expect(onFile).toHaveBeenLastCalledWith(shot);
  });

  it('отмена выбора не трогает уже приложенную справку и не ругается', () => {
    // Пустое событие change — это «передумал открывать», а не «убери
    // справку». Прежний код на нём вызывал onFile(null) и стирал файл.
    const onFile = vi.fn();
    const { container } = render(
      <FileUploadField file={image('приложена.jpg', 2048)} onFile={onFile} />,
    );

    choose(inputFor(container, 'Выбрать из галереи'), []);

    expect(onFile).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByText('приложена.jpg')).toBeTruthy();
  });

  it('повторный выбор заменяет справку, а не добавляет вторую', () => {
    const onFile = vi.fn();
    const { container } = render(
      <FileUploadField file={image('первая.jpg', 2048)} onFile={onFile} />,
    );

    const second = image('вторая.jpg', 2048);
    choose(inputFor(container, 'Сфотографировать'), [second]);

    expect(onFile).toHaveBeenCalledTimes(1);
    expect(onFile).toHaveBeenCalledWith(second);
    expect(container.querySelectorAll('.file-chosen')).toHaveLength(1);
  });

  it('пока заявка уходит, приложить другую справку нельзя', () => {
    const { container } = render(
      <FileUploadField file={null} onFile={noop} disabled />,
    );

    for (const input of container.querySelectorAll('input[type="file"]')) {
      expect((input as HTMLInputElement).disabled).toBe(true);
    }
  });

  it('остальные поля формы переживают выбор справки', () => {
    // Форма собирается целиком и уходит одним запросом: файл не должен
    // ни отправляться сам по себе, ни сбрасывать уже введённое.
    const { container } = render(
      <AbsenceForm
        kind="SICK_LEAVE"
        fullName="Иванов Иван"
        options={options}
        balance={null}
        onClose={noop}
        onCreated={noop}
      />,
    );

    const comment = screen.getByLabelText(/Комментарий/) as HTMLTextAreaElement;
    fireEvent.change(comment, { target: { value: 'вернусь в среду' } });
    // Строка одна: системный выбор файла и так предлагает камеру,
    // галерею и документы, и три кнопки были ответом на вопрос,
    // которого человек не задавал.
    choose(inputFor(container, 'Прикрепить справку'), [
      image('справка.jpg', 4096),
    ]);

    expect(comment.value).toBe('вернусь в среду');
    expect(screen.getByText('справка.jpg')).toBeTruthy();
  });

  it('заявка оформляется на того, кто вошёл', () => {
    // Имя видно прямо в форме: человек должен понимать, за кого
    // расписывается, а не узнавать это из письма кадровику.
    render(
      <AbsenceForm
        kind="SICK_LEAVE"
        fullName="Мурадов Азизбек"
        options={options}
        balance={null}
        onClose={noop}
        onCreated={noop}
      />,
    );

    expect(screen.getByText('Мурадов Азизбек')).toBeTruthy();
  });

  it('без согласия с условиями заявку не создать', () => {
    render(
      <AbsenceForm
        kind="SICK_LEAVE"
        fullName="Иванов Иван"
        options={options}
        balance={null}
        onClose={noop}
        onCreated={noop}
      />,
    );

    const submit = screen.getByRole('button', { name: 'Создать заявку' });
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole('checkbox'));
    expect((submit as HTMLButtonElement).disabled).toBe(false);
  });

  it('даты у больничного пустые и необязательные', () => {
    // Человек заболел в пятницу вечером и не знает, выйдет ли во
    // вторник. Требовать от него число — получить выдуманное.
    render(
      <AbsenceForm
        kind="SICK_LEAVE"
        fullName="Иванов Иван"
        options={options}
        balance={null}
        onClose={noop}
        onCreated={noop}
      />,
    );

    const first = screen.getByLabelText('С какого дня') as HTMLInputElement;
    expect(first.value).toBe('');
    expect(first.placeholder).toBe('Не указано');
    expect(first.required).toBe(false);
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
        fullName="Назарова Сабина"
        office="Филиал Худжанд"
        today={ready({ status: status(), sessions: [] })}
        week={pending()}
        requests={pending()}
        notes={pending()}
        onScan={noop}
        onHistory={noop}
        onRequests={noop}
        onNewRequest={noop}
        onCorrection={noop}
        onQuestion={noop}
        onNote={noop}
        onWeek={noop}
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
    // Табельный номер — тоже внутренний идентификатор: человеку он
    // ничего не объясняет, а в разговоре с кадрами хватает фамилии.
    expect(container.textContent).not.toContain('DEMO-001');
    expect(container.textContent).toContain(profile.office.name);
  });
});

// --- 19. доступность --------------------------------------------------------

describe('доступность', () => {
  it('кнопка из одной иконки подписана', () => {
    render(
      <TopBar
        fullName="Рахимов Далер"
        unread={2}
        onNotifications={noop}
        onProfile={noop}
      />,
    );
    expect(screen.getByRole('button', { name: 'Открыть профиль' })).toBeTruthy();
    // Счётчик не только цветная точка: диктор называет число.
    expect(
      screen.getByRole('button', { name: 'Уведомления, непрочитанных: 2' }),
    ).toBeTruthy();
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
          distance_m: null,
          radius_m: null,
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

/** Скрытый input, который откроется по нажатию на подпись.
 *
 * Классов два: `file-field` — прежние кнопки отпуска, `sick-attach` —
 * одна строка больничного. Искать по обоим, а не по одному: форм две,
 * и у каждой свой способ приложить бумагу.
 */
function inputFor(container: HTMLElement, label: string): HTMLInputElement {
  const found = Array.from(
    container.querySelectorAll('label.file-field, label.sick-attach'),
  ).find((node) => node.textContent?.includes(label));
  if (!found) throw new Error(`нет действия «${label}»`);
  return found.querySelector('input[type="file"]') as HTMLInputElement;
}

/** Выбор в системном окне. Пустой список — окно закрыли, не выбрав. */
function choose(input: HTMLInputElement, files: File[]): void {
  fireEvent.change(input, { target: { files } });
}

/** Файл заданного размера: настоящие мегабайты в тесте держать незачем. */
function image(name: string, size: number): File {
  const file = new File([''], name, { type: 'image/jpeg' });
  Object.defineProperty(file, 'size', { value: size });
  return file;
}

/**
 * `URL.createObjectURL` в jsdom нет вовсе — не заглушка, а `undefined`.
 * Компонент это переживает и показывает имя без картинки; чтобы проверить
 * саму картинку и — важнее — освобождение адреса, метод подставляется.
 */
function stubObjectUrl(): { address: string; revoke: ReturnType<typeof vi.fn> } {
  const address = 'blob:humotech/предпросмотр';
  const revoke = vi.fn();
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: vi.fn(() => address),
    revokeObjectURL: revoke,
  });
  return { address, revoke };
}

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
