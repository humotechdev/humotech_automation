/**
 * Безопасность CRM на стороне браузера.
 *
 * Данные в CRM вводят не только HR: имя, текст обращения, название
 * файла, комментарий приходят от сотрудника из Telegram. Проверяется,
 * что такие строки остаются текстом, что адреса из данных и из адресной
 * строки не уводят за пределы CRM и не меняют маршрут запроса, и что
 * после выхода в памяти вкладки не остаётся ответов для прежнего
 * человека. Плюс статический сторож: опасные приёмы в исходниках.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useEffect } from 'react';
import { Link, MemoryRouter } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';

import { csrfToken, isSafePath, request, upload } from '../src/api/client';
import type { FeedDetail } from '../src/api/crm';
import { ApiFailure, SCOPE_LIMITED, messageFor, reasonFor } from '../src/api/errors';
import { refusalText } from '../src/components/admin/Parts';
import { toCsv } from '../src/pages/OnboardingPage';
import { suppressedText } from '../src/pages/SurveyCampaignPage';
import { actionTitle } from '../src/features/admin/model';
import { FeedCard } from '../src/components/FeedParts';
import { FileRow } from '../src/features/requests/model';
import { useBadges } from '../src/features/shell/badges';
import { forgetNavigation } from '../src/features/shell/memory';
import { internalPath } from '../src/utils/internal-path';
import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

/** Полезные нагрузки: каждая — строка, которую сотрудник может прислать сам. */
const XSS = [
  '<img src=x onerror="window.__pwned=1">',
  '<script>window.__pwned=1</script>',
  '"><svg onload="window.__pwned=1">',
  '<a href="javascript:window.__pwned=1">жми</a>',
  '[жми](javascript:window.__pwned=1)',
  '{{constructor.constructor("window.__pwned=1")()}}',
] as const;

