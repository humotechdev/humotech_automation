/**
 * Прохождение опроса: один вопрос на экране.
 *
 * Все вопросы сразу — это стена текста, которую пролистывают до конца и
 * закрывают. По одному видно, сколько осталось, и каждый вопрос читают.
 * Отсюда же «Назад»: человек, передумавший на четвёртом вопросе, должен
 * иметь возможность вернуться, а не начинать заново.
 *
 * Опрос ИМЕННОЙ, и об этом сказано на первом экране, а не мелким шрифтом
 * в конце. Человек, отвечающий про руководителя, должен знать это до
 * того, как ответит, — иначе приложение обмануло его молчанием.
 *
 * Ответы уходят одним запросом в конце. Отправлять по одному значило бы
 * оставлять в базе половину мнения, которую HR прочтёт как целое.
 */

import { useCallback, useEffect, useState } from 'react';

import {
  api,
  type Survey as SurveyData,
  type SurveyAnswer,
  type SurveyQuestion,
} from '../api';
import { useSection } from '../sections';
import { backButton } from '../telegram';
import {
  Card,
  PageContainer,
  PrimaryButton,
  ProgressBar,
  SecondaryButton,
} from '../ui/primitives';
import { ErrorState, LoadingScreen } from '../ui/states';

/** Что человек уже выбрал: по вопросу. */
type Draft = Record<string, SurveyAnswer>;

const SCALE = [1, 2, 3, 4, 5];

/** Подписи к краям шкалы: голые цифры не говорят, где хорошо. */
const SCALE_ENDS: [string, string] = ['совсем нет', 'полностью да'];

export function Survey({
  recipientId,
  onDone,
  onExit,
}: {
  recipientId: string;
  /** Опрос отправлен: приложение может вернуться в кабинет. */
  onDone: () => void;
  /** Человек ушёл, не закончив. Ответы остаются несохранёнными. */
  onExit: () => void;
}) {
  const survey = useSection<SurveyData>(() => api.survey(recipientId), [
    recipientId,
  ]);

  if (survey.loading) {
    return (
      <PageContainer className="survey">
        <LoadingScreen label="Открываем опрос" cards={2} />
      </PageContainer>
    );
  }

  if (!survey.data) {
    return (
      <PageContainer className="survey">
        <ErrorState
          title="Опрос не открылся"
          message={survey.error ?? 'Попробуйте ещё раз.'}
          onRetry={survey.reload}
        />
        <SecondaryButton onClick={onExit} wide>
          В кабинет
        </SecondaryButton>
      </PageContainer>
    );
  }

  return (
    <Running
      survey={survey.data}
      onDone={onDone}
      onExit={onExit}
    />
  );
}

