/**
 * База знаний.
 *
 * Проверяется то, что легче всего изобразить и труднее всего заметить
 * глазами:
 *
 * — текст документа пишет человек, и разметка не должна давать ему
 *   исполнить скрипт на странице кадровика;
 * — строка списка — это ДОКУМЕНТ, а не версия, и счётчик вкладки
 *   считает по тому же правилу;
 * — число на вкладке равно числу строк, которые по ней покажутся;
 * — черновик нигде не называется опубликованным, а недоступное
 *   действие гаснет заранее и с причиной, а не отправляет обречённый
 *   запрос;
 * — переименование не уводит новую версию в отдельный документ.
 *
 * Материалы здесь выдуманные. Настоящие правила компании и тексты
 * сотрудников в тестовые данные не попадают.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';

import { Markup, excerpt, inline, parse, safeHref } from '../src/features/knowledge/markup';
import {
  FAQ_TABS, SOURCE_TABS, canArchive, canEditInPlace, faqActivateBlockedBecause,
  faqTabCount, indexBlockedBecause, readinessNote, scopeTitle, sourceTabCount,
  type SourceTab,
} from '../src/features/knowledge/model';
import { shownLine } from '../src/pages/KnowledgePage';
import { USER, crm, fakeNetwork, json, renderApp } from './helpers';

// --- разметка: безопасность -------------------------------------------------

describe('разметка документа не исполняет чужой код', () => {
  test('тег script остаётся текстом, а не элементом', () => {
    const { container } = render(
      <Markup content={'Правило первое.\n<script>window.__hacked = 1</script>'} />,
    );
    expect(container.querySelector('script')).toBeNull();
    expect(container.textContent).toContain('<script>window.__hacked = 1</script>');
    expect((window as unknown as Record<string, unknown>)['__hacked']).toBeUndefined();
  });

  test('обработчик события в теге не превращается в тег', () => {
    const { container } = render(
      <Markup content={'<img src=x onerror="window.__hacked = 1">'} />,
    );
    expect(container.querySelector('img')).toBeNull();
    // Угловые скобки экранированы: `onerror` остался частью ТЕКСТА,
    // а не стал атрибутом тега.
    expect(container.innerHTML).toContain('&lt;img src=x onerror=');
    expect(container.innerHTML).not.toContain('<img');
    expect(container.textContent).toContain('onerror');
  });

  test('ссылка на javascript: не становится ссылкой', () => {
    const { container } = render(
      <Markup content={'Смотрите [здесь](javascript:alert(1)) подробности.'} />,
    );
    expect(container.querySelector('a')).toBeNull();
    // Ничего не прячем: непропущенный адрес виден как был.
    expect(container.textContent).toContain('[здесь](javascript:alert(1))');
  });

  test('обычная ссылка работает и открывается безопасно', () => {
    const { container } = render(
      <Markup content={'Форма лежит [на портале](https://example.test/form).'} />,
    );
    const link = container.querySelector('a');
    expect(link?.getAttribute('href')).toBe('https://example.test/form');
    expect(link?.getAttribute('rel')).toContain('noopener');
  });

  test('пропускается только http и https, регистр и пробелы не помогают', () => {
    expect(safeHref('https://example.test')).toBe(true);
    expect(safeHref('HTTP://example.test')).toBe(true);
    expect(safeHref('  https://example.test')).toBe(true);
    expect(safeHref('javascript:alert(1)')).toBe(false);
    expect(safeHref(' JavaScript:alert(1)')).toBe(false);
    expect(safeHref('data:text/html;base64,PHNjcmlwdD4=')).toBe(false);
    expect(safeHref('vbscript:msgbox')).toBe(false);
    expect(safeHref('/relative/path')).toBe(false);
  });
});

describe('разметка документа читается', () => {
  test('заголовки, списки и выноска разбираются', () => {
    const blocks = parse(
      '# Заголовок\nАбзац первый.\n\n- пункт один\n- пункт два\n\n1. шаг\n\n> примечание',
    );
    expect(blocks.map((block) => block.kind))
      .toEqual(['head', 'text', 'list', 'list', 'note']);
    expect(blocks[2]).toMatchObject({ ordered: false, items: ['пункт один', 'пункт два'] });
    expect(blocks[3]).toMatchObject({ ordered: true, items: ['шаг'] });
  });

  test('незнакомая разметка остаётся текстом, а не пропадает', () => {
    expect(parse('||таблица||')).toEqual([{ kind: 'text', text: '||таблица||' }]);
  });

  test('жирный текст выделяется, звёздочки не остаются в тексте', () => {
    const { container } = render(<Markup content={'Срок **три рабочих дня**.'} />);
    expect(container.querySelector('b')?.textContent).toBe('три рабочих дня');
    expect(container.textContent).not.toContain('**');
  });

  test('пустой документ так и называется', () => {
    render(<Markup content={'   '} />);
    expect(screen.getByText('Документ пуст.')).toBeTruthy();
  });

  test('выдержка берёт первый абзац без разметки', () => {
    expect(excerpt('# Заголовок\nСрок **три дня**.')).toBe('Срок три дня.');
    expect(inline('обычный текст')).toEqual(['обычный текст']);
  });
});

// --- правила вкладок и подписей ---------------------------------------------

describe('счётчики вкладок', () => {
  const counts = {
    total: 12, DRAFT: 4, INDEXING: 1, ACTIVE: 3, ARCHIVED: 3, ERROR: 1,
  };

  test('число на вкладке равно тому, что по ней отфильтруется', () => {
    // Инвариант: слагаемые счётчика и состояния фильтра — одно и то же.
    for (const tab of SOURCE_TABS) {
      if (tab.key === 'all') continue;
      const expected = tab.statuses
        .split(',')
        .reduce((sum, code) => sum + ((counts as Record<string, number>)[code] ?? 0), 0);
      expect(sourceTabCount(tab.key as SourceTab, counts)).toBe(expected);
    }
  });

  test('вкладки покрывают весь набор: ни один документ не выпадает', () => {
    const sum = SOURCE_TABS.filter((tab) => tab.key !== 'all')
      .reduce((total, tab) => total + sourceTabCount(tab.key as SourceTab, counts), 0);
    expect(sum).toBe(counts.total);
    expect(sourceTabCount('all', counts)).toBe(counts.total);
  });

  test('индексируемая версия не попадает в «Опубликованы»', () => {
    // Она ещё не опубликована и сотрудникам не отвечает.
    expect(sourceTabCount('published', counts)).toBe(3);
    expect(SOURCE_TABS.find((tab) => tab.key === 'published')?.statuses).toBe('ACTIVE');
  });

  test('у вопросов и ответов то же правило', () => {
    const faq = { total: 5, DRAFT: 2, ACTIVE: 2, ARCHIVED: 1 };
    for (const tab of FAQ_TABS) {
      if (tab.key === 'all') continue;
      expect(faqTabCount(tab.key, faq))
        .toBe((faq as Record<string, number>)[tab.statuses] ?? 0);
    }
  });
});

describe('подпись под списком', () => {
  const three = { items: [1, 2, 3] };

  test('«показаны все» — только когда набор действительно кончился', () => {
    expect(shownLine({ ...three, hasMore: false }, 3)).toBe('Показаны все 3');
    expect(shownLine({ ...three, hasMore: true }, 30)).toBe('Показано 3 из 30');
  });

  test('без счётчика длина массива за итог не выдаётся', () => {
    expect(shownLine({ ...three, hasMore: false }, null)).toBe('Показано 3');
  });

  test('под пустым списком подписи нет', () => {
    expect(shownLine({ items: [], hasMore: false }, 0)).toBe('');
  });
});

describe('область действия и доступные действия', () => {
  test('пустая область — это вся организация, а не прочерк', () => {
    expect(scopeTitle({ office_name: null, region_name: null })).toBe('Вся организация');
    expect(scopeTitle({ office_name: 'Тестовый офис', region_name: 'Тестовый регион' }))
      .toBe('Тестовый офис');
    expect(scopeTitle({ office_name: null, region_name: 'Тестовый регион' }))
      .toBe('Тестовый регион');
  });

  test('архивируют опубликованное, правят поверх — только черновик', () => {
    expect(canArchive({ status: 'ACTIVE' })).toBe(true);
    expect(canArchive({ status: 'DRAFT' })).toBe(false);
    expect(canEditInPlace({ status: 'DRAFT' })).toBe(true);
    expect(canEditInPlace({ status: 'ERROR' })).toBe(true);
    expect(canEditInPlace({ status: 'ACTIVE' })).toBe(false);
  });

  test('при выключенном ассистенте индексация закрыта, и причина названа', () => {
    const off = { embeddings_available: false, reason: 'ai_disabled' as const };
    expect(indexBlockedBecause({ status: 'DRAFT' }, off)).toContain('AI-ассистент выключен');
    const unset = { embeddings_available: false, reason: 'provider_not_configured' as const };
    expect(indexBlockedBecause({ status: 'DRAFT' }, unset)).toContain('провайдер');
    const on = { embeddings_available: true, reason: null };
    expect(indexBlockedBecause({ status: 'DRAFT' }, on)).toBeNull();
    expect(indexBlockedBecause({ status: 'ACTIVE' }, on)).toContain('новую');
  });

  test('запись без эмбеддинга в ответы не включают, и причина названа', () => {
    const off = { embeddings_available: false, reason: 'ai_disabled' as const };
    expect(faqActivateBlockedBecause({ indexed: false }, off))
      .toContain('AI-ассистент выключен');
    // Ассистент включён, но эмбеддинга ещё нет — это другая причина.
    const on = { embeddings_available: true, reason: null };
    expect(faqActivateBlockedBecause({ indexed: false }, on))
      .toContain('эмбеддинга');
    expect(faqActivateBlockedBecause({ indexed: true }, off)).toBeNull();
  });

  test('черновик нигде не назван опубликованным', () => {
    const off = { embeddings_available: false, reason: 'ai_disabled' as const };
    const note = readinessNote({ status: 'DRAFT' }, off);
    expect(note).toContain('Черновик сохранён');
    expect(note).not.toMatch(/опубликован/i);
    expect(readinessNote({ status: 'INDEXING' }, off)).toContain('не опубликована');
    expect(readinessNote({ status: 'ACTIVE' }, off)).toContain('участвует в ответах');
  });
});

// --- страница целиком -------------------------------------------------------

const AI_OFF = { embeddings_available: false, reason: 'ai_disabled' } as const;

const DRAFT = {
  id: 'src-1',
  title: 'Тестовый регламент А',
  source_type: 'POLICY',
  language: 'ru',
  status: 'DRAFT',
  version: 1,
  priority: 0,
  office_id: null,
  region_id: null,
  department_id: null,
  office_name: null,
  region_name: null,
  created_by: 'Тестовый администратор',
  effective_from: null,
  effective_to: null,
  parent_source_id: null,
  created_at: '2026-09-01T10:00:00Z',
  updated_at: '2026-09-01T10:00:00Z',
};

const LIVE = {
  ...DRAFT,
  id: 'src-2',
  title: 'Тестовая инструкция Б',
  source_type: 'INSTRUCTION',
  status: 'ACTIVE',
  version: 2,
  office_id: 'off-1',
  office_name: 'Тестовый офис',
  parent_source_id: 'src-3',
  updated_at: '2026-09-04T09:00:00Z',
};

const OLD = {
  ...LIVE,
  id: 'src-3',
  status: 'ARCHIVED',
  version: 1,
  parent_source_id: null,
  updated_at: '2026-08-20T09:00:00Z',
};

const TEXTS: Record<string, string> = {
  'src-1': '# Тестовый регламент А\nПервая редакция тестового текста.',
  'src-2': '# Тестовая инструкция Б\nДействующий тестовый текст, версия два.',
  'src-3': '# Тестовая инструкция Б\nСтарый тестовый текст, версия один.',
};

const FAQ = {
  id: 'faq-1',
  canonical_question: 'Тестовый вопрос о пропуске?',
  approved_answer: 'Тестовый ответ, написанный человеком.',
  language: 'ru',
  status: 'DRAFT',
  priority: 0,
  office_id: null,
  region_id: null,
  source_id: 'src-2',
  office_name: null,
  region_name: null,
  source_title: 'Тестовая инструкция Б',
  created_by: 'Тестовый администратор',
  indexed: false,
  created_at: '2026-09-02T10:00:00Z',
  updated_at: '2026-09-02T10:00:00Z',
};

const DOC_COUNTS = { total: 2, DRAFT: 1, INDEXING: 0, ACTIVE: 1, ARCHIVED: 0, ERROR: 0 };
const FAQ_COUNTS = { total: 1, DRAFT: 1, ACTIVE: 0, ARCHIVED: 0 };

const REGIONS = [{ id: 'reg-1', name: 'Тестовый регион', status: 'ACTIVE' }];
const OFFICES = [
  { id: 'off-1', name: 'Тестовый офис', region_id: 'reg-1', status: 'ACTIVE' },
  { id: 'off-2', name: 'Второй тестовый офис', region_id: null, status: 'ACTIVE' },
];

const path = (url: string) => url.split('?')[0] ?? '';

/** Поддельный сервер базы знаний. `own` перехватывает нужный тесту адрес. */
function network(
  own: (url: string, method: string) => Response | Promise<Response> | null = () => null,
  options: { docs?: unknown[]; counts?: unknown; capability?: unknown } = {},
) {
  return fakeNetwork((url, call) => {
    const mine = own(url, call.method);
    if (mine) return mine;
    const bare = path(url);

    if (bare.includes('/auth/')) {
      return json(200, {
        ...USER,
        permissions: ['knowledge.read', 'knowledge.write', 'knowledge.publish',
                      'knowledge.index'],
      });
    }
    if (bare.includes('/knowledge/sources/capability/')) {
      return json(200, options.capability ?? AI_OFF);
    }
    if (bare.includes('/knowledge/sources/counts/')) {
      return json(200, options.counts ?? DOC_COUNTS);
    }
    if (bare.includes('/knowledge/faq/counts/')) return json(200, FAQ_COUNTS);

    const versions = /\/knowledge\/sources\/([^/]+)\/versions\/$/.exec(bare);
    if (versions) return json(200, [LIVE, OLD]);

    const one = /\/knowledge\/sources\/([^/]+)\/$/.exec(bare);
    if (one && call.method === 'GET') {
      const id = one[1] as string;
      const row = [DRAFT, LIVE, OLD].find((item) => item.id === id);
      return row ? json(200, { ...row, content: TEXTS[id] ?? '' }) : json(404, {});
    }
    if (bare.endsWith('/knowledge/sources/')) {
      return json(200, {
        items: options.docs ?? [DRAFT, LIVE],
        next_cursor: null,
        has_more: false,
      });
    }

    const faqOne = /\/knowledge\/faq\/([^/]+)\/$/.exec(bare);
    if (faqOne && call.method === 'GET') return json(200, FAQ);
    if (bare.endsWith('/knowledge/faq/')) {
      return json(200, { items: [FAQ], next_cursor: null, has_more: false });
    }

    if (bare.includes('/regions/')) return json(200, { items: REGIONS });
    if (bare.includes('/offices/')) return json(200, { items: OFFICES });
    return crm(url) ?? json(200, { items: [], next_cursor: null, has_more: false });
  });
}

