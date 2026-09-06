/**
 * Обращения.
 *
 * Главное, что проверяется: показывается ровно та переписка, которую
 * хранит backend (вопрос и один ответ), системное событие отделено от
 * сообщения, а подтверждение говорит про очередь, а не про доставку.
 */

import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, test } from 'vitest';

import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

const WAITING = {
  id: 'q-1',
  employee: { id: 'e-1', full_name: 'Каримов Алишер', employee_number: 'HT-001' },
  question_text: 'Хочу перенести отпуск с 14–25 сентября на 21 сентября — 2 октября.',
  normalized_topic: 'Как перенести отпуск?',
  status: 'ESCALATED_TO_HR',
  ai_answer_text: null,
  hr_answer_text: null,
  assigned_to_user_id: null,
  answered_at: null,
  created_at: '2026-09-06T11:24:00Z',
  updated_at: '2026-09-06T11:24:00Z',
};

const ANSWERED = {
  ...WAITING,
  id: 'q-2',
  status: 'HR_ANSWERED',
  hr_answer_text: 'Заявку принял, перенесу даты.',
  assigned_to_user_id: USER.id,
  answered_at: '2026-09-06T11:42:00Z',
  updated_at: '2026-09-06T11:42:00Z',
};

function network(handler: (path: string, method: string) => Response | null = () => null) {
  return fakeNetwork((path, call) => {
    const own = handler(path, call.method);
    if (own) return own;
    if (path.includes('/auth/')) {
      return json(200, { ...USER, permissions: ['questions.read', 'questions.answer'] });
    }
    if (path.includes('/escalations/counts')) {
      return json(200, { total: 2, ESCALATED_TO_HR: 1, HR_ANSWERED: 1 });
    }
    if (path.includes('/escalations/')) {
      return json(200, { items: [WAITING, ANSWERED], next_cursor: null, has_more: false });
    }
    if (path.includes('/employees/')) {
      return json(200, {
        id: 'e-1', full_name: 'Каримов Алишер', employee_number: 'HT-001',
        current_assignment: { position_name: 'Специалист поддержки',
                              office_name: 'Ташкент', department_name: 'Операционный отдел' },
      });
    }
    return crm(path) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

describe('очередь обращений', () => {
  test('счётчики вкладок не зависят от выбранной вкладки', async () => {
    const calls = network();
    renderApp('/questions');
    await screen.findAllByText('Каримов Алишер');

    fireEvent.click(screen.getByRole('tab', { name: /Закрытые/ }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('status=HR_ANSWERED'))).toBe(true),
    );
    const counts = calls.filter((c) => c.url.includes('/escalations/counts'));
    expect(counts.every((c) => !c.url.includes('status='))).toBe(true);
  });

  test('поиск уходит на сервер', async () => {
    const calls = network();
    renderApp('/questions');
    await screen.findAllByText('Каримов Алишер');

    fireEvent.change(screen.getByLabelText('Поиск обращений'), {
      target: { value: 'отпуск' },
    });

    await waitFor(
      () => expect(calls.some((c) => c.url.includes('search='))).toBe(true),
      { timeout: 2000 },
    );
  });

  test('ошибка не превращается в «обращений нет»', async () => {
    network((path) =>
      path.includes('/escalations/') && !path.includes('counts')
        ? json(500, { error: {} })
        : null,
    );
    renderApp('/questions');

    expect(await screen.findByText(/Не удалось загрузить обращения/)).toBeTruthy();
  });
});

describe('переписка', () => {
  test('открывается по прямой ссылке и показывает вопрос', async () => {
    network();
    renderApp('/questions?id=q-1');

    // Текст виден и в выдержке списка, и в самом сообщении.
    expect((await screen.findAllByText(/Хочу перенести отпуск/)).length).toBeGreaterThan(1);
  });

  test('ответ HR и системное событие показаны отдельно', async () => {
    // Системное событие — не сообщение: у него нет автора и текста от людей.
    network();
    renderApp('/questions?id=q-2');

    expect(await screen.findByText('Заявку принял, перенесу даты.')).toBeTruthy();
    expect(screen.getByText('Вы взяли обращение в работу')).toBeTruthy();
    expect(screen.getByText('Ответ HR')).toBeTruthy();
  });

  test('на отвеченное обращение второй ответ не предлагается', async () => {
    network();
    renderApp('/questions?id=q-2');

    expect(await screen.findByText(/Ответ уже дан/)).toBeTruthy();
    expect(screen.queryByLabelText('Ответ сотруднику')).toBeNull();
  });

  test('без права отвечать поля ответа нет', async () => {
    network((path) =>
      path.includes('/auth/')
        ? json(200, { ...USER, permissions: ['questions.read'] })
        : null,
    );
    renderApp('/questions?id=q-1');

    expect(await screen.findByText(/Нет права отвечать/)).toBeTruthy();
  });
});

describe('ответ', () => {
  test('подтверждение говорит про очередь, а не про доставку', async () => {
    // Сервис кладёт уведомление в outbox; отправляет его бот. Сказать
    // «отправлено» до этого значит подтвердить то, чего не случилось.
    network((path, method) =>
      method === 'POST' && path.includes('/answer/') ? json(200, ANSWERED) : null,
    );
    renderApp('/questions?id=q-1');

    const area = await screen.findByLabelText('Ответ сотруднику');
    fireEvent.change(area, { target: { value: 'Проверю и напишу здесь.' } });
    fireEvent.click(screen.getByRole('button', { name: /Отправить/ }));

    expect(await screen.findByText(/поставлен в очередь/)).toBeTruthy();
    expect(screen.queryByText(/^Отправлено$/)).toBeNull();
  });

  test('при ошибке текст остаётся в поле', async () => {
    network((path, method) =>
      method === 'POST' && path.includes('/answer/')
        ? json(409, { error: { code: 'conflict', message: 'уже ответили' } })
        : null,
    );
    renderApp('/questions?id=q-1');

    const area = await screen.findByLabelText('Ответ сотруднику');
    fireEvent.change(area, { target: { value: 'Черновик ответа' } });
    fireEvent.click(screen.getByRole('button', { name: /Отправить/ }));

    await screen.findByRole('alert');
    expect((area as HTMLTextAreaElement).value).toBe('Черновик ответа');
  });

  test('повторное нажатие не отправляет второй запрос', async () => {
    const calls = network((path, method) =>
      method === 'POST' && path.includes('/answer/') ? json(200, ANSWERED) : null,
    );
    renderApp('/questions?id=q-1');

    const area = await screen.findByLabelText('Ответ сотруднику');
    fireEvent.change(area, { target: { value: 'Ответ' } });
    const button = screen.getByRole('button', { name: /Отправить/ });
    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() =>
      expect(calls.filter((c) => c.url.includes('/answer/'))).toHaveLength(1),
    );
  });
});

describe('контекст', () => {
  test('связанных заявок не выдумывает', async () => {
    // Связи обращения с заявкой backend не хранит.
    network();
    renderApp('/questions?id=q-1');

    expect(await screen.findByText(/Связей с заявками у обращения нет/)).toBeTruthy();
    expect(await screen.findByText('Специалист поддержки')).toBeTruthy();
  });
});