function Running({
  survey,
  onDone,
  onExit,
}: {
  survey: SurveyData;
  onDone: () => void;
  onExit: () => void;
}) {
  // Шаг 0 — вступление: что за опрос и что он именной. Дальше вопросы,
  // последний экран — отправка.
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft>(() => {
    const was: Draft = {};
    for (const answer of survey.answers) was[answer.question_id] = answer;
    return was;
  });
  const [sending, setSending] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const questions = survey.questions;
  const total = questions.length;
  // Шаги: вступление, вопросы, итог.
  const last = total + 1;

  const back = useCallback(() => {
    setRefusal(null);
    setStep((was) => Math.max(0, was - 1));
  }, []);

  // Родная кнопка Telegram делает то же, что «Назад» на экране: две
  // разные «назад» на одном экране закрывают приложение вместо возврата.
  useEffect(() => {
    if (step === 0 || sent) return backButton(null);
    return backButton(back);
  }, [step, sent, back]);

  if (sent) {
    return (
      <PageContainer className="survey">
        <Card className="survey-done">
          <p className="survey-done__mark" aria-hidden="true">✓</p>
          <h1 className="survey-done__title">Спасибо, опрос завершён</h1>
          <p className="state-text">
            Ответы ушли в отдел кадров. Бот пришлёт подтверждение в чат.
          </p>
          <PrimaryButton onClick={onDone} wide>
            В кабинет
          </PrimaryButton>
        </Card>
      </PageContainer>
    );
  }

  if (step === 0) {
    return (
      <PageContainer className="survey">
        <Card className="survey-intro">
          <h1 className="survey-intro__title">{survey.title}</h1>
          {survey.description && (
            <p className="state-text">{survey.description}</p>
          )}
          <p className="survey-intro__facts">
            {total} {plural(total, ['вопрос', 'вопроса', 'вопросов'])} · 2–3 минуты
          </p>
          {/* Сказано до первого ответа, а не после последнего. */}
          <p className="survey-intro__named">
            Опрос не анонимный: отдел кадров увидит ваше имя рядом с
            ответами.
          </p>
          <PrimaryButton onClick={() => setStep(1)} wide>
            Начать
          </PrimaryButton>
          <SecondaryButton onClick={onExit} wide>
            Позже
          </SecondaryButton>
        </Card>
      </PageContainer>
    );
  }

  if (step <= total) {
    const question = questions[step - 1] as SurveyQuestion;
    const answer = draft[question.id];
    const answered = hasAnswer(answer);
    return (
      <PageContainer className="survey">
        <header className="survey-head">
          <ProgressBar
            value={step}
            max={total}
            label={`Вопрос ${step} из ${total}`}
          />
          <p className="survey-head__count">
            {step} из {total}
          </p>
        </header>

        <Card className="survey-card">
          <h1 className="survey-question">
            {question.text}
            {!question.is_required && (
              <span className="survey-optional">необязательный</span>
            )}
          </h1>

          <QuestionInput
            question={question}
            answer={answer}
            onChange={(next) =>
              setDraft((was) => ({ ...was, [question.id]: next }))
            }
          />
        </Card>

        <div className="survey-tools">
          <SecondaryButton onClick={back}>Назад</SecondaryButton>
          <PrimaryButton
            onClick={() => setStep(step + 1)}
            // Обязательный вопрос не пропускается: иначе человек дойдёт
            // до конца и получит отказ сервера на последнем шаге.
            disabled={question.is_required && !answered}
          >
            {step === total ? 'К отправке' : 'Далее'}
          </PrimaryButton>
        </div>
        {question.is_required && !answered && (
          <p className="survey-hint">Ответьте, чтобы идти дальше</p>
        )}
      </PageContainer>
    );
  }

  // Итоговый экран: что ответили и одна кнопка отправки.
  const missing = questions.filter(
    (one) => one.is_required && !hasAnswer(draft[one.id]),
  );

  const send = async () => {
    if (sending) return;
    setSending(true);
    setRefusal(null);
    const payload = questions
      .map((one) => draft[one.id])
      .filter((one): one is SurveyAnswer => hasAnswer(one));
    const answer = await api.submitSurvey(survey.id, payload);
    setSending(false);
    if (answer.ok) {
      setSent(true);
      return;
    }
    setRefusal(answer.message);
  };

  return (
    <PageContainer className="survey">
      <header className="survey-head">
        <ProgressBar value={last} max={last} label="Опрос пройден" />
        <p className="survey-head__count">{total} из {total}</p>
      </header>

      <Card className="survey-card">
        <h1 className="survey-question">Проверьте ответы</h1>
        <ul className="survey-review">
          {questions.map((one, index) => (
            <li key={one.id} className="survey-review__row">
              <button
                type="button"
                className="survey-review__hit"
                onClick={() => setStep(index + 1)}
              >
                <span className="survey-review__q">{one.text}</span>
                <span className="survey-review__a">
                  {describe(draft[one.id]) ?? 'без ответа'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </Card>

      {missing.length > 0 && (
        <p className="survey-hint survey-hint--bad" role="alert">
          Не отвечены обязательные вопросы: {missing.length}
        </p>
      )}
      {refusal && (
        <p className="survey-hint survey-hint--bad" role="alert">
          {refusal}
        </p>
      )}

      <div className="survey-tools">
        <SecondaryButton onClick={back}>Назад</SecondaryButton>
        <PrimaryButton
          onClick={() => void send()}
          disabled={sending || missing.length > 0}
        >
          {sending ? 'Отправляем…' : 'Отправить ответы'}
        </PrimaryButton>
      </div>
    </PageContainer>
  );
}

// --- поля ответов -----------------------------------------------------------

function QuestionInput({
  question,
  answer,
  onChange,
}: {
  question: SurveyQuestion;
  answer: SurveyAnswer | undefined;
  onChange: (answer: SurveyAnswer) => void;
}) {
  if (question.kind === 'SCALE') {
    return (
      <div className="survey-scale">
        <div className="survey-scale__row" role="radiogroup"
             aria-label={question.text}>
          {SCALE.map((mark) => (
            <button
              key={mark}
              type="button"
              role="radio"
              aria-checked={answer?.number === mark}
              className={
                answer?.number === mark
                  ? 'survey-mark survey-mark--on'
                  : 'survey-mark'
              }
              onClick={() =>
                onChange({ question_id: question.id, number: mark })
              }
            >
              {mark}
            </button>
          ))}
        </div>
        {/* Голые цифры не говорят, где хорошо, а где плохо. */}
        <div className="survey-scale__ends">
          <span>{SCALE_ENDS[0]}</span>
          <span>{SCALE_ENDS[1]}</span>
        </div>
      </div>
    );
  }

  if (question.kind === 'TEXT') {
    return (
      <textarea
        className="survey-text"
        rows={5}
        maxLength={2000}
        value={answer?.text ?? ''}
        placeholder="Ваш ответ"
        aria-label={question.text}
        onChange={(event) =>
          onChange({ question_id: question.id, text: event.target.value })
        }
      />
    );
  }

  const chosen = answer?.options ?? [];
  const many = question.kind === 'MULTI';
  return (
    <ul className="survey-options"
        role={many ? 'group' : 'radiogroup'}
        aria-label={question.text}>
      {(question.options ?? []).map((option) => {
        const on = chosen.includes(option);
        return (
          <li key={option}>
            <button
              type="button"
              role={many ? 'checkbox' : 'radio'}
              aria-checked={on}
              className={on ? 'survey-option survey-option--on' : 'survey-option'}
              onClick={() =>
                onChange({
                  question_id: question.id,
                  options: many
                    ? on
                      ? chosen.filter((one) => one !== option)
                      : [...chosen, option]
                    : [option],
                })
              }
            >
              <span className="survey-option__mark" aria-hidden="true">
                {on ? '✓' : ''}
              </span>
              <span>{option}</span>
            </button>
          </li>
        );
      })}
      {many && (
        <li className="survey-options__note">Можно выбрать несколько</li>
      )}
    </ul>
  );
}

// --- мелочи -----------------------------------------------------------------

/** Есть ли в ответе хоть что-нибудь. Пробелы ответом не считаются. */
export function hasAnswer(answer: SurveyAnswer | undefined): boolean {
  if (!answer) return false;
  if (typeof answer.number === 'number') return true;
  if (answer.options && answer.options.length > 0) return true;
  return Boolean(answer.text && answer.text.trim());
}

/** Что показать на итоговом экране. */
export function describe(answer: SurveyAnswer | undefined): string | null {
  if (!hasAnswer(answer) || !answer) return null;
  if (typeof answer.number === 'number') return `${answer.number} из 5`;
  if (answer.options && answer.options.length) return answer.options.join(', ');
  return (answer.text ?? '').trim();
}

/** «1 вопрос», «2 вопроса», «5 вопросов». */
export function plural(value: number, forms: [string, string, string]): string {
  const tail = value % 100;
  if (tail >= 11 && tail <= 14) return forms[2];
  switch (value % 10) {
    case 1:
      return forms[0];
    case 2:
    case 3:
    case 4:
      return forms[1];
    default:
      return forms[2];
  }
}