const opened = async () => {
  await screen.findByText('Тестовый регламент А');
};

describe('список материалов', () => {
  test('строка — это документ, а не версия', async () => {
    const calls = network();
    renderApp('/knowledge');
    await opened();

    // У «Тестовой инструкции Б» две версии, а строка одна.
    expect(screen.getAllByText('Тестовая инструкция Б')).toHaveLength(1);
    const list = calls.filter((c) => path(c.url).endsWith('/knowledge/sources/'));
    expect(list.length).toBeGreaterThan(0);
    expect(list.every((c) => !c.url.includes('all_versions'))).toBe(true);
  });

  test('счётчик вкладки приходит с сервера, а не считается по строкам', async () => {
    // На сервере девять документов, на первой странице — два.
    network(
      (url, method) =>
        method === 'GET' && path(url).endsWith('/knowledge/sources/')
          ? json(200, { items: [DRAFT, LIVE], next_cursor: 'cur-2', has_more: true })
          : null,
      { counts: { total: 9, DRAFT: 5, INDEXING: 0, ACTIVE: 3, ARCHIVED: 1, ERROR: 0 } },
    );
    renderApp('/knowledge');
    await opened();

    expect(await screen.findByText('Показано 2 из 9')).toBeTruthy();
    const docsTab = screen.getByRole('tab', { name: /Документы/ });
    expect(docsTab.textContent).toContain('9');
  });

  test('счётчики запрашиваются без фильтра состояния', async () => {
    const calls = network();
    renderApp('/knowledge');
    await opened();

    fireEvent.click(screen.getByRole('tab', { name: /Опубликованы/ }));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('status=ACTIVE'))).toBe(true),
    );
    const counts = calls.filter((c) => path(c.url).includes('/knowledge/sources/counts/'));
    expect(counts.every((c) => !c.url.includes('status='))).toBe(true);
  });

  test('вкладка «Черновики» запрашивает ровно те состояния, что считает', async () => {
    const calls = network();
    renderApp('/knowledge');
    await opened();

    fireEvent.click(screen.getByRole('tab', { name: /Черновики/ }));
    await waitFor(() => {
      const asked = calls
        .map((c) => decodeURIComponent(c.url))
        .filter((url) => url.includes('status='));
      expect(asked.some((url) => url.includes('status=DRAFT,ERROR,INDEXING'))).toBe(true);
    });
  });

  test('поиск и фильтр области уходят на сервер', async () => {
    const calls = network();
    renderApp('/knowledge');
    await opened();

    fireEvent.change(screen.getByLabelText('Поиск материала'), {
      target: { value: 'регламент' },
    });
    await waitFor(() =>
      expect(calls.some((c) => decodeURIComponent(c.url).includes('search=регламент')))
        .toBe(true),
    );

    fireEvent.change(screen.getByLabelText('Регион'), { target: { value: 'reg-1' } });
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('region_id=reg-1'))).toBe(true),
    );
  });

  test('«Показать ещё» дочитывает по курсору и добавляет строки', async () => {
    const second = {
      ...DRAFT, id: 'src-9', title: 'Тестовый документ со второй страницы',
    };
    const calls = network((url, method) => {
      if (method !== 'GET' || !path(url).endsWith('/knowledge/sources/')) return null;
      return url.includes('cursor=cur-2')
        ? json(200, { items: [second], next_cursor: null, has_more: false })
        : json(200, { items: [DRAFT, LIVE], next_cursor: 'cur-2', has_more: true });
    }, { counts: { total: 3, DRAFT: 2, INDEXING: 0, ACTIVE: 1, ARCHIVED: 0, ERROR: 0 } });
    renderApp('/knowledge');
    await opened();

    fireEvent.click(screen.getByRole('button', { name: 'Показать ещё' }));
    await screen.findByText('Тестовый документ со второй страницы');
    expect(screen.getByText('Показаны все 3')).toBeTruthy();
    expect(calls.some((c) => c.url.includes('cursor=cur-2'))).toBe(true);

    // Смена вкладки — другой набор: дочитанное не должно остаться под ним.
    fireEvent.click(screen.getByRole('tab', { name: /Опубликованы/ }));
    await waitFor(() =>
      expect(screen.queryByText('Тестовый документ со второй страницы')).toBeNull(),
    );
  });

  test('ошибка загрузки не выдаётся за пустую базу', async () => {
    network((url, method) =>
      method === 'GET' && path(url).endsWith('/knowledge/sources/')
        ? json(500, { error: { code: 'server_error', message: 'x' } })
        : null,
    );
    renderApp('/knowledge');
    expect(await screen.findByText(/это ошибка запроса, а не пустая база/i)).toBeTruthy();
  });
});

