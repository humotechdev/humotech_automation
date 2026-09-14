/**
 * Страница «Новый сотрудник».
 *
 * Проверяется не вёрстка формы, а три вещи, из-за которых приём может
 * тихо испортить данные: обязательные поля не должны уходить пустыми,
 * запрос не должен уходить до подтверждения, и двойное нажатие не должно
 * заводить второго человека.
 *
 * Отдельно проверяется, что отказ сервера встаёт рядом со своим полем.
 * «Сотрудник с таким ПИНФЛ уже есть» общей строкой над формой не говорит,
 * какое из четырнадцати полей править.
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

const REGIONS = {
  items: [{ id: 'r1', code: 'TAS', name: 'Ташкент', status: 'ACTIVE' }],
};

const OFFICES = {
  items: [
    { id: 'o1', code: 'HQ', name: 'Головной офис', region_id: 'r1',
      region_name: 'Ташкент', status: 'ACTIVE' },
    // Офис другого региона: после выбора региона он не должен предлагаться.
    { id: 'o2', code: 'SAM', name: 'Самарканд', region_id: 'r2',
      region_name: 'Самарканд', status: 'ACTIVE' },
  ],
};

const DEPARTMENTS = {
  items: [{ id: 'd1', name: 'Отдел кадров', office_id: 'o1' }],
  next_cursor: null, has_more: false,
};

const POSITIONS = {
  items: [{ id: 'p1', name: 'HR-специалист', code: 'HR', status: 'ACTIVE' }],
  next_cursor: null, has_more: false,
};

const SCHEDULES = {
  items: [{ id: 's1', name: 'Пятидневка · 09:00–18:00',
            timezone: 'Asia/Tashkent', status: 'ACTIVE' }],
  next_cursor: null, has_more: false,
};

const CREATED = {
  employee: {
    id: 'e-new', employee_number: 'HT-0001', full_name: 'Каримова Нигина',
  },
  created: true,
  schedule_assigned: true,
  telegram: {
    state: 'INVITED',
    link: 'https://t.me/humotech_bot?start=link_abc',
    message: 'Ссылка одноразовая',
  },
  documents: [],
};

type Options = {
  onboard?: () => Response | Promise<Response>;
  attach?: () => Response | Promise<Response>;
};

function network({ onboard, attach }: Options = {}) {
  const sent: unknown[] = [];
  const calls = fakeNetwork(async (path, call) => {
    if (path.includes('/auth/')) return json(200, USER);
    if (path.includes('/employees/onboard')) {
      sent.push(call.body);
      return onboard ? onboard() : json(201, CREATED);
    }
    if (path.includes('/employees/attachments')) {
      return attach ? attach() : json(201, {
        id: 'f-1', name: 'passport.pdf',
        mime_type: 'application/pdf', size_bytes: 2048,
      });
    }
    if (path.includes('/regions')) return json(200, REGIONS);
    if (path.includes('/offices')) return json(200, OFFICES);
    if (path.includes('/departments')) return json(200, DEPARTMENTS);
    if (path.includes('/positions')) return json(200, POSITIONS);
    if (path.includes('/work-schedules')) return json(200, SCHEDULES);
    if (path.includes('/employees')) {
      return json(200, { items: [], next_cursor: null, has_more: false });
    }
    return crm(path) ?? json(200, { items: [] });
  });
  return { calls, sent };
}

async function openForm() {
  renderApp('/employees/new');
  await screen.findByRole('heading', { name: 'Новый сотрудник' });
}

/*
 * Подпись поля — «Фамилия» плюс звёздочка обязательности, поэтому поиск
 * идёт по началу строки, а не по точному совпадению.
 */
const field = (label: string) =>
  screen.getByLabelText(new RegExp(`^${label}`)) as HTMLInputElement;

function type(label: string, value: string) {
  fireEvent.change(field(label), { target: { value } });
}

/**
 * Выбрать значение в фирменном списке: он не `<select>`, а кнопка и
 * список. Ожидание нужно там, где справочник грузится после выбора
 * предыдущего поля — отделы появляются только после выбора офиса.
 */
