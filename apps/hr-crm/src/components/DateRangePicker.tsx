import { DatePicker } from './DatePicker';

/** Pair of shared date fields with range constraints and continuous range highlighting. */
export function AppDateRangePicker({ from, to, onFromChange, onToChange, label = 'Период', now, className = '', max }: {
  from: string; to: string; onFromChange: (value: string) => void; onToChange: (value: string) => void;
  label?: string; now: string; className?: string; max?: string;
}) {
  const first = from && to ? from : undefined;
  const last = from && to ? to : undefined;
  return <div className={`app-date-range ${className}`} role="group" aria-label={label}>
    <DatePicker label={`${label}: начало`} value={from} onChange={onFromChange} now={now} {...(to || max ? { max: to || max } : {})} {...(first ? { rangeStart: first } : {})} {...(last ? { rangeEnd: last } : {})} allowEmpty />
    <span className="app-date-range__dash" aria-hidden="true">—</span>
    <DatePicker label={`${label}: конец`} value={to} onChange={onToChange} now={now} {...(from ? { min: from } : {})} {...(max ? { max } : {})} {...(first ? { rangeStart: first } : {})} {...(last ? { rangeEnd: last } : {})} allowEmpty />
  </div>;
}

export { AppDateRangePicker as DateRangePicker };