describe('карточка документа', () => {
  test('черновик не архивируется, и кнопка объясняет почему', async () => {
    network();
    renderApp('/knowledge');
    await opened();
    fireEvent.click(screen.getByText('Тестовый регламент А'));

    const card = await screen.findByLabelText('Выбранный материал');
    const archive = within(card).getByRole('button', { name: /В архив/ });
    expect(archive).toHaveProperty('disabled', true);
    expect(archive.getAttribute('title')).toMatch(/у черновика нет публикации/i);
    // Значок архива, а не корзины: удаления здесь нет.
    expect(card.innerHTML).not.toMatch(/trash|корзин/i);
  });

  test('у действующей версии правка идёт новой версией, архив доступен', async () => {
    network();
    renderApp('/knowledge');
    await opened();
    fireEvent.click(screen.getByText('Тестовая инструкция Б'));

    const card = await screen.findByLabelText('Выбранный материал');
    expect(within(card).getByRole('button', { name: /Новая версия/ })).toBeTruthy();
    expect(within(card).queryByRole('button', { name: /^Редактировать/ })).toBeNull();
    expect(within(card).getByRole('button', { name: /В архив/ }))
      .toHaveProperty('disabled', false);
    expect(within(card).getByText(/участвует в ответах сотрудникам/)).toBeTruthy();
  });

  test('при выключенном ассистенте индексация выключена и запрос не уходит', async () => {
    const calls = network();
    renderApp('/knowledge');
    await opened();

    expect(await screen.findByText('AI-ответы выключены')).toBeTruthy();
    fireEvent.click(screen.getByText('Тестовый регламент А'));
    const card = await screen.findByLabelText('Выбранный материал');
    const index = within(card).getByRole('button', { name: /Отправить на индексацию/ });
    expect(index).toHaveProperty('disabled', true);
    fireEvent.click(index);

    expect(calls.some((c) => c.url.includes('/index/'))).toBe(false);
    expect(within(card).getByText(/Индексация недоступна/)).toBeTruthy();
    expect(within(card).getByText(/Черновик сохранён/)).toBeTruthy();
  });

  test('нигде нет генерации ответа', async () => {
    network();
    renderApp('/knowledge');
    await opened();
    fireEvent.click(screen.getByRole('tab', { name: /Вопросы и ответы/ }));
    await screen.findByText('Тестовый вопрос о пропуске?');
    fireEvent.click(screen.getByText('Тестовый вопрос о пропуске?'));
    await screen.findByLabelText('Выбранный материал');

    expect(screen.queryByRole('button', { name: /генерир/i })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Добавить материал/ }));
    expect(await screen.findByRole('dialog')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /генерир/i })).toBeNull();
  });

  test('устаревший ответ не попадает под чужой заголовок', async () => {
    const gate: { open?: (value: Response) => void } = {};
    network((url, method) => {
      if (method !== 'GET') return null;
      if (path(url).endsWith('/knowledge/sources/src-1/')) {
        return new Promise<Response>((resolve) => {
          gate.open = resolve;
        });
      }
      return null;
    });
    renderApp('/knowledge');
    await opened();

    fireEvent.click(screen.getByText('Тестовый регламент А'));
    await screen.findByText('Открываем документ…');
    fireEvent.click(screen.getByText('Тестовая инструкция Б'));
    const card = await screen.findByLabelText('Выбранный материал');
    // Уровень важен: заголовок карточки — h2, а `#` в тексте документа
    // даёт h3 с тем же названием.
    await within(card).findByRole('heading', {
      name: 'Тестовая инструкция Б', level: 2,
    });

    // Медленный ответ по первой строке приходит уже после переключения.
    gate.open?.(json(200, { ...DRAFT, content: TEXTS['src-1'] }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(within(card).getByRole('heading', {
      name: 'Тестовая инструкция Б', level: 2,
    })).toBeTruthy();
    expect(within(card).queryByText(/Первая редакция тестового текста/)).toBeNull();
  });
});

