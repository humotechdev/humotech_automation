/**
 * Текст документа на экране: заголовки, абзацы, списки, ссылки.
 *
 * Backend хранит содержимое обычным текстовым полем и никак его
 * не размечает — разметка живёт только здесь, на чтении.
 *
 * Разбор возвращает ЭЛЕМЕНТЫ REACT, а не строку HTML. Это главное
 * решение модуля: `dangerouslySetInnerHTML` на содержимом, которое
 * набирает человек, — это исполнение чужого скрипта на странице
 * кадровика, и никакая «очистка» не делает его безопасным настолько,
 * чтобы стоило рисковать. React экранирует текст сам, и вставить тег
 * через него нельзя в принципе.
 *
 * Поддерживается ровно то, что нужно регламенту, и ничего сверх:
 *
 *     # Заголовок          -> h3
 *     ## Подзаголовок      -> h4
 *     - пункт              -> маркированный список
 *     1. пункт             -> нумерованный список
 *     > примечание         -> выноска
 *     **жирный**           -> выделение
 *     [текст](адрес)       -> ссылка, только http(s)
 *
 * Всё остальное остаётся текстом как есть. Незнакомая разметка должна
 * выглядеть как незнакомая разметка, а не пропадать.
 */

import type { ReactNode } from 'react';

type Block =
  | { kind: 'head'; level: 2 | 3; text: string }
  | { kind: 'text'; text: string }
  | { kind: 'note'; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] };

/** Разбор текста на блоки. Отдельно от разметки — чтобы проверять. */
export function parse(content: string): Block[] {
  const blocks: Block[] = [];
  const lines = content.replace(/\r\n/g, '\n').split('\n');
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const closeParagraph = () => {
    if (paragraph.length) {
      blocks.push({ kind: 'text', text: paragraph.join(' ') });
      paragraph = [];
    }
  };
  const closeList = () => {
    if (list) {
      blocks.push({ kind: 'list', ordered: list.ordered, items: list.items });
      list = null;
    }
  };
  const close = () => {
    closeParagraph();
    closeList();
  };

  for (const raw of lines) {
    const line = raw.trim();

    if (!line) {
      close();
      continue;
    }

    const head = /^(#{1,2})\s+(.*)$/.exec(line);
    if (head) {
      close();
      blocks.push({
        kind: 'head',
        level: head[1]?.length === 1 ? 2 : 3,
        text: head[2] ?? '',
      });
      continue;
    }

    const note = /^>\s?(.*)$/.exec(line);
    if (note) {
      close();
      blocks.push({ kind: 'note', text: note[1] ?? '' });
      continue;
    }

    const bullet = /^[-*•]\s+(.*)$/.exec(line);
    const numbered = /^(\d+)[.)]\s+(.*)$/.exec(line);
    if (bullet || numbered) {
      closeParagraph();
      const ordered = Boolean(numbered);
      const text = (bullet ? bullet[1] : numbered?.[2]) ?? '';
      if (!list || list.ordered !== ordered) {
        closeList();
        list = { ordered, items: [] };
      }
      list.items.push(text);
      continue;
    }

    closeList();
    paragraph.push(line);
  }
  close();
  return blocks;
}

/** Готовый к показу документ. */
export function Markup({ content }: { content: string }) {
  const blocks = parse(content);
  if (blocks.length === 0) {
    return <p className="doc__empty">Документ пуст.</p>;
  }
  return (
    <div className="doc">
      {blocks.map((block, index) => (
        <Piece key={index} block={block} />
      ))}
    </div>
  );
}

function Piece({ block }: { block: Block }) {
  if (block.kind === 'head') {
    return block.level === 2 ? (
      <h3 className="doc__h1">{inline(block.text)}</h3>
    ) : (
      <h4 className="doc__h2">{inline(block.text)}</h4>
    );
  }
  if (block.kind === 'note') {
    return <p className="doc__note">{inline(block.text)}</p>;
  }
  if (block.kind === 'list') {
    const items = block.items.map((item, index) => (
      <li key={index}>{inline(item)}</li>
    ));
    return block.ordered ? (
      <ol className="doc__ol">{items}</ol>
    ) : (
      <ul className="doc__ul">{items}</ul>
    );
  }
  return <p className="doc__p">{inline(block.text)}</p>;
}

/**
 * Жирный текст и ссылки внутри строки.
 *
 * Адрес ссылки пропускается только с `http` и `https`. `javascript:` и
 * `data:` в href — это тот же исполняемый скрипт, только записанный
 * иначе; такая ссылка остаётся обычным текстом.
 */
export function inline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = /\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g;
  let last = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    if (match[1] !== undefined) {
      parts.push(<b key={parts.length}>{match[1]}</b>);
    } else {
      const label = match[2] ?? '';
      const href = match[3] ?? '';
      parts.push(
        safeHref(href) ? (
          <a key={parts.length} href={href} target="_blank" rel="noopener noreferrer">
            {label}
          </a>
        ) : (
          // Адрес не пропущен — показываем как было, ничего не пряча.
          `[${label}](${href})`
        ),
      );
    }
    last = pattern.lastIndex;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

export function safeHref(href: string): boolean {
  const value = href.trim().toLowerCase();
  return value.startsWith('http://') || value.startsWith('https://');
}

/** Короткая выдержка для списка: первая строка без разметки. */
export function excerpt(content: string, limit = 120): string {
  const first = parse(content).find((block) => block.kind !== 'head');
  const text =
    first === undefined
      ? ''
      : first.kind === 'list'
        ? (first.items[0] ?? '')
        : first.text;
  const plain = text.replace(/\*\*/g, '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');
  return plain.length > limit ? `${plain.slice(0, limit).trimEnd()}…` : plain;
}
