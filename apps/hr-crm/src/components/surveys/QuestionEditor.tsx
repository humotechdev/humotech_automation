/**
 * Составление вопросов шаблона.
 *
 * Тип вопроса решает, что человек увидит в Mini App, и менять его после
 * ответов нельзя — поэтому он выбирается здесь, а не выводится из
 * заполненных полей. Варианты ответа появляются только у тех типов, у
 * которых они бывают: пустое поле вариантов у шкалы означало бы, что
 * кто-то собирался их туда положить.
 *
 * Порядок вопросов важен: его видит сотрудник, и «сначала про команду,
 * потом про руководителя» — это другой опрос, чем наоборот.
 */

import { AppIcon } from '../AppIcon';
import { AppSelectField } from '../AppSelect';

export type QuestionDraft = {
  text: string;
  kind: 'SINGLE' | 'MULTI' | 'SCALE' | 'TEXT';
  is_required: boolean;
  options: string[];
};

const KINDS: Array<[QuestionDraft['kind'], string, string]> = [
  ['SINGLE', 'Один вариант', 'Человек выбирает ровно один ответ'],
  ['MULTI', 'Несколько вариантов', 'Можно отметить сразу несколько'],
  ['SCALE', 'Шкала 1–5', 'Оценка от 1 до 5'],
  ['TEXT', 'Свободный ответ', 'Человек пишет своими словами'],
];

/** Сколько вопросов допускается. Столько же проверяет сервер. */
export const MAX_QUESTIONS = 20;

export function blankQuestion(): QuestionDraft {
  return { text: '', kind: 'SINGLE', is_required: true, options: ['', ''] };
}

export function QuestionEditor({ questions, error, onChange }: {
  questions: QuestionDraft[];
  error?: string | undefined;
  onChange: (questions: QuestionDraft[]) => void;
}) {
  const setAt = (index: number, next: Partial<QuestionDraft>) =>
    onChange(
      questions.map((one, at) => (at === index ? { ...one, ...next } : one)),
    );

  const move = (index: number, by: number) => {
    const to = index + by;
    if (to < 0 || to >= questions.length) return;
    const copy = [...questions];
    const [taken] = copy.splice(index, 1);
    copy.splice(to, 0, taken as QuestionDraft);
    onChange(copy);
  };

  return (
    <div className="sv-questions">
      <div className="sv-questions__head">
        <span className="adm-field__label">Вопросы</span>
        <span className="sv-questions__count">
          {questions.length} из {MAX_QUESTIONS}
        </span>
      </div>

      {questions.map((question, index) => {
        const withOptions =
          question.kind === 'SINGLE' || question.kind === 'MULTI';
        return (
          <div key={index} className="sv-question">
            <div className="sv-question__top">
              <span className="sv-question__no">{index + 1}</span>
              <div className="sv-question__order">
                <button type="button" className="tool tool--ghost"
                        aria-label={`Поднять вопрос ${index + 1}`}
                        disabled={index === 0}
                        onClick={() => move(index, -1)}>
                  <AppIcon name="chevron" size={16} />
                </button>
                <button type="button" className="tool tool--ghost"
                        aria-label={`Опустить вопрос ${index + 1}`}
                        disabled={index === questions.length - 1}
                        onClick={() => move(index, 1)}>
                  <AppIcon name="chevron" size={16} />
                </button>
                <button type="button" className="tool tool--ghost"
                        aria-label={`Удалить вопрос ${index + 1}`}
                        disabled={questions.length === 1}
                        onClick={() =>
                          onChange(questions.filter((_, at) => at !== index))}>
                  <AppIcon name="close" size={16} />
                </button>
              </div>
            </div>

            <textarea className="input input--area" rows={2} maxLength={500}
                      value={question.text}
                      aria-label={`Текст вопроса ${index + 1}`}
                      placeholder="Например: насколько вам комфортно в команде?"
                      onChange={(event) =>
                        setAt(index, { text: event.target.value })} />

            <div className="sv-question__row">
              <AppSelectField label={`Тип вопроса ${index + 1}`}
                              value={question.kind}
                              onChange={(value) =>
                                setAt(index, {
                                  kind: value as QuestionDraft['kind'],
                                  // Варианты бывают только у выбора.
                                  // Оставить их у шкалы значит хранить
                                  // то, чего человек никогда не увидит.
                                  options:
                                    value === 'SINGLE' || value === 'MULTI'
                                      ? (question.options.length
                                        ? question.options
                                        : ['', ''])
                                      : [],
                                })}>
                {KINDS.map(([key, title]) => (
                  <option key={key} value={key}>{title}</option>
                ))}
              </AppSelectField>

              <label className="adm-check sv-question__req">
                <input type="checkbox" checked={question.is_required}
                       onChange={(event) =>
                         setAt(index, { is_required: event.target.checked })} />
                <span>Обязательный</span>
              </label>
            </div>

            {withOptions && (
              <ul className="sv-options">
                {question.options.map((option, at) => (
                  <li key={at} className="sv-option">
                    <input className="input" value={option} maxLength={200}
                           aria-label={`Вариант ${at + 1} вопроса ${index + 1}`}
                           placeholder={`Вариант ${at + 1}`}
                           onChange={(event) =>
                             setAt(index, {
                               options: question.options.map((one, two) =>
                                 two === at ? event.target.value : one),
                             })} />
                    <button type="button" className="tool tool--ghost"
                            aria-label={`Удалить вариант ${at + 1} вопроса ${index + 1}`}
                            disabled={question.options.length <= 2}
                            onClick={() =>
                              setAt(index, {
                                options: question.options.filter(
                                  (_, two) => two !== at),
                              })}>
                      <AppIcon name="close" size={16} />
                    </button>
                  </li>
                ))}
                <li>
                  <button type="button" className="btn btn--small"
                          onClick={() =>
                            setAt(index, { options: [...question.options, ''] })}>
                    <AppIcon name="plus" size={16} /> Вариант
                  </button>
                </li>
              </ul>
            )}
          </div>
        );
      })}

      {error && <span className="adm-field__error" role="alert">{error}</span>}

      <button type="button" className="btn btn--small"
              disabled={questions.length >= MAX_QUESTIONS}
              onClick={() => onChange([...questions, blankQuestion()])}>
        <AppIcon name="plus" size={16} /> Добавить вопрос
      </button>
      {questions.length >= MAX_QUESTIONS && (
        <span className="adm-field__hint">
          Больше {MAX_QUESTIONS} вопросов — это уже не короткий опрос, а
          анкета, которую бросают на середине.
        </span>
      )}
    </div>
  );
}