describe('версии', () => {
  test('история приходит с сервера, старая версия помечена как не текущая', async () => {
    const calls = network();
    renderApp('/knowledge');
    await opened();
    fireEvent.click(screen.getByText('Тестовая инструкция Б'));
    const card = await screen.findByLabelText('Выбранный материал');

    fireEvent.click(within(card).getByRole('tab', { name: /Версии/ }));
    expect(await within(card).findByText('v2')).toBeTruthy();
    expect(within(card).getByText('v1')).toBeTruthy();
    expect(within(card).getByText('Открыта')).toBeTruthy();
    expect(calls.some((c) => c.url.includes('/src-2/versions/'))).toBe(true);

    fireEvent.click(within(card).getByRole('button', { name: 'Посмотреть' }));
    expect(await within(card).findByText(/Показана предыдущая версия 1/)).toBeTruthy();
    expect(await within(card).findByText(/Старый тестовый текст/)).toBeTruthy();
    // Текст старой версии читается отдельным запросом, а не берётся
    // из строки истории.
    expect(calls.some((c) => path(c.url).endsWith('/knowledge/sources/src-3/'))).toBe(true);
  });

  test('в истории нет ни отката, ни удаления', async () => {
    network();
    renderApp('/knowledge');
    await opened();
    fireEvent.click(screen.getByText('Тестовая инструкция Б'));
    const card = await screen.findByLabelText('Выбранный материал');
    fireEvent.click(within(card).getByRole('tab', { name: /Версии/ }));
    await within(card).findByText('v1');

    expect(within(card).queryByRole('button', { name: /Откатить|Восстановить|Удалить/ }))
      .toBeNull();
  });
});

