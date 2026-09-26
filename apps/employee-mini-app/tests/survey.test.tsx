// @vitest-environment jsdom
/**
 * Опрос в Mini App: один вопрос на экране.
 *
 * Проверяется то, что легко сделать почти правильно:
 *
 * — «3 из 6», посчитанное не по тем шагам;
 * — обязательный вопрос, который пропускают и получают отказ в конце;
 * — ответы, ушедшие по одному вместо одного запроса;
 * — пустой ответ, отправленный как ответ;
 * — «Спасибо», показанное до ответа сервера;
 * — повторная отправка от второго нажатия;
 * — именной опрос, о котором сказали после ответов, а не до.
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Survey, describe as describeAnswer, hasAnswer, plural } from
  '../src/screens/Survey';
import { api } from '../src/api';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
});

const QUESTIONS = [
  {
    id: 'q1', position: 1, kind: 'SCALE' as const, is_required: true,
    text: 'Насколько вам комфортно в команде?', options: null,
  },
  {
    id: 'q2', position: 2, kind: 'MULTI' as const, is_required: true,
    text: 'Что помогает в работе?',
    options: ['Коллеги', 'График', 'Задачи'],
  },
  {
    id: 'q3', position: 3, kind: 'TEXT' as const, is_required: false,
    text: 'Что бы вы изменили?', options: null,
  },
];

/** Опрос из ответа сервера. `over` подменяет отдельные поля. */
function survey(over: Record<string, unknown> = {}) {
  return {
    id: 'r-1',
    title: 'Пульс-опрос',
    description: 'Короткий опрос о работе',
    status: 'STARTED',
    questions: QUESTIONS,
    answers: [],
    ...over,
  };
}

function mount(overrides: Record<string, unknown> = {}) {
  vi.spyOn(api, 'survey').mockResolvedValue({
    ok: true,
    value: survey(overrides) as never,
  });
  const done = vi.fn();
  const exit = vi.fn();
  render(<Survey recipientId="r-1" onDone={done} onExit={exit} />);
  return { done, exit };
}

/** Пройти вступление и дойти до первого вопроса. */
async function start() {
  fireEvent.click(await screen.findByRole('button', { name: 'Начать' }));
}

describe('вступление', () => {
  it('говорит, что опрос именной, ДО первого ответа', async () => {
    mount();
    // Человек, отвечающий про руководителя, должен знать это заранее.
    expect(await screen.findByText(/не анонимный/i)).toBeTruthy();
    expect(screen.getByText(/увидит ваше имя/i)).toBeTruthy();
  });

  it('называет длину опроса честно', async () => {
    mount();
    expect(await screen.findByText(/3 вопроса/)).toBeTruthy();
  });
});