/** В дереве нет ни исполняемого узла, ни обработчика из данных, ни `javascript:`. */
function assertInert(root: ParentNode = document.body) {
  expect(root.querySelectorAll('script').length).toBe(0);
  expect(root.querySelectorAll('svg[onload], img[onerror]').length).toBe(0);
  for (const element of Array.from(root.querySelectorAll('*'))) {
    for (const attribute of Array.from(element.attributes)) {
      expect(attribute.name.startsWith('on'), `${element.tagName} ${attribute.name}`).toBe(false);
      if (['href', 'src', 'action', 'formaction', 'xlink:href'].includes(attribute.name)) {
        expect(attribute.value.trim().toLowerCase()).not.toMatch(/^javascript:(?!throw new error\('react has blocked)/);
        expect(attribute.value.trim().toLowerCase().startsWith('data:text/html')).toBe(false);
      }
    }
  }
  expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
}

// --- XSS: данные сотрудника остаются текстом --------------------------------

describe('XSS: строки из данных не становятся разметкой', () => {
  const HR = {
    ...USER,
    permissions: ['questions.read', 'questions.answer', 'employees.read', 'knowledge.read'],
  };
  const payload = XSS.join(' ');
  const now = new Date().toISOString();
  const ROW = {
    id: 'q-1', number: 1,
    employee: { id: 'e-1', full_name: `Иванов ${XSS[0]}`, employee_number: 'T-1', has_photo: false },
    office: { id: 'o-1', name: `Офис ${XSS[2]}` },
    topic: `Тема ${XSS[1]}`, snippet: payload, last_message_kind: 'EMPLOYEE',
    category: 'OTHER', priority: 'NORMAL', status: 'NEW', unread: false,
    awaiting_reply: true, due_at: now, overdue: false, last_message_at: now,
    created_at: now, assignee: null,
  };
  const QUESTION = {
    ...ROW,
    question_text: payload, channel: 'TELEGRAM', first_response_at: null,
    closed_at: null, closed_by: null, close_reason: null,
    telegram: { connected: true, reason: null, status: 'ACTIVE' },
    messages: [{
      id: 'm-1', kind: 'EMPLOYEE', source: 'TELEGRAM', body: `Текст ${payload}`,
      event: null, details: null,
      author: { type: 'employee', id: 'e-1', name: `Иванов ${XSS[0]}` },
      created_at: now, delivery: null,
      attachment: { name: `справка${XSS[2]}.pdf`, size_bytes: 10, mime_type: 'application/pdf' },
    }],
    draft: null,
    actions: {
      take: true, assign: true, priority: true, category: true, wait: true,
      start: false, close: true, reopen: false, reply: true, draft: false,
    },
  };

  const CONTEXT = {
    employee: {
      id: 'e-1', full_name: `Иванов ${XSS[0]}`, employee_number: 'T-1',
      employment_status: 'ACTIVE', has_photo: false, position: XSS[1],
      department: XSS[2], office: { id: 'o-1', name: XSS[3] },
      schedule: { name: XSS[4], flexible: false, summary: XSS[5] },
      telegram: { connected: true, reason: null, status: 'ACTIVE', username: 'ivanov' },
    },
    links: { employee_card: true, attendance: false, requests: true },
    requests: [], balance: [], corrections: [], documents: [],
    history: { total: 1, closed: 0, open: 1, recent: [] },
    materials: [],
    today: null,
  };

  test('переписка обращения: имя, тема, текст и имя файла — только текст', async () => {
    fakeNetwork((path) => {
      if (path.includes('/auth/')) return json(200, HR);
      if (path.includes('/context/')) return json(200, CONTEXT);
      if (path.includes('/knowledge/escalations/counts')) {
        return json(200, {
          statuses: { NEW: 1, IN_PROGRESS: 0, WAITING_EMPLOYEE: 0, CLOSED: 0 }, total: 1,
          quick: { all: 1, unanswered: 1, mine: 0, urgent: 0, unread: 0 },
        });
      }
      if (path.includes('/knowledge/escalations/assignees')) {
        return json(200, { items: [{ id: 'u-2', name: `Кадровик ${XSS[0]}` }] });
      }
      if (/\/knowledge\/escalations\/q-1\/?$/.test(path)) return json(200, QUESTION);
      if (/\/knowledge\/escalations\/(\?|$)/.test(path)) {
        return json(200, { items: [ROW], next_cursor: null, has_more: false });
      }
      return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
    });
    renderApp('/questions?id=q-1');

    const talk = await screen.findByRole('region', { name: 'Переписка' });
    // Нагрузка видна как текст — значит, дошла до экрана, а не потерялась.
    await within(talk).findByText((_, node) =>
      node?.classList.contains('tk-msg__text') === true
      && (node.textContent ?? '').includes('<script>window.__pwned=1</script>'));
    expect(talk.textContent).toContain(`справка${XSS[2]}.pdf`);
    assertInert();
    // Ссылка на файл — адрес нашего API, а не что-то из данных.
    const file = talk.querySelector('a.tk-file');
    expect(file?.getAttribute('href')).toBe('/api/v1/knowledge/escalations/q-1/messages/m-1/file/');
    expect(file?.getAttribute('rel')).toContain('noreferrer');
  });

  test('карточка сотрудника: ФИО, должность и документы — только текст', async () => {
    const PERSON = {
      id: 'e-9', employee_number: 'HT-009',
      full_name: `${XSS[1]} Азизбек`, first_name: 'Азизбек', last_name: XSS[0],
      phone: XSS[1], corporate_email: XSS[3],
      employment_status: 'ACTIVE', employment_status_title: 'Работает',
      termination_reason: null, hire_date: '2026-09-01', termination_date: null,
      telegram_connected: true, photo: false,
      current_assignment: {
        id: 'a-9', office_id: 'o-1', office_name: XSS[2], region_id: 'r-1',
        region_name: 'Центр', department_id: 'd-1', department_name: XSS[4],
        position_id: 'p-1', position_name: XSS[5],
        employment_type: 'FULL_TIME', work_mode: 'ONSITE', is_primary: true,
        valid_from: '2026-09-01', valid_to: null,
      },
      current_schedule: null, documents: [], assignment_history: [],
    };
    fakeNetwork((path) => {
      if (path.includes('/auth/')) {
        return json(200, { ...USER, permissions: ['employees.view', 'employees.read'] });
      }
      if (path.includes('/employees/e-9/')) return json(200, PERSON);
      return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
    });
    renderApp('/employees/e-9');

    await screen.findByRole('heading', { name: `${XSS[1]} Азизбек` });
    assertInert();
  });

  test('карточка ленты уведомлений: заголовок, комментарий и автор — только текст', () => {
    const card = {
      id: 'absence_request:1', type: 'absence_request', group: 'requests',
      title: XSS[0], short_text: XSS[1], employee_id: 'e-1', employee_name: XSS[2],
      office_id: 'o-1', office_name: XSS[3], status: 'SUBMITTED', status_label: XSS[4],
      priority: 'NORMAL', requires_action: true, created_at: '2026-09-01T09:00:00Z',
      read_at: null, related_entity_type: 'absence_requests', related_entity_id: '1',
      action_url: 'javascript:window.__pwned=1', action_title: XSS[5],
      employee: { id: 'e-1', full_name: XSS[2], position_name: XSS[1], office_name: XSS[3] },
      author: { id: 'u-1', name: XSS[0] },
      comment: XSS.join('\n'),
      occurred_at: '2026-09-01T09:00:00Z',
    } as unknown as FeedDetail;
    const follow = vi.fn();
    render(<MemoryRouter><FeedCard card={card} onFollow={follow} /></MemoryRouter>);

    expect(screen.getByText(XSS[0], { selector: 'h3' })).toBeTruthy();
    assertInert();
  });
});

// --- Ссылки из данных --------------------------------------------------------

describe('ссылки из данных', () => {
  test('`javascript:` в адресе файла React 19 не пропускает в DOM', () => {
    // Защита от `javascript:` здесь — сам React: он подменяет такой
    // адрес на бросающий ошибку. Тест фиксирует это как опору, на
    // которой стоят `FileRow` и ссылка приглашения в Telegram.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(<FileRow tone="blue" title="Справка" note="1 КБ"
                    href={'javascript:window.__pwned=1'} download="x.pdf" action="Открыть" />);
    const link = document.querySelector('a.rq-file__get');
    expect(link?.getAttribute('href') ?? '').not.toContain('__pwned');
    expect(link?.getAttribute('rel')).toContain('noreferrer');
    spy.mockRestore();
    assertInert();
  });

  test('`<Link>` отдаёт внешний адрес как есть — поэтому `back` фильтруется', () => {
    // Воспроизведение: без фильтра `?back=https://evil.example` из
    // присланной ссылки становился кнопкой «Все сотрудники».
    render(<MemoryRouter><Link to="https://evil.example/login">назад</Link></MemoryRouter>);
    expect(screen.getByText('назад').getAttribute('href')).toBe('https://evil.example/login');
  });

  test.each([
    ['https://evil.example/', '/employees'],
    ['//evil.example/', '/employees'],
    ['/\\evil.example', '/employees'],
    ['/\t/evil.example', '/employees'],
    ['javascript:alert(1)', '/employees'],
    ['JaVaScRiPt:alert(1)', '/employees'],
    ['data:text/html,<script>alert(1)</script>', '/employees'],
    ['employees', '/employees'],
    ['', '/employees'],
    ['/employees?search=%D0%90&status=ACTIVE', '/employees?search=%D0%90&status=ACTIVE'],
  ])('internalPath(%j) → %j', (value, expected) => {
    expect(internalPath(value, '/employees')).toBe(expected);
  });

  test('карточка сотрудника: «Все сотрудники» не уводит на чужой сайт', async () => {
    const PERSON = {
      id: 'e-9', employee_number: 'HT-009', full_name: 'Мурадов Азизбек',
      first_name: 'Азизбек', last_name: 'Мурадов', phone: null, corporate_email: null,
      employment_status: 'ACTIVE', employment_status_title: 'Работает',
      termination_reason: null, hire_date: '2026-09-01', termination_date: null,
      telegram_connected: true, photo: false, current_assignment: null,
      current_schedule: null, documents: [], assignment_history: [],
    };
    fakeNetwork((path) => {
      if (path.includes('/auth/')) {
        return json(200, { ...USER, permissions: ['employees.view', 'employees.read'] });
      }
      if (path.includes('/employees/e-9/')) return json(200, PERSON);
      return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
    });
    renderApp('/employees/e-9?back=' + encodeURIComponent('https://evil.example/login'));

    await screen.findByRole('heading', { name: 'Мурадов Азизбек' });
    const back = screen.getByRole('link', { name: /Все сотрудники/ });
    expect(back.getAttribute('href')).toBe('/employees');
  });

  test('карточка сотрудника: свой адрес возврата сохраняется', async () => {
    fakeNetwork((path) => {
      if (path.includes('/auth/')) {
        return json(200, { ...USER, permissions: ['employees.view', 'employees.read'] });
      }
      if (path.includes('/employees/e-9/')) {
        return json(200, {
          id: 'e-9', full_name: 'Мурадов Азизбек', employment_status: 'ACTIVE',
          photo: false, current_assignment: null, documents: [], assignment_history: [],
        });
      }
      return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
    });
    renderApp('/employees/e-9?back=' + encodeURIComponent('/employees?search=abc'));

    await screen.findByRole('heading', { name: 'Мурадов Азизбек' });
    expect(screen.getByRole('link', { name: /Все сотрудники/ }).getAttribute('href'))
      .toBe('/employees?search=abc');
  });
});

// --- Подмена маршрута запроса через идентификатор из адреса ----------------

describe('путь запроса не выходит за собранный кодом маршрут', () => {
  test('браузер действительно схлопывает `..` и `%2e%2e` — это и было атакой', () => {
    const base = 'http://crm.test';
    expect(new URL('/api/v1/absence-requests/../employees/x/terminate?/approve', base).pathname)
      .toBe('/api/v1/employees/x/terminate');
    expect(new URL('/api/v1/absence-requests/%2e%2e/employees/x', base).pathname)
      .toBe('/api/v1/employees/x');
    expect(new URL('/api/v1/absence-requests/..\\employees/x', base).pathname)
      .toBe('/api/v1/employees/x');
  });

  test.each([
    '/absence-requests/../employees/x/terminate?/approve',
    '/absence-requests/%2e%2e/employees/x',
    '/absence-requests/%2E./employees/x',
    '/absence-requests/./x',
    '/absence-requests/..',
    '/absence-requests/..\\employees/x',
  ])('отказ до сети: %s', async (path) => {
    const calls = fakeNetwork(() => json(200, {}));
    expect(isSafePath(path)).toBe(false);
    await expect(request(path, { method: 'POST', body: {} })).rejects.toBeInstanceOf(ApiFailure);
    await expect(upload(path, new FormData())).rejects.toBeInstanceOf(ApiFailure);
    expect(calls).toHaveLength(0);
  });

  test.each([
    '/absence-requests/8f14e45f-ceea-467a-9e2f-000000000001/approve',
    '/employees/?search=..&status=ACTIVE',
    '/knowledge/escalations/q-1/messages/m-1/file/',
    '/files/report..pdf',
  ])('обычный путь проходит: %s', (path) => {
    expect(isSafePath(path)).toBe(true);
  });

  test('ссылка `/requests/..%2F…` не отправляет запросов мимо заявок', async () => {
    const calls = fakeNetwork((path) => {
      if (path.includes('/auth/')) {
        return json(200, { ...USER, permissions: ['absences.read', 'absences.approve'] });
      }
      return crm(path) ?? json(200, { items: [] });
    });
    renderApp('/requests/..%2Femployees%2Fe-1%2Fterminate%3F');
    await waitFor(() => expect(calls.length).toBeGreaterThan(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    for (const call of calls) {
      const pathname = new URL(call.url, 'http://crm.test').pathname;
      expect(pathname, call.url).not.toMatch(/\/employees\/e-1\/terminate/);
      expect(call.url).not.toMatch(/\/\.\.(\/|$)|%2e%2e/i);
    }
  });
});

// --- Хранение и CSRF --------------------------------------------------------

describe('хранение на клиенте и CSRF', () => {
  test('CSRF-токен берётся из cookie и уходит только в изменяющие запросы', async () => {
    document.cookie = 'csrftoken=test-csrf-value; path=/';
    const calls = fakeNetwork(() => json(200, {}));
    await request('/x/');
    await request('/x/', { method: 'POST', body: {} });
    await upload('/y/', new FormData());
    expect(calls[0]?.headers['X-CSRFToken']).toBeUndefined();
    expect(calls[1]?.headers['X-CSRFToken']).toBe('test-csrf-value');
    expect(calls[2]?.headers['X-CSRFToken']).toBe('test-csrf-value');
    document.cookie = 'csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
  });

  test('csrfToken не путает похожие имена cookie', () => {
    expect(csrfToken('xcsrftoken=bad; csrftoken=good')).toBe('good');
    expect(csrfToken('csrftoken_old=bad')).toBeNull();
  });

  test('вход не кладёт в localStorage ни пароль, ни ответ сервера', async () => {
    fakeNetwork((path) => {
      if (path.endsWith('/auth/me')) {
        return json(403, { error: { code: 'not_authenticated', message: '…', details: null } });
      }
      if (path.endsWith('/auth/login')) return json(200, USER);
      return crm(path) ?? json(200, { items: [] });
    });
    renderApp('/login');
    await screen.findByRole('heading', { name: 'Добро пожаловать' });
    await userEvent.type(screen.getByLabelText('Логин'), 'hr@humotech.local');
    await userEvent.type(screen.getByLabelText('Пароль'), 'Secret-Test-Pass-1');
    await userEvent.click(screen.getByRole('button', { name: 'Войти' }));
    await screen.findByText('Обзор на сегодня');

    const stored = Object.keys(localStorage).map((key) => `${key}=${localStorage.getItem(key)}`).join('\n')
      + Object.keys(sessionStorage).map((key) => `${key}=${sessionStorage.getItem(key)}`).join('\n');
    expect(stored).not.toContain('Secret-Test-Pass-1');
    expect(stored).not.toContain(USER.organization_id);
    expect(stored).not.toMatch(/sessionid|csrftoken|permissions/);
    for (const key of Object.keys(localStorage)) expect(key).toBe('humotech.crm.login');
  });

  test('выход сбрасывает счётчики меню, и запоздалый ответ их не возвращает', async () => {
    const pending: Array<() => void> = [];
    let slow = false;
    fakeNetwork(async (path) => {
      if (slow) await new Promise<void>((resolve) => { pending.push(resolve); });
      if (path.includes('/requests/counts')) return json(200, { open: 7 });
      return json(200, { quick: { unanswered: 3 } });
    });

    const seen: Array<Record<string, number>> = [];
    function Probe() {
      const badges = useBadges();
      useEffect(() => { seen.push(badges); }, [badges]);
      return <output>{JSON.stringify(badges)}</output>;
    }
    const { unmount } = render(<Probe />);
    await screen.findByText('{"requests":7,"questions":3}');

    // Выход: App вызывает forgetNavigation, когда сессия стала anonymous.
    forgetNavigation();
    await screen.findByText('{}');
    unmount();

    // Запрос, начатый прежним человеком, заканчивается уже после выхода.
    slow = true;
    render(<Probe />);
    await waitFor(() => expect(pending).toHaveLength(2));
    forgetNavigation();
    for (const resolve of pending) resolve();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.getByText('{}')).toBeTruthy();
    expect(seen.at(-1)).toEqual({});
  });
});

// --- Статический сторож ----------------------------------------------------

describe('исходники: опасных приёмов нет', () => {
  const root = join(__dirname, '..', 'src');
  function files(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) return files(full);
      return /\.(tsx?|jsx?)$/.test(name) ? [full] : [];
    });
  }
  const sources = files(root).map((file) => ({
    file: file.slice(root.length + 1),
    text: readFileSync(file, 'utf8'),
  }));

  test('нет вывода сырого HTML и исполнения строк', () => {
    const banned = /dangerouslySetInnerHTML|\.innerHTML\s*=|\.outerHTML\s*=|insertAdjacentHTML|document\.write|\beval\s*\(|new Function\s*\(|srcdoc|bindPopup|bindTooltip|setContent\(/;
    const hits = sources.filter(({ text }) => banned.test(text)).map(({ file }) => file);
    expect(hits).toEqual([]);
  });

  test('каждая ссылка в новую вкладку — с rel="noreferrer" или "noopener"', () => {
    const bad: string[] = [];
    for (const { file, text } of sources) {
      const tags = text.match(/<a\b[^>]*target="_blank"[^>]*>/gs) ?? [];
      for (const tag of tags) {
        if (!/rel="[^"]*(noreferrer|noopener)/.test(tag)) bad.push(`${file}: ${tag}`);
      }
      if (/window\.open\s*\(/.test(text) && !/noopener|noreferrer/.test(text)) bad.push(`${file}: window.open`);
    }
    expect(bad).toEqual([]);
  });

  test('токенов и пароля в localStorage/sessionStorage нет', () => {
    const hits = sources
      .filter(({ text }) => /(localStorage|sessionStorage)\.setItem\([^)]*(token|password|session|csrf)/i.test(text))
      .map(({ file }) => file);
    expect(hits).toEqual([]);
  });

  test('прод-сборка без карт исходников, прокси только у dev-сервера', () => {
    const config = readFileSync(join(__dirname, '..', 'vite.config.ts'), 'utf8');
    expect(config).toMatch(/build:\s*{\s*sourcemap:\s*false/);
    expect(config).not.toMatch(/sourcemap:\s*true/);
    expect(config).not.toMatch(/preview:\s*{[^}]*proxy/s);
  });
});

// --- Передача от зоны auth: 429, CSRF перед входом, журнал -----------------

const dropCsrf = () => {
  document.cookie = 'csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
};
const CSRF_REFUSED = json(403, {
  error: { code: 'permission_denied', message: 'CSRF Failed: CSRF token missing.', details: null },
});
const ANONYMOUS = json(403, { error: { code: 'not_authenticated', message: '…', details: null } });

async function typeAndSubmit() {
  await screen.findByRole('heading', { name: 'Добро пожаловать' });
  await userEvent.type(screen.getByLabelText('Логин'), 'hr@humotech.local');
  await userEvent.type(screen.getByLabelText('Пароль'), 'Test-Pass-1');
  await userEvent.click(screen.getByRole('button', { name: 'Войти' }));
}

describe('вход: ограничение попыток и CSRF', () => {
  test('429 too_many_attempts — «слишком много попыток» со сроком, а не «сервер недоступен»', async () => {
    dropCsrf();
    document.cookie = 'csrftoken=t1; path=/';
    fakeNetwork((path) => {
      if (path.endsWith('/auth/me')) return ANONYMOUS.clone();
      if (path.endsWith('/auth/login')) {
        return new Response(JSON.stringify({ error: {
          code: 'too_many_attempts',
          message: 'Слишком много неудачных попыток входа. Повторите через 5 мин.',
          details: { retry_after: 300 },
        } }), { status: 429, headers: { 'Content-Type': 'application/json', 'Retry-After': '300' } });
      }
      return crm(path) ?? json(200, { items: [] });
    });
    renderApp('/login');
    await typeAndSubmit();

    expect(await screen.findByText('Слишком много попыток. Попробуйте через 5 минут')).toBeTruthy();
    expect(screen.queryByText(/Сервер временно недоступен/)).toBeNull();
    dropCsrf();
  });

  test.each([
    [30, 'через 1 минуту'], [60, 'через 1 минуту'], [61, 'через 2 минуты'],
    [300, 'через 5 минут'], [660, 'через 11 минут'], [1260, 'через 21 минуту'],
  ])('срок %i с → «%s»', (seconds, words) => {
    const failure = new ApiFailure('throttled', 429, {}, null, 'too_many_attempts', null,
      { retry_after: seconds });
    expect(messageFor(failure)).toBe(`Слишком много попыток. Попробуйте ${words}`);
  });

  test('429 без срока — общий текст про попытки', () => {
    expect(messageFor(new ApiFailure('throttled', 429))).toBe('Слишком много попыток. Попробуйте позже');
  });

  test('429 на /auth/me не выдаёт человека за вышедшего', async () => {
    fakeNetwork(() => new Response(JSON.stringify({ error: { code: 'throttled', message: '…' } }),
      { status: 429, headers: { 'Content-Type': 'application/json', 'Retry-After': '10' } }));
    renderApp('/');
    expect(await screen.findByText(/Сервер не отвечает/)).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Добро пожаловать' })).toBeNull();
  });

  test('чистая вкладка без cookie: токен запрашивается до входа, вход проходит', async () => {
    dropCsrf();
    let meCalls = 0;
    const calls = fakeNetwork((path, call) => {
      if (path.endsWith('/auth/me')) {
        meCalls += 1;
        // Первый ответ при загрузке cookie не донёс (стёрли, истекла);
        // cookie ставит уже второй — тот, что вход запросил сам.
        if (meCalls >= 2) document.cookie = 'csrftoken=fresh-token; path=/';
        return ANONYMOUS.clone();
      }
      if (path.endsWith('/auth/login')) {
        return call.headers['X-CSRFToken'] === 'fresh-token' ? json(200, USER) : CSRF_REFUSED.clone();
      }
      return crm(path) ?? json(200, { items: [] });
    });
    renderApp('/login');
    await typeAndSubmit();

    await screen.findByText('Обзор на сегодня');
    const logins = calls.filter((call) => call.url.endsWith('/auth/login'));
    expect(logins).toHaveLength(1);
    expect(logins[0]?.headers['X-CSRFToken']).toBe('fresh-token');
    const order = calls.map((call) => call.url.replace(/^.*\/auth\//, ''));
    expect(order.indexOf('me', 1)).toBeLessThan(order.indexOf('login'));
    dropCsrf();
  });

  test('устаревший токен: один повтор со свежим, пароль вводить заново не нужно', async () => {
    dropCsrf();
    document.cookie = 'csrftoken=stale; path=/';
    const calls = fakeNetwork((path, call) => {
      if (path.endsWith('/auth/me')) {
        if (calls.some((one) => one.url.endsWith('/auth/login'))) {
          document.cookie = 'csrftoken=renewed; path=/';
        }
        return ANONYMOUS.clone();
      }
      if (path.endsWith('/auth/login')) {
        return call.headers['X-CSRFToken'] === 'renewed' ? json(200, USER) : CSRF_REFUSED.clone();
      }
      return crm(path) ?? json(200, { items: [] });
    });
    renderApp('/login');
    await typeAndSubmit();

    await screen.findByText('Обзор на сегодня');
    const logins = calls.filter((call) => call.url.endsWith('/auth/login'));
    expect(logins.map((one) => one.headers['X-CSRFToken'])).toEqual(['stale', 'renewed']);
    dropCsrf();
  });

  test('CSRF-отказ повторяется не больше одного раза', async () => {
    dropCsrf();
    document.cookie = 'csrftoken=bad; path=/';
    const calls = fakeNetwork((path) => {
      if (path.endsWith('/auth/me')) return ANONYMOUS.clone();
      if (path.endsWith('/auth/login')) return CSRF_REFUSED.clone();
      return crm(path) ?? json(200, { items: [] });
    });
    renderApp('/login');
    await typeAndSubmit();

    expect(await screen.findByText('Сессия устарела. Обновите страницу и повторите')).toBeTruthy();
    expect(calls.filter((call) => call.url.endsWith('/auth/login'))).toHaveLength(2);
    dropCsrf();
  });
});

describe('журнал: подписи событий входа', () => {
  test.each([
    ['auth.login', 'Вход в систему'],
    ['auth.login_failed', 'Неудачная попытка входа'],
    ['auth.login_locked', 'Вход временно заблокирован'],
    ['auth.logout', 'Выход из системы'],
  ])('%s → «%s»', (action, title) => {
    expect(actionTitle(action)).toBe(title);
  });
});

// --- Передача от зоны surveys ----------------------------------------------

describe('CSV ознакомлений: формулы остаются текстом', () => {
  test.each([
    ['=HYPERLINK("http://evil.example","жми")', `"'=HYPERLINK(""http://evil.example"",""жми"")"`],
    ['+7 900 000', "'+7 900 000"],
    ['-2+3', "'-2+3"],
    ['@SUM(A1)', "'@SUM(A1)"],
    ['\t=1', "'\t=1"],
    ['\r=1', `"'\r=1"`],
    ['Иванов', 'Иванов'],
  ])('ячейка %j → %j', (value, cell) => {
    const csv = toCsv([{ full_name: value }]);
    const line = csv.split('\n')[1] ?? '';
    // Сотрудник — вторая колонка после табельного номера.
    expect(line.split(';')[1]).toBe(cell);
  });

  test('числа не трогаются: -3 остаётся числом', () => {
    const line = toCsv([{ sections: -3 }]).split('\n')[1] ?? '';
    expect(line).not.toContain("'");
  });
});

describe('опросы: анонимность и область видимости', () => {
  test('скрытая сводка объяснена, а не выдана за «ещё не ответили»', () => {
    expect(suppressedText(3)).toBe('Ответов пока меньше трёх — итоги скрыты для анонимности.');
    expect(suppressedText(null)).toBe('Ответов пока меньше трёх — итоги скрыты для анонимности.');
  });

  test('403 scope_limited — понятный текст, а не «сессия истекла»', () => {
    const failure = new ApiFailure('session', 403, {}, null, 'permission_denied',
      'Рассылки опросов доступны только с доступом ко всей организации', { reason: 'scope_limited' });
    expect(messageFor(failure)).toBe(SCOPE_LIMITED);
    expect(reasonFor(failure)).toBe(SCOPE_LIMITED);
    expect(refusalText(failure)).toBe(SCOPE_LIMITED);
    expect(SCOPE_LIMITED).toMatch(/всей организации/);
  });

  test('scope_limited от сервера доходит до мастера рассылки', async () => {
    const failure = await (async () => {
      fakeNetwork(() => json(403, { error: {
        code: 'permission_denied',
        message: 'Рассылки опросов доступны только с доступом ко всей организации',
        details: { reason: 'scope_limited' },
      } }));
      try {
        await request('/surveys/campaigns/', { method: 'POST', body: {} });
      } catch (error) {
        return error;
      }
      return null;
    })();
    expect(refusalText(failure)).toBe(SCOPE_LIMITED);
  });
});