describe('форма материала', () => {
  test('новая версия не переименовывается и наследует область', async () => {
    const calls = network((url, method) =>
      method === 'POST' && path(url).endsWith('/knowledge/sources/')
        ? json(201, { ...LIVE, id: 'src-4', status: 'DRAFT', version: 3,
                      content: 'Третья редакция.' })
        : null,
    );
    renderApp('/knowledge');
    await opened();
    fireEvent.click(screen.getByText('Тестовая инструкция Б'));
    const card = await screen.findByLabelText('Выбранный материал');
    fireEvent.click(within(card).getByRole('button', { name: /Новая версия/ }));

    const form = await screen.findByRole('dialog');
    // Название — имя документа: по нему собирается история версий.
    expect(within(form).getByLabelText('Название документа'))
      .toHaveProperty('disabled', true);
    // Область версии не выбирают заново — она наследуется.
    expect(within(form).queryByLabelText('Офис материала')).toBeNull();

    fireEvent.change(within(form).getByLabelText('Содержание документа'), {
      target: { value: 'Третья редакция тестового текста.' },
    });
    fireEvent.click(within(form).getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => {
      const post = calls.find((c) => c.method === 'POST');
      expect(post?.body).toMatchObject({
        title: 'Тестовая инструкция Б',
        parent_source_id: 'src-2',
        office_id: 'off-1',
      });
    });
  });

  test('закрытие с несохранёнными правками спрашивает подтверждение', async () => {
    const ask = vi.spyOn(window, 'confirm').mockReturnValue(false);
    network();
    renderApp('/knowledge');
    await opened();
    fireEvent.click(screen.getByRole('button', { name: /Добавить материал/ }));

    const form = await screen.findByRole('dialog');
    fireEvent.change(within(form).getByLabelText('Название документа'), {
      target: { value: 'Тестовый черновик' },
    });
    fireEvent.click(within(form).getByRole('button', { name: 'Отмена' }));

    expect(ask).toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeTruthy();

    ask.mockReturnValue(true);
    fireEvent.click(within(form).getByRole('button', { name: 'Отмена' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    ask.mockRestore();
  });

  test('форма без правок закрывается молча', async () => {
    const ask = vi.spyOn(window, 'confirm').mockReturnValue(true);
    network();
    renderApp('/knowledge');
    await opened();
    fireEvent.click(screen.getByRole('button', { name: /Добавить материал/ }));

    const form = await screen.findByRole('dialog');
    fireEvent.click(within(form).getByRole('button', { name: 'Отмена' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(ask).not.toHaveBeenCalled();
    ask.mockRestore();
  });

  test('отказ сервера не стирает набранный текст и называет поле', async () => {
    network((url, method) =>
      method === 'POST' && path(url).endsWith('/knowledge/sources/')
        ? json(400, {
            error: {
              code: 'validation_error',
              message: 'Проверьте поля',
              details: { title: ['Документ с таким названием уже есть'] },
            },
          })
        : null,
    );
    renderApp('/knowledge');
    await opened();
    fireEvent.click(screen.getByRole('button', { name: /Добавить материал/ }));

    const form = await screen.findByRole('dialog');
    const title = within(form).getByLabelText('Название документа');
    const body = within(form).getByLabelText('Содержание документа');
    fireEvent.change(title, { target: { value: 'Тестовый регламент А' } });
    fireEvent.change(body, { target: { value: 'Три абзаца тестового текста.' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Сохранить' }));

    expect(await within(form).findByText('Документ с таким названием уже есть')).toBeTruthy();
    expect(title).toHaveProperty('value', 'Тестовый регламент А');
    expect(body).toHaveProperty('value', 'Три абзаца тестового текста.');
    expect(within(form).getByRole('button', { name: 'Сохранить' }))
      .toHaveProperty('disabled', false);
  });

  test('двойное нажатие не заводит два материала', async () => {
    const calls = network((url, method) =>
      method === 'POST' && path(url).endsWith('/knowledge/sources/')
        ? new Promise<Response>(() => {})
        : null,
    );
    renderApp('/knowledge');
    await opened();
    fireEvent.click(screen.getByRole('button', { name: /Добавить материал/ }));

    const form = await screen.findByRole('dialog');
    fireEvent.change(within(form).getByLabelText('Название документа'), {
      target: { value: 'Тестовый регламент В' },
    });
    fireEvent.change(within(form).getByLabelText('Содержание документа'), {
      target: { value: 'Текст тестового регламента.' },
    });
    const save = within(form).getByRole('button', { name: /Сохран/ });
    fireEvent.click(save);
    fireEvent.click(save);
    fireEvent.click(save);

    await waitFor(() => expect(save.textContent).toContain('Сохраняем'));
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
  });

  test('после сохранения список и счётчики перечитываются', async () => {
    const calls = network((url, method) =>
      method === 'POST' && path(url).endsWith('/knowledge/sources/')
        ? json(201, { ...DRAFT, id: 'src-7', title: 'Тестовый регламент Г',
                      content: 'Текст.' })
        : null,
    );
    renderApp('/knowledge');
    await opened();
    const before = calls.filter((c) =>
      path(c.url).includes('/knowledge/sources/counts/')).length;

    fireEvent.click(screen.getByRole('button', { name: /Добавить материал/ }));
    const form = await screen.findByRole('dialog');
    fireEvent.change(within(form).getByLabelText('Название документа'), {
      target: { value: 'Тестовый регламент Г' },
    });
    fireEvent.change(within(form).getByLabelText('Содержание документа'), {
      target: { value: 'Текст тестового регламента.' },
    });
    fireEvent.click(within(form).getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() =>
      expect(calls.filter((c) =>
        path(c.url).includes('/knowledge/sources/counts/')).length).toBeGreaterThan(before),
    );
  });

  test('регион меняет список офисов и сбрасывает несовместимый', async () => {
    network();
    renderApp('/knowledge');
    await opened();
    fireEvent.click(screen.getByRole('button', { name: /Добавить материал/ }));

    const form = await screen.findByRole('dialog');
    const office = within(form).getByLabelText('Офис материала') as HTMLSelectElement;
    await waitFor(() => expect(office.options.length).toBeGreaterThan(2));
    fireEvent.change(office, { target: { value: 'off-2' } });
    expect(office.value).toBe('off-2');

    // «Второй тестовый офис» не в этом регионе — выбор снимается.
    fireEvent.change(within(form).getByLabelText('Регион материала'), {
      target: { value: 'reg-1' },
    });
    await waitFor(() => expect(office.value).toBe(''));
  });
});

describe('вопросы и ответы', () => {
  test('включение в ответы выключено, пока эмбеддинга нет', async () => {
    const calls = network();
    renderApp('/knowledge?area=faq');
    await screen.findByText('Тестовый вопрос о пропуске?');
    fireEvent.click(screen.getByText('Тестовый вопрос о пропуске?'));

    const card = await screen.findByLabelText('Выбранный материал');
    const activate = within(card).getByRole('button', { name: /Включить в ответы/ });
    expect(activate).toHaveProperty('disabled', true);
    fireEvent.click(activate);
    expect(calls.some((c) => c.url.includes('/activate/'))).toBe(false);
    // Причина названа и на кнопке, и в подписи о готовности.
    expect(activate.getAttribute('title')).toMatch(/AI-ассистент выключен/);
    expect(within(card).getByText(/Включить в автоматические ответы нельзя/))
      .toBeTruthy();

    // Архивация FAQ — обычная смена состояния, её сервер принимает
    // и у черновика: это не снятие публикации, как у документа.
    expect(within(card).getByRole('button', { name: /В архив/ }))
      .toHaveProperty('disabled', false);
  });

  test('вкладка показывает записи и открывает карточку', async () => {
    const calls = network();
    renderApp('/knowledge');
    await opened();

    fireEvent.click(screen.getByRole('tab', { name: /Вопросы и ответы/ }));
    await screen.findByText('Тестовый вопрос о пропуске?');
    expect(calls.some((c) => path(c.url).endsWith('/knowledge/faq/'))).toBe(true);

    fireEvent.click(screen.getByText('Тестовый вопрос о пропуске?'));
    const card = await screen.findByLabelText('Выбранный материал');
    expect(within(card).getByText('Тестовый ответ, написанный человеком.')).toBeTruthy();
    expect(within(card).getByText('Тестовая инструкция Б')).toBeTruthy();
    // Про выключенный ассистент карточка говорит дважды — в подписи
    // о готовности и в объяснении недоступной кнопки; проверяем первую.
    expect(within(card).getByText(/Черновик сохранён/)).toBeTruthy();
  });

  test('ответ правится и уходит на сервер как есть', async () => {
    const calls = network((url, method) =>
      method === 'PATCH' && path(url).endsWith('/knowledge/faq/faq-1/')
        ? json(200, { ...FAQ, approved_answer: 'Исправленный тестовый ответ.' })
        : null,
    );
    renderApp('/knowledge?area=faq');
    await screen.findByText('Тестовый вопрос о пропуске?');
    fireEvent.click(screen.getByText('Тестовый вопрос о пропуске?'));
    const card = await screen.findByLabelText('Выбранный материал');
    fireEvent.click(within(card).getByRole('button', { name: /Редактировать/ }));

    const form = await screen.findByRole('dialog');
    fireEvent.change(within(form).getByLabelText('Ответ HR'), {
      target: { value: 'Исправленный тестовый ответ.' },
    });
    fireEvent.click(within(form).getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => {
      const patch = calls.find((c) => c.method === 'PATCH');
      expect(patch?.body).toMatchObject({
        approved_answer: 'Исправленный тестовый ответ.',
      });
    });
  });
});