describe('ход опроса', () => {
  it('показывает по одному вопросу и считает шаги от единицы', async () => {
    mount();
    await start();

    expect(await screen.findByText('1 из 3')).toBeTruthy();
    expect(screen.getByText(QUESTIONS[0]!.text)).toBeTruthy();
    // Второго вопроса на экране нет: это не стена текста.
    expect(screen.queryByText(QUESTIONS[1]!.text)).toBeNull();
  });

  it('обязательный вопрос не пропускается', async () => {
    mount();
    await start();

    const next = await screen.findByRole('button', { name: 'Далее' });
    expect((next as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Ответьте, чтобы идти дальше/)).toBeTruthy();

    fireEvent.click(screen.getByRole('radio', { name: '4' }));
    await waitFor(() => {
      expect(
        (screen.getByRole('button', { name: 'Далее' }) as HTMLButtonElement)
          .disabled,
      ).toBe(false);
    });
  });

  it('необязательный вопрос пропустить можно', async () => {
    mount();
    await start();
    fireEvent.click(screen.getByRole('radio', { name: '4' }));
    fireEvent.click(screen.getByRole('button', { name: 'Далее' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: /Коллеги/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Далее' }));

    // Третий вопрос необязательный: кнопка доступна без ответа.
    expect(await screen.findByText('3 из 3')).toBeTruthy();
    const next = screen.getByRole('button', { name: 'К отправке' });
    expect((next as HTMLButtonElement).disabled).toBe(false);
  });

  it('«Назад» возвращает прежний ответ, а не пустой экран', async () => {
    mount();
    await start();
    fireEvent.click(screen.getByRole('radio', { name: '5' }));
    fireEvent.click(screen.getByRole('button', { name: 'Далее' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Назад' }));

    const five = await screen.findByRole('radio', { name: '5' });
    expect(five.getAttribute('aria-checked')).toBe('true');
  });

  it('в вопросе с одним вариантом выбор заменяется, а не копится',
    async () => {
      vi.spyOn(api, 'survey').mockResolvedValue({
        ok: true,
        value: survey({
          questions: [{
            id: 'q1', position: 1, kind: 'SINGLE', is_required: true,
            text: 'Где удобнее?', options: ['Офис', 'Дома'],
          }],
        }) as never,
      });
      render(<Survey recipientId="r-1" onDone={vi.fn()} onExit={vi.fn()} />);
      await start();

      fireEvent.click(await screen.findByRole('radio', { name: /Офис/ }));
      fireEvent.click(screen.getByRole('radio', { name: /Дома/ }));

      expect(
        screen.getByRole('radio', { name: /Офис/ }).getAttribute('aria-checked'),
      ).toBe('false');
      expect(
        screen.getByRole('radio', { name: /Дома/ }).getAttribute('aria-checked'),
      ).toBe('true');
    });

  it('начатый опрос открывается с прежними ответами', async () => {
    mount({ answers: [{ question_id: 'q1', number: 2 }] });
    await start();

    const two = await screen.findByRole('radio', { name: '2' });
    expect(two.getAttribute('aria-checked')).toBe('true');
  });
});

describe('отправка', () => {
  async function fill() {
    mount();
    await start();
    fireEvent.click(screen.getByRole('radio', { name: '4' }));
    fireEvent.click(screen.getByRole('button', { name: 'Далее' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: /График/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Далее' }));
    fireEvent.click(await screen.findByRole('button', { name: 'К отправке' }));
  }

  it('ответы уходят ОДНИМ запросом', async () => {
    const send = vi.spyOn(api, 'submitSurvey').mockResolvedValue({
      ok: true,
      value: { id: 'r-1', status: 'COMPLETED', completed_at: '2026-09-18' },
    });
    await fill();

    fireEvent.click(await screen.findByRole('button', { name: 'Отправить ответы' }));

    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const [id, answers] = send.mock.calls[0]!;
    expect(id).toBe('r-1');
    expect(answers).toEqual([
      { question_id: 'q1', number: 4 },
      { question_id: 'q2', options: ['График'] },
    ]);
  });

  it('пустой ответ не отправляется как ответ', async () => {
    const send = vi.spyOn(api, 'submitSurvey').mockResolvedValue({
      ok: true,
      value: { id: 'r-1', status: 'COMPLETED', completed_at: null },
    });
    await fill();
    // Третий вопрос остался нетронутым — его в запросе быть не должно.
    fireEvent.click(await screen.findByRole('button', { name: 'Отправить ответы' }));

    await waitFor(() => expect(send).toHaveBeenCalled());
    const answers = send.mock.calls[0]![1] as Array<{ question_id: string }>;
    expect(answers.map((one) => one.question_id)).not.toContain('q3');
  });

  it('«Спасибо» появляется только после ответа сервера', async () => {
    let release: ((value: unknown) => void) | null = null;
    const held = new Promise((resolve) => { release = resolve; });
    vi.spyOn(api, 'submitSurvey').mockReturnValue(held as never);
    await fill();

    fireEvent.click(await screen.findByRole('button', { name: 'Отправить ответы' }));
    expect(screen.queryByText(/Спасибо, опрос завершён/)).toBeNull();

    release!({ ok: true, value: { id: 'r-1', status: 'COMPLETED', completed_at: null } });
    expect(await screen.findByText(/Спасибо, опрос завершён/)).toBeTruthy();
  });

  it('второе нажатие не отправляет второй раз', async () => {
    let release: ((value: unknown) => void) | null = null;
    const held = new Promise((resolve) => { release = resolve; });
    const send = vi.spyOn(api, 'submitSurvey').mockReturnValue(held as never);
    await fill();

    const button = await screen.findByRole('button', { name: 'Отправить ответы' });
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);

    expect(send).toHaveBeenCalledTimes(1);
    release!({ ok: true, value: { id: 'r-1', status: 'COMPLETED', completed_at: null } });
  });

  it('отказ сервера показывается его словами и экран остаётся', async () => {
    vi.spyOn(api, 'submitSurvey').mockResolvedValue({
      ok: false,
      kind: 'refused',
      message: 'Этот опрос уже пройден',
    });
    await fill();

    fireEvent.click(await screen.findByRole('button', { name: 'Отправить ответы' }));

    expect(await screen.findByText('Этот опрос уже пройден')).toBeTruthy();
    expect(screen.queryByText(/Спасибо, опрос завершён/)).toBeNull();
  });
});

describe('вспомогательное', () => {
  it('пробелы ответом не считаются', () => {
    expect(hasAnswer({ question_id: 'q', text: '   ' })).toBe(false);
    expect(hasAnswer({ question_id: 'q', text: 'да' })).toBe(true);
    expect(hasAnswer({ question_id: 'q', options: [] })).toBe(false);
    expect(hasAnswer({ question_id: 'q', number: 1 })).toBe(true);
    // Ноль — не «пусто»: шкала начинается с единицы, но правило общее.
    expect(hasAnswer({ question_id: 'q', number: 0 })).toBe(true);
  });

  it('оценка читается как оценка, а не как число', () => {
    expect(describeAnswer({ question_id: 'q', number: 4 })).toBe('4 из 5');
    expect(describeAnswer({ question_id: 'q', options: ['А', 'Б'] })).toBe('А, Б');
    expect(describeAnswer(undefined)).toBeNull();
  });

  it('счёт по-русски не сводится к «одному или больше»', () => {
    const forms: [string, string, string] = ['вопрос', 'вопроса', 'вопросов'];
    expect(plural(1, forms)).toBe('вопрос');
    expect(plural(3, forms)).toBe('вопроса');
    expect(plural(5, forms)).toBe('вопросов');
    expect(plural(11, forms)).toBe('вопросов');
    expect(plural(21, forms)).toBe('вопрос');
  });
});