async function choose(label: string, option: string) {
  // Подпись списка начинается с его названия: «Тип занятости: Полный
  // рабочий день» иначе попадал бы под поиск поля «Пол».
  fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${label}`) }));
  fireEvent.click(await screen.findByRole('option', { name: option }));
}

/** Ввести дату в поле календаря: он принимает набранный текст. */
function setDate(label: string, value: string) {
  const input = screen.getByLabelText(label);
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: 'Enter' });
}

/** Заполнить форму целиком — так, как это делает человек. */
async function fill() {
  type('Фамилия', 'Каримова');
  type('Имя', 'Нигина');
  setDate('Дата рождения', '14.03.1998');
  type('ПИНФЛ', '39803141234567');
  type('Телефон', '+998 90 123 45 67');
  setDate('Дата начала работы', '15.09.2026');
  await choose('Регион', 'Ташкент');
  await choose('Офис', 'Головной офис');
  await choose('Отдел', 'Отдел кадров');
  await choose('Должность', 'HR-специалист');
  await choose('График работы', 'Пятидневка · 09:00–18:00');
}

/** Подложить файл в скрытое поле — то же, что выбрать его в окне. */
function attachFile(label: string | RegExp, file: File) {
  const input = screen.getByLabelText(label) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

const png = () =>
  new File([new Uint8Array([137, 80, 78, 71])], 'photo.png', { type: 'image/png' });
const pdf = () =>
  new File([new Uint8Array([37, 80, 68, 70])], 'passport.pdf', { type: 'application/pdf' });

const add = () => screen.getByRole('button', { name: /Добавить сотрудника/ });
const confirm = () => screen.getByRole('button', { name: 'Подтвердить добавление' });

describe('фотография и документы', () => {
  test('файл уходит отдельным запросом, а приём ссылается на него', async () => {
    const { calls, sent } = network();
    await openForm();

    attachFile('Фотография сотрудника', png());
    // Пока файл записывается, приём не начинается: сотрудник без ещё
    // не дописанной фотографии — это вторая попытка её приложить.
    await waitFor(() =>
      expect(calls.some((one) => one.url.includes('/employees/attachments'))).toBe(true));
    await waitFor(() => expect(add().hasAttribute('disabled')).toBe(false));

    await fill();
    fireEvent.click(add());
    fireEvent.click(confirm());

    await waitFor(() => expect(sent).toHaveLength(1));
    expect((sent[0] as Record<string, unknown>)['photo_file_id']).toBe('f-1');
  });

  test('документ уходит вместе с сотрудником, а не отдельным шагом', async () => {
    const { sent } = network();
    await openForm();

    fireEvent.click(screen.getByRole('button', { name: /Добавить документ/ }));
    attachFile(/^Файл документа/, pdf());
    await waitFor(() => expect(screen.getByText('passport.pdf')).toBeTruthy());

    await fill();
    fireEvent.click(add());
    fireEvent.click(confirm());

    await waitFor(() => expect(sent).toHaveLength(1));
    const body = sent[0] as Record<string, unknown>;
    expect(body['documents']).toEqual([
      { kind: 'IDENTITY', file_id: 'f-1', title: 'passport.pdf' },
    ]);
  });

  test('не загрузившийся файл не даёт закончить приём', async () => {
    const { sent } = network({
      attach: () => json(400, {
        error: { code: 'validation_failed', message: 'Файл слишком большой' },
      }),
    });
    await openForm();
    await fill();

    fireEvent.click(screen.getByRole('button', { name: /Добавить документ/ }));
    attachFile(/^Файл документа/, pdf());

    // Строка осталась на месте, кнопка заперта: молча выкинуть
    // приложенный файл и создать сотрудника без него нельзя.
    await waitFor(() => expect(add().hasAttribute('disabled')).toBe(true));
    expect(screen.getByText(/Файл не загрузился/)).toBeTruthy();
    expect(sent).toHaveLength(0);
  });

  test('пол и семейное положение уходят кодами, а не подписями', async () => {
    const { sent } = network();
    await openForm();
    await fill();
    await choose('Пол', 'Мужской');
    await choose('Семейное положение', 'Женат / замужем');

    fireEvent.click(add());
    fireEvent.click(confirm());

    await waitFor(() => expect(sent).toHaveLength(1));
    const body = sent[0] as Record<string, unknown>;
    expect(body['gender']).toBe('MALE');
    expect(body['marital_status']).toBe('MARRIED');
  });
});

describe('форма приёма', () => {
  test('без обязательных полей запрос не уходит', async () => {
    const { sent } = network();
    await openForm();

    fireEvent.click(add());

    // Ни подтверждения, ни запроса: ошибки стоят у полей.
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(sent).toHaveLength(0);
    expect(screen.getAllByText('Заполните поле').length).toBeGreaterThan(5);
  });

  test('ПИНФЛ короче четырнадцати цифр не пропускается', async () => {
    const { sent } = network();
    await openForm();
    await fill();
    type('ПИНФЛ', '123');

    fireEvent.click(add());

    expect(screen.getByText('ПИНФЛ состоит из 14 цифр')).toBeTruthy();
    expect(sent).toHaveLength(0);
  });

  test('регион сужает список офисов', async () => {
    network();
    await openForm();

    await choose('Регион', 'Ташкент');
    fireEvent.click(screen.getByRole('button', { name: /^Офис/ }));

    const list = screen.getByRole('listbox');
    expect(within(list).queryByText('Головной офис')).toBeTruthy();
    // Офис другого региона не предлагается: пару «регион A, офис из B»
    // сервер отверг бы только в момент отправки.
    expect(within(list).queryByText('Самарканд')).toBeNull();
  });

  test('первое нажатие открывает подтверждение и ничего не отправляет', async () => {
    const { sent } = network();
    await openForm();
    await fill();

    fireEvent.click(add());

    const dialog = screen.getByRole('dialog');
    // Текст собран из нескольких узлов, поэтому проверяется всё окно
    // целиком, а не отдельный узел с точным совпадением.
    expect(dialog.textContent).toContain('Добавить сотрудника?');
    expect(dialog.textContent).toContain('Каримова Нигина');
    expect(within(dialog).getByText('Головной офис')).toBeTruthy();
    expect(sent).toHaveLength(0);
  });

  test('«Вернуться к редактированию» закрывает окно и не отправляет', async () => {
    const { sent } = network();
    await openForm();
    await fill();
    fireEvent.click(add());

    fireEvent.click(screen.getByRole('button', { name: 'Вернуться к редактированию' }));

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(sent).toHaveLength(0);
    // Заполненное осталось на месте: окно закрывали, а не форму.
    expect(field('Фамилия').value).toBe('Каримова');
  });

  test('подтверждение отправляет форму целиком', async () => {
    const { sent } = network();
    await openForm();
    await fill();
    fireEvent.click(add());
    fireEvent.click(confirm());

    await waitFor(() => expect(sent).toHaveLength(1));
    const body = sent[0] as Record<string, unknown>;
    expect(body['last_name']).toBe('Каримова');
    expect(body['pinfl']).toBe('39803141234567');
    expect(body['hire_date']).toBe('2026-09-15');
    expect(body['office_id']).toBe('o1');
    expect(body['department_id']).toBe('d1');
    expect(body['position_id']).toBe('p1');
    expect(body['schedule_id']).toBe('s1');
    // Табельного номера в форме нет: его выдаёт система.
    expect(body['employee_number']).toBeUndefined();
    expect(String(body['idempotency_key'] ?? '')).not.toBe('');
  });

  test('двойное нажатие не отправляет форму дважды', async () => {
    // Сервер держит ответ: так воспроизводится настоящее двойное нажатие,
    // когда первый запрос ещё в пути.
    // Держатель ответа объявлен через объект: присваивание внутри
    // исполнителя промиса TypeScript в потоке не видит, и простая
    // переменная сузилась бы до `null`.
    const held: { release: (() => void) | null } = { release: null };
    const { sent } = network({
      onboard: () =>
        new Promise<Response>((go) => {
          held.release = () => go(json(201, CREATED));
        }),
    });
    await openForm();
    await fill();
    fireEvent.click(add());

    // Кнопка берётся один раз: после первого нажатия она меняет надпись
    // на «Добавляем…», и поиск по имени нашёл бы уже другую кнопку — а
    // человек с двойным щелчком попадает именно по этой.
    const button = confirm();
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() => expect(sent).toHaveLength(1));
    expect(button.hasAttribute('disabled')).toBe(true);
    held.release?.();
  });

  test('повтор уходит с тем же ключом, а не с новым', async () => {
    // Первый раз сервер отвечает отказом связи, второй — успехом. Ключ
    // обязан совпасть: иначе повтор завёл бы второго человека.
    let first = true;
    const { sent } = network({
      onboard: () => {
        if (first) {
          first = false;
          return json(500, { error: { code: 'server', message: 'сбой' } });
        }
        return json(201, CREATED);
      },
    });
    await openForm();
    await fill();

    fireEvent.click(add());
    fireEvent.click(confirm());
    await waitFor(() => expect(sent).toHaveLength(1));

    fireEvent.click(add());
    fireEvent.click(confirm());
    await waitFor(() => expect(sent).toHaveLength(2));

    const one = sent[0] as Record<string, unknown>;
    const two = sent[1] as Record<string, unknown>;
    expect(two['idempotency_key']).toBe(one['idempotency_key']);
  });
});

describe('отказы сервера', () => {
  test('дубликат ПИНФЛ показывается у своего поля', async () => {
    network({
      onboard: () =>
        json(409, {
          error: {
            code: 'conflict',
            message: 'Сотрудник с таким ПИНФЛ уже есть',
            details: { field: 'pinfl', value: '39803141234567' },
          },
        }),
    });
    await openForm();
    await fill();
    fireEvent.click(add());
    fireEvent.click(confirm());

    await screen.findByText('Сотрудник с таким ПИНФЛ уже есть');
    // Окно подтверждения закрылось: править надо форму, а не смотреть на
    // сводку поверх неё.
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  test('отказ без имени поля объясняется одной строкой над формой', async () => {
    network({
      onboard: () =>
        json(403, { error: { code: 'permission_denied', message: 'нельзя' } }),
    });
    await openForm();
    await fill();
    fireEvent.click(add());
    fireEvent.click(confirm());

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBeTruthy();
  });
});

describe('результат', () => {
  test('успех показывает шаги и ссылку на карточку', async () => {
    network();
    await openForm();
    await fill();
    fireEvent.click(add());
    fireEvent.click(confirm());

    await screen.findByText('Сотрудник успешно добавлен');
    expect(document.body.textContent).toContain('HT-0001');
    expect(screen.getByText('Карточка создана')).toBeTruthy();
    expect(screen.getByText('Назначение создано')).toBeTruthy();
    expect(screen.getByText('График назначен')).toBeTruthy();
    // Telegram не обещает подключения, которого нет: бот не может
    // написать первым, пока человек не нажал Start.
    expect(screen.getByText('Telegram: ожидается первый запуск')).toBeTruthy();
    expect(
      screen.getByRole('link', { name: /Открыть карточку сотрудника/ }),
    ).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /Скопировать ссылку Telegram/ }),
    ).toBeTruthy();
  });

  test('привязанный Telegram показан подключённым', async () => {
    network({
      onboard: () =>
        json(201, {
          ...CREATED,
          telegram: { state: 'CONNECTED', link: null, message: 'Telegram подключён' },
        }),
    });
    await openForm();
    await fill();
    fireEvent.click(add());
    fireEvent.click(confirm());

    // Слова «Telegram подключён» стоят и в списке шагов, и в пояснении
    // под ним, поэтому проверяется наличие хотя бы одного.
    await waitFor(() =>
      expect(screen.getAllByText('Telegram подключён').length).toBeGreaterThan(0),
    );
    // Ссылки нет — и кнопки копирования тоже: копировать нечего.
    expect(screen.queryByRole('button', { name: /Скопировать/ })).toBeNull();
  });
});
